"""Visual 'hosting': new quads are written INTO existing geometry objects (the file format cannot grow, so a new mesh must live in
the vertex/index buffers of an object that already exists). Hosting therefore SACRIFICES the host objects' original meshes.
The tool never does this silently: choose_hosts() returns an explicit, inspectable list.

A host must belong to a section that is loaded near the player (its placement is only drawn when its section is active).
Each quad needs 4 vertices + 12 list indices (4 triangles, both windings); Obj.capacity says how many an object can hold.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass
import numpy as np
from . import world, bun
from .world import fill

@dataclass
class Placement:
    sid: int; node: object; off: int; aabb: np.ndarray; type_name: str

def index_placements(tree) -> list[Placement]:
    out = []
    for n in tree:
        if n.magic != 0x80034100: continue
        k = {c.magic: c for c in n.kids}; sid, = struct.unpack_from("<I", k[0x34101].data, 0xC)
        q, p = k[0x34102].data, k[0x34103].data
        names = [q[i:i + 32].split(b"\0")[0].decode("latin1") for i in range(len(q) % 68, len(q) - 67, 68)]
        for j in range(len(p) % 64, len(p) - 63, 64):
            ti, = struct.unpack_from("<H", p, j + 0x18)
            out.append(Placement(sid, k[0x34103], j, np.array(struct.unpack_from("<6f", p, j)), names[ti] if ti < len(names) else "?"))
    return out

def match_placements(objs, places):
    """object -> placement by AABB (<3 m), one placement per object"""
    PA = np.array([p.aabb for p in places]); used = set(); out = {}
    for o in objs:
        lo, hi = o.bounds()
        if lo is None or o.capacity < 1: continue
        d = np.abs(PA - np.concatenate([lo, hi])).max(1); j = int(np.argmin(d))
        if d[j] < 3 and j not in used: used.add(j); out[id(o)] = places[j]
    return out

@dataclass
class Host:
    obj: world.Obj; place: Placement; cap: int
    @property
    def name(self): return self.obj.name

def choose_hosts(objs, places, need, sections, avoid=None, name_prefix="TRN_", prefer_far_from=None):
    """Pick sacrificial objects: placed in `sections`, not within r of any (x, y, r) in `avoid`, farthest from `prefer_far_from` first."""
    pm = match_placements(objs, places); cands = []
    for o in objs:
        pl = pm.get(id(o))
        if pl is None or pl.sid not in sections or not o.name.upper().startswith(name_prefix): continue
        lo, hi = o.bounds(); c = (lo + hi)[:2] / 2
        if avoid and any(np.hypot(*(c - np.array(a[:2]))) < a[2] + float(np.hypot(*(hi - lo)[:2])) / 2 for a in avoid): continue
        dist = float(np.hypot(*(c - np.array(prefer_far_from)))) if prefer_far_from is not None else 0.0
        cands.append((dist, Host(o, pl, o.capacity)))
    cands.sort(key=lambda t: -t[0]); hosts = []; total = 0
    for _, h in cands:
        if total >= need: break
        hosts.append(h); total += h.cap
    return hosts, total

@dataclass
class Prim:
    """one primitive to host: vertices (x, y, z, u, v) with ONE colour, local triangle-list indices, optional texture key"""
    verts: list; idx: list; color: int = 0xFFB0B0B0; key: int = 0

def quad_prim(corners, z, color=0xFFB0B0B0, key=0, uv=None):
    zz = np.broadcast_to(np.asarray(z, float), (4,))
    if uv is None: uv = [(float(x) / 20, float(y) / 20) for x, y in corners]
    verts = [(float(x), float(y), float(zv), float(u), float(v)) for (x, y), zv, (u, v) in zip(corners, zz, uv)]
    return Prim(verts, [0, 1, 2, 0, 2, 3, 0, 2, 1, 0, 3, 2], color, key)                 # 4 triangles = both windings (the engine's culling mode is unknown)

def quad_prim1(corners, z, color=0xFFB0B0B0, key=0, uv=None):
    """SINGLE-sided ground quad (6 indices, wound CCW seen from above like 92% of the stock ground triangles) -> twice the capacity of quad_prim"""
    p = quad_prim(corners, z, color, key, uv); v = p.verts
    nz = (v[1][0] - v[0][0]) * (v[2][1] - v[0][1]) - (v[1][1] - v[0][1]) * (v[2][0] - v[0][0])
    p.idx = [0, 1, 2, 0, 2, 3] if nz >= 0 else [0, 2, 1, 0, 3, 2]; return p

def ribbon_prims(qs, color=0xFFB0B0B0, key=0, chunk=7):
    """consecutive quads [(corners [L0,R0,R1,L1], z, uv)] sharing their edge vertices: 2 verts + 6 indices per quad (vs 4 verts + 12 indices)"""
    out = []
    for s in range(0, len(qs), chunk):
        part = qs[s:s + chunk]; verts = []; idx = []
        for i, (c, z, uv) in enumerate(part):
            z = np.broadcast_to(np.asarray(z, float), (4,))
            if i == 0:
                verts += [(float(c[0][0]), float(c[0][1]), float(z[0]), float(uv[0][0]), float(uv[0][1])), (float(c[1][0]), float(c[1][1]), float(z[1]), float(uv[1][0]), float(uv[1][1]))]
            verts += [(float(c[3][0]), float(c[3][1]), float(z[3]), float(uv[3][0]), float(uv[3][1])), (float(c[2][0]), float(c[2][1]), float(z[2]), float(uv[2][0]), float(uv[2][1]))]
            a = 2 * i; L0, R0, L1, R1 = a, a + 1, a + 2, a + 3
            idx += [L0, R0, R1, L0, R1, L1]
        v = verts; nz = (v[1][0] - v[0][0]) * (v[3][1] - v[0][1]) - (v[1][1] - v[0][1]) * (v[3][0] - v[0][0])
        if nz < 0: idx = [x for t in range(0, len(idx), 3) for x in (idx[t], idx[t + 2], idx[t + 1])]
        out.append(Prim(verts, idx, color, key))
    return out

def tri_prim(pts, uv, color=0xFFB0B0B0, key=0, double=True):
    verts = [(float(p[0]), float(p[1]), float(p[2]), float(t[0]), float(t[1])) for p, t in zip(pts, uv)]
    return Prim(verts, [0, 1, 2, 0, 2, 1] if double else [0, 1, 2], color, key)

def _slot_key(o):
    sn = next((k for k in o.node.kids if k.magic == 0x134012), None)
    if sn is None: return 0
    f = fill(sn.data); return struct.unpack_from("<I", sn.data, f)[0] if len(sn.data) >= f + 8 else 0

GROW = {"geom": True}   # host buffers may grow beyond the deleted object's size (file is relocated afterwards, see relocate.py)
MAX_KEYS = 16           # textures (materials) one hosted object may carry

def _pad_node(growth):
    """padding chunk (magic 0) that makes a chunk-level growth a multiple of 128 bytes (the engine 128-aligns vertex data inside chunks)"""
    return bun.Node(0, data=b"\0" * ((-growth - 8) % 128)) if growth % 128 else None

def _write_host(h, prims):
    """Overwrite a deleted object with `prims`. Prims may use SEVERAL textures: the object gets one material (B02 record + slot entry) per distinct key."""
    o = h.obj; old_size = len(bun.serialize([o.node], False)); B1, B2, B3 = o.ch[0x134B01], o.ch[0x134B02], o.ch[0x134B03]
    f1 = fill(B1.data); n_old = (len(B1.data) - f1) // 24; old = o.verts; old = old[np.isfinite(old).all(1) & (abs(old[:, :2]).max(1) < 9000)]
    orig_key = _slot_key(o); groups = {}                                   # key -> prims (key 0 = the texture the object already had)
    for pr in prims: groups.setdefault(pr.key or orig_key, []).append(pr)
    keys = list(groups); N = len(keys); assert N <= MAX_KEYS, ("too many textures for one host", N)
    verts = bytearray(B1.data[:f1]); pts = []; ranges = []; idx = []
    for key in keys:
        start = len(idx)
        for pr in groups[key]:
            b = len(pts)
            for (x, y, z, u, v) in pr.verts: pts.append((x, y, z)); verts += struct.pack("<3fI2f", x, y, z, pr.color, u, v)
            idx += [b + i for i in pr.idx]
        ranges.append((start, len(idx)))
    assert len(pts) <= n_old or GROW["geom"], ("host vertex buffer would grow", h.name, len(pts), n_old); assert len(pts) < 65535
    n_out = n_old if len(pts) <= n_old else n_old + -(-(len(pts) - n_old) // 16) * 16      # grow in multiples of 16 vertices (16*24 = 3*128 B)
    tail = len(B1.data) - f1 - n_old * 24
    verts += struct.pack("<3fI2f", *pts[0], prims[0].color, 0, 0) * (n_out - len(pts)) + B1.data[len(B1.data) - tail:] * (tail > 0)
    ni = max(len(B3.data) // 2, (len(idx) + 3) // 3 * 3); assert len(idx) <= ni, ("host index buffer would grow", h.name, len(idx), ni)
    a_last, b_last = ranges[-1]; pat = idx[a_last:b_last][:12] if b_last - a_last >= 3 else []
    while pat and len(idx) + len(pat) <= ni // 3 * 3 and len(pat) % 3 == 0: idx += pat              # pad the LAST material with REAL duplicate triangles, never zero-area ones
    idx += [idx[-1]] * (ni - len(idx)); ranges[-1] = (a_last, len(idx))
    P3 = np.array(pts, "f4"); lo, hi = P3.min(0) - 0.5, P3.max(0) + 0.5
    r = B2.data; f2 = fill(r); nrec_old = (len(r) - f2) // 60; tpl = bytes(r[f2:f2 + 60]); out = bytearray(r[:f2])
    for k, (a, bb) in enumerate(ranges):
        rec = bytearray(tpl); sub = P3[np.unique(np.array(idx[a:bb]))]
        rec[0:12] = struct.pack("<3f", *sub.min(0)); rec[16:28] = struct.pack("<3f", *sub.max(0))
        rec[12:16] = struct.pack("<I", bb - a); rec[0x1c:0x20] = struct.pack("<I", k); rec[0x34:0x38] = struct.pack("<I", a); out += rec
    hd = next(k for k in o.node.kids if k.magic == 0x134011); hb = bytearray(hd.data); fl = np.frombuffer(bytes(hb[:len(hb) // 4 * 4]), "<f4")
    for j in range(0, len(fl) - 8):
        if len(old) and np.all(np.abs(fl[j:j + 3] - old.min(0)) < 6) and np.all(np.abs(fl[j + 4:j + 7] - old.max(0)) < 6):
            struct.pack_into("<3f", hb, j * 4, *lo); struct.pack_into("<3f", hb, j * 4 + 16, *hi); break
    f4 = fill(hb); old_ni = len(B3.data) // 2
    if len(hb) >= f4 + 24 and struct.unpack_from("<I", hb, f4 + 20)[0] == old_ni // 3: struct.pack_into("<I", hb, f4 + 20, ni // 3)        # object header keeps the triangle count
    if len(hb) >= f4 + 28 and struct.unpack_from("<I", hb, f4 + 24)[0] == nrec_old * 256: struct.pack_into("<I", hb, f4 + 24, N * 256)    # ... and the material count (x256)
    c9 = o.ch.get(0x134900)          # per-object counts: u32[4] = materials, u32[9] = triangles, u32[13] = vertices
    if c9 is not None and len(c9.data) >= 56:
        d9 = bytearray(c9.data); struct.pack_into("<I", d9, 16, N); struct.pack_into("<I", d9, 36, ni // 3); struct.pack_into("<I", d9, 52, n_out); c9.data = bytes(d9)
    # slot (material -> texture key) table: one 8-byte entry per material
    sn = next((k for k in o.node.kids if k.magic == 0x134012), None); slot_growth = 0
    if sn is not None:
        fs = fill(sn.data); ent = bytes(sn.data[fs:fs + 8]) if len(sn.data) >= fs + 8 else b"\0" * 8; old_len = len(sn.data); nd = bytearray(sn.data[:fs])
        for key in keys: e = bytearray(ent); struct.pack_into("<I", e, 0, key); nd += e
        sn.data = bytes(nd); slot_growth = len(sn.data) - old_len
    hd.data = bytes(hb); B1.data = bytes(verts); B2.data = bytes(out); B3.data = np.array(idx, "<u2").tobytes()
    # keep every following chunk's address 128-aligned: padding chunks after the grown slot table and the grown record table
    pad = _pad_node(slot_growth)
    if pad is not None: o.node.kids.insert(o.node.kids.index(sn) + 1, pad)
    cont = next(k for k in o.node.kids if k.magic == 0x80134100); pad = _pad_node(len(out) - len(r))
    if pad is not None: cont.kids.insert(cont.kids.index(B2_node(cont)) + 1, pad)
    delta = len(bun.serialize([o.node], False)) - old_size
    if delta % 128: o.node.kids.append(bun.Node(0, data=b"\0" * ((-delta - 8) % 128)))
    pb = bytearray(h.place.node.data); struct.pack_into("<3f", pb, h.place.off, *lo); struct.pack_into("<3f", pb, h.place.off + 12, *hi); h.place.node.data = bytes(pb)
    return keys

def B2_node(cont): return next(k for k in cont.kids if k.magic == 0x134B02)

def layout(hosts, prims):
    """greedy: prims grouped by texture key; a host takes up to MAX_KEYS different textures (one material each). Returns [(host, None, prims)]."""
    pool = list(hosts); plan = []; groups = {}
    for p in prims: groups.setdefault(p.key, []).append(p)
    cur = None; keys = set(); nv = 0
    for key, items in groups.items():
        need = sum(len(p.verts) for p in items)
        if cur is None or len(keys) >= MAX_KEYS or nv + need > 60000:
            assert pool, ("not enough host objects: delete one more object (each host takes up to %d textures)" % MAX_KEYS, key)
            cur = pool.pop(0); plan.append((cur, None, [])); keys = set(); nv = 0
        plan[-1][2].extend(items); keys.add(key); nv += need
    return plan

def write_prims(hosts, prims):
    plan = layout(hosts, prims); rep = []
    for h, _, lst in plan:
        keys = _write_host(h, lst); rep.append((h.name, len(lst), len(keys)))
    return rep

def write_quads(hosts, quads):
    """compat: quads = [(corners 4x2, z, rgba)]"""
    return write_prims(hosts, [quad_prim(c, z, col) for c, z, col in quads])
