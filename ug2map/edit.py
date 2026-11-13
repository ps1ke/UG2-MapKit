"""Apply edit operations (the "ops" of a project's edits.json) to a parsed STREAML4Rx.BUN.

Every op is a plain dict with an "op" name and a "region" (L4RA = the free-roam city). All ops are SYMBOLIC (object ids, instance slots,
polygon numbers, coordinates): nothing in them is a byte offset, so projects can be combined. Files are always re-written from the untouched originals.

  remove_object      {id}                                  hide a baked object (terrain / road piece); frees its room for new surfaces
  transform_object   {id, center, position, quaternion, scale}
  move_instance      {sec, off, T, R, aabb}                move / hide a placed prop, tree or building
  duplicate_instance {sec, off, ...}                       copy a placed model (re-uses a small prop slot)
  move_vertices      {id, moves:[[vertex, x, y, z]], proto?}  reshape an object (props: reshapes every copy)
  set_texture        {id, from_key, to_key}                switch a material of an object / prop to another texture
  replace_texture    {key, image}                          overwrite a texture with your own image (same size / format)
  add_texture        {name, w, h, fmt, image}              add a new texture to the texture pack
  add_quad / add_road / add_box / add_mesh                 new visible surfaces / imported models (hosted in removed objects); collision:true makes them solid
  add_wall           {kind: tall|mid, p0, p1, z, sides}    invisible collision wall (the collision array grows when no slot is free)
  remove_polys       {sid, polys}                          delete collision polygons
  clear_walls_box    {box}                                 delete collision walls in a rectangle
  paste              {src, items, centroid, pos, quat, scl, size}   copy objects (and the ground / walls under them) from any region
  paste_collision    {polys, centroid, pos, quat, scl}     copy only collision polygons
  move_marker / add_marker {hash, index, pos, angle}       shop / garage / spawn markers (handled by markers.py)
"""
from __future__ import annotations
import re
import struct
import numpy as np
from . import bun, world, host, tex
from .world import fill

def _quat_matrix(q):
    x, y, z, w = q; return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])

def refresh_bounds(o, old_lo, old_hi, place):
    """after vertices moved: per-range bboxes (0x134B02), object header bounds (0x134011), placement AABB (0x34103)"""
    B1, B2, B3 = o.ch[0x134B01], o.ch[0x134B02], o.ch[0x134B03]; v = o.verts; ok = np.isfinite(v).all(1) & (abs(v[:, :2]).max(1) < 9000)
    lo, hi = v[ok].min(0) - 0.5, v[ok].max(0) + 0.5
    d3 = B3.data; f3 = fill(d3); idx = np.frombuffer(d3[f3:f3 + (len(d3) - f3) // 2 * 2], "<u2").astype(np.int64)
    r = bytearray(B2.data); f2 = fill(r)
    for i in range(f2, len(r) - 59, 60):
        cnt, = struct.unpack_from("<I", r, i + 0x0c); st, = struct.unpack_from("<I", r, i + 0x34); sub = idx[st:st + cnt]; sub = sub[sub < len(v)]
        if len(sub) and ok[sub].any(): s = v[sub[ok[sub]]]; struct.pack_into("<3f", r, i, *s.min(0)); struct.pack_into("<3f", r, i + 16, *s.max(0))
    B2.data = bytes(r)
    hd = next(k for k in o.node.kids if k.magic == 0x134011); hb = bytearray(hd.data); fl = np.frombuffer(bytes(hb[:len(hb) // 4 * 4]), "<f4")
    for j in range(0, len(fl) - 8):
        if np.all(np.abs(fl[j:j + 3] - old_lo) < 6) and np.all(np.abs(fl[j + 4:j + 7] - old_hi) < 6): struct.pack_into("<3f", hb, j * 4, *lo); struct.pack_into("<3f", hb, j * 4 + 16, *hi); break
    hd.data = bytes(hb)
    if place is not None:
        pb = bytearray(place.node.data); struct.pack_into("<3f", pb, place.off, *lo); struct.pack_into("<3f", pb, place.off + 12, *hi); place.node.data = bytes(pb)

def transform_object(o, place, center, position, quat, scale):
    lo, hi = o.bounds(); B1 = o.ch[0x134B01]; d = bytearray(B1.data); f = fill(d); n = (len(d) - f) // 24
    R = _quat_matrix(quat) * np.array(scale)[None, :] if False else _quat_matrix(quat) @ np.diag(scale); C = np.array(center); P = np.array(position)
    for i in range(n):
        v = np.array(struct.unpack_from("<3f", d, f + i * 24))
        if not np.isfinite(v).all() or abs(v[:2]).max() > 9000: continue
        struct.pack_into("<3f", d, f + i * 24, *(P + R @ (v - C)))
    B1.data = bytes(d); refresh_bounds(o, lo, hi, place)

def remove_object(o):
    B1 = o.ch[0x134B01]; d = bytearray(B1.data); f = fill(d); n = (len(d) - f) // 24
    for i in range(n): struct.pack_into("<3f", d, f + i * 24, 0.0, 0.0, -500.0)
    B1.data = bytes(d)

def _masks(c, xs, ys):
    out = []; xmn, xmx, ymn, ymx = min(xs), max(xs), min(ys), max(ys)
    for cx in range(max(int(np.floor((xmn - c.ox) / 32)), 0), min(int(np.floor((xmx - c.ox) / 32)), c.W - 1) + 1):
        for cy in range(max(int(np.floor((ymn - c.oy) / 32)), 0), min(int(np.floor((ymx - c.oy) / 32)), c.H - 1) + 1):
            x0, y0 = c.ox + cx * 32, c.oy + cy * 32; m = 0
            for sy in range(4):
                for sx in range(4):
                    if xmx >= x0 + sx * 8 and xmn <= x0 + sx * 8 + 8 and ymx >= y0 + sy * 8 and ymn <= y0 + sy * 8 + 8: m |= 1 << (sy * 4 + sx)
            if m: out.append((cy * c.W + cx, m))
    return out

def _lists(c):
    L = {}
    for ci in range(c.W * c.H):
        st, cnt = struct.unpack_from("<HH", c.cells, ci * 4); L[ci] = [struct.unpack_from("<HH", c.refs, (st + k) * 4) for k in range(cnt)]
    return L

def bbox_byte(xs, ys):
    """byte +1 of a collision polygon = four 2-bit vertex indices (x-min, y-min, x-max, y-max) that the engine uses as the polygon's 2D bounding box when it
    gathers candidates (decoded from the broadphase at 0x5e0f7f). A wrong byte makes the polygon fail the box test forever = it never collides."""
    xs = [int(v) for v in xs]; ys = [int(v) for v in ys]
    return xs.index(min(xs)) | ys.index(min(ys)) << 2 | xs.index(max(xs)) << 4 | ys.index(max(ys)) << 6

GROW = {"refs": True}     # kept for compatibility; the reference table is now only ever APPENDED to

def _store_lists(c, L):
    """Write cell lists back WITHOUT disturbing the original layout: the engine depends on the original order of the references inside a cell
    (31 of 154 cells in section 1735 are not sorted by polygon index) and many cells share one run of references. Every cell whose list
    changed gets a new run appended at the end of the table (the chunk grows in 128-byte steps); untouched cells keep their pointers."""
    c._deadcache = None
    old = bytes(c.refs); cells = bytearray(c.cells); extra = bytearray(); base = len(old) // 4
    for ci in range(c.W * c.H):
        st, cnt = struct.unpack_from("<HH", cells, ci * 4); cur = [struct.unpack_from("<HH", old, (st + k) * 4) for k in range(cnt)]
        new = [tuple(e) for e in L[ci]]
        if new == [tuple(e) for e in cur]: continue
        struct.pack_into("<HH", cells, ci * 4, base + len(extra) // 4, len(new))
        for pi, m in new: extra += struct.pack("<HH", pi, m)
    assert base + len(extra) // 4 < 65535, "collision reference table too large"
    extra += b"\0" * ((-len(extra)) % 128); c.refs = bytearray(old + bytes(extra)); c.cells = cells

def remove_polys(c, ids):
    """Delete collision polygons by removing their cell references ONLY. The polygon keeps its shape and its place in the array: the engine
    identifies the wall groups by the polygons' shapes/positions in the array, and parking a polygon (zero area, far away) in the wall range broke
    the physics of the WHOLE section (found 2026-11-12: spawn pushed 7 m, car stuck)."""
    ids = set(ids); L = _lists(c)
    for ci in L: L[ci] = [e for e in L[ci] if e[0] not in ids]
    _store_lists(c, L); c._deadcache = None

def _parked(c, i):
    d = getattr(c, "_deadcache", None)
    if d is None or d[0] != len(c.refs) or d[1] != bytes(c.cells[:64]):
        used = set()
        for ci, lst in _lists(c).items(): used.update(e[0] for e in lst)
        d = c._deadcache = (len(c.refs), bytes(c.cells[:64]), used)
    return i not in d[2]

def _wall_run(c, kind):
    """index range of the polygon array the engine keeps tall walls (tail) / mid walls (head) in; only slots inside it can hold a wall of that kind"""
    n = c.npoly
    if kind == "tall":
        i = n
        while i > 0 and (c.kind(i - 1) == "wall_tall" or _parked(c, i - 1)): i -= 1
        return i, n
    j = 0
    while j < n and (c.kind(j) == "wall_mid" or _parked(c, j)): j += 1
    return 0, j

def free_slots(c, kind):
    """parked (deleted) polygons in the right part of the array: tall walls = the tail, mid walls = the head"""
    n = c.npoly
    if kind == "tall":
        i = n
        while i > 0 and (c.kind(i - 1) == "wall_tall" or _parked(c, i - 1)): i -= 1
        return [j for j in range(i, n) if _parked(c, j)]
    j = 0
    while j < n and (c.kind(j) == "wall_mid" or _parked(c, j)): j += 1
    return [k for k in range(j) if _parked(c, k)]

def add_wall(c, kind, p0, p1, z, sides="both", grow=True):
    """Collision walls are ONE-SIDED (facing follows the vertex order, verified in game 2026-11-12): 'both' writes two polygons with opposite order so the
    wall blocks from either side. Returns the polygon indices used."""
    ensure_grid(c, min(p0[0], p1[0]) - 0.5, max(p0[0], p1[0]) + 0.5, min(p0[1], p1[1]) - 0.5, max(p0[1], p1[1]) + 0.5)
    slots = free_slots(c, "tall" if kind == "tall" else "mid"); need = 2 if sides == "both" else 1
    if len(slots) < need and grow:                       # no free slot: GROW the section's polygon array (the chunk gets 32 bytes bigger per polygon; refs reach polygons by index)
        extra = need - len(slots); first = c.npoly; c.polys.extend(bytes(32 * extra)); c._deadcache = None; slots = slots + list(range(first, first + extra))
    assert len(slots) >= need, f"section {c.sid} has {len(slots)} free {kind}-wall slot(s), {need} needed: delete {need - len(slots)} more existing {kind} wall(s) in this section first"
    c._dirty = True; used = []; orders = [(p1, p0), (p0, p1)] if sides == "both" else [((p1, p0) if sides != "back" else (p0, p1))]
    for slot, (a, b_) in zip(slots, orders):
        xs = [b_[0], b_[0], a[0], a[0]]; ys = [b_[1], b_[1], a[1], a[1]]
        if kind == "tall": zb, zd = 0, [-127 * 256, 127 * 256, 127 * 256, -127 * 256]
        else: zb = int(round(z[0])); zd = [0, int(round((z[1] - zb) * 256)), int(round((z[1] - zb) * 256)), 0]
        qx = [int(round(v * 8)) for v in xs]; qy = [int(round(v * 8)) for v in ys]
        c.polys[slot * 32:slot * 32 + 32] = struct.pack("<BBHhH4h4h4h", 0, bbox_byte(qx, qy), 5652, zb, 0xFFFF, *qx, *qy, *zd)
        L = _lists(c)
        for ci, m in _masks(c, xs, ys): L[ci].append((slot, m))
        _store_lists(c, L); used.append(slot)
    return used

def box_quads(center, size, quat, color):
    sx, sy, sz = [s / 2 for s in size]; R = _quat_matrix(quat); C = np.array(center)
    pts = {(a, b, d): C + R @ np.array([a * sx, b * sy, d * sz]) for a in (-1, 1) for b in (-1, 1) for d in (-1, 1)}
    faces = [[(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], [(-1, -1, -1), (-1, 1, -1), (1, 1, -1), (1, -1, -1)], [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)],
             [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)], [(1, 1, -1), (-1, 1, -1), (-1, 1, 1), (1, 1, 1)], [(-1, 1, -1), (-1, -1, -1), (-1, -1, 1), (-1, 1, 1)]]
    out = []
    for f in faces:
        P = np.array([pts[k] for k in f]); out.append((P[:, :2], P[:, 2], color))
    return out

def catmull(points, step=5.0):
    P = [np.array(points[0], float) * 2 - np.array(points[1], float)] + [np.array(p, float) for p in points] + [np.array(points[-1], float) * 2 - np.array(points[-2], float)]
    pts = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for t in np.arange(0, 1, 0.02): pts.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    pts.append(P[-2]); pts = np.array(pts); seg = np.linalg.norm(np.diff(pts, axis=0), axis=1); s = np.concatenate([[0], np.cumsum(seg)])
    n = max(1, int(round(s[-1] / step))); sv = np.linspace(0, s[-1], n + 1)
    return np.stack([np.interp(sv, s, pts[:, k]) for k in range(pts.shape[1])], 1), sv

def road_quads(op):
    """polyline ribbon: returns [(corners 4x2, z[4], uv[4x2])]"""
    pts = [list(p) + [op.get("z", 30.0)] * (3 - len(p)) for p in op["points"]]
    if len(pts) == 2: pts = [pts[0], [(a + b) / 2 for a, b in zip(pts[0], pts[1])], pts[1]]
    c, sv = catmull(pts, op.get("step", 5.0)); hw = op["width"] / 2; tile = op.get("uv_len", 10.0); out = []
    for i in range(len(c) - 1):
        cs = []
        for j in (i, i + 1):
            d = c[min(j + 1, len(c) - 1), :2] - c[max(j - 1, 0), :2]; d = d / (np.linalg.norm(d) or 1); nrm = np.array([-d[1], d[0]]); cs.append((c[j, :2] + nrm * hw, c[j, :2] - nrm * hw, c[j, 2]))
        corners = np.array([cs[0][0], cs[0][1], cs[1][1], cs[1][0]]); z = np.array([cs[0][2], cs[0][2], cs[1][2], cs[1][2]]); v0, v1 = sv[i] / tile, sv[i + 1] / tile
        out.append((corners, z, [(0, v0), (1, v0), (1, v1), (0, v1)]))
    return out

def _insert_ground_ref(c, lst, poly, mask):
    """The engine's ground query scans a cell's references IN ORDER and STOPS at the first polygon whose flags byte has bit 0x2 or 0x8 (walls)
    (WCollider::QueryGround, decompiled in troikoss/speed-re). Ground references therefore must come BEFORE the first wall reference of the cell."""
    pos = next((k for k, (pp, mm) in enumerate(lst) if c.polys[pp * 32 + 3] & 0xA), len(lst)); lst.insert(pos, (poly, mask))

def add_ground(c, quads, surface=(0, 147, 4097), prefer_box=None):
    """solid ground: reuse the farthest ground polygons of the section as donors (high indices first, any index when the section is small)"""
    cx = np.mean([q[0].mean(0) for q in quads], 0); gnd = []; low = []
    for i in range(c.npoly):
        x, y, z = c.poly(i)
        if x.min() < -3500 or c.kind(i) != "ground" or z.max() - z.min() >= 3 or abs((x[2] - x[0]) * (y[3] - y[1]) - (y[2] - y[0]) * (x[3] - x[1])) / 2 <= 0.5: continue
        (gnd if i >= 300 else low).append((np.hypot(x.mean() - cx[0], y.mean() - cx[1]), i))
    gnd.sort(reverse=True); low.sort(reverse=True); gnd = gnd + low
    unused_inside = []
    if callable(prefer_box):                                    # REPLACE mode (paste): the destination ground under the pasted area is swapped for the pasted ground; more slots are made by growing the array
        inside = [i for i in range(c.npoly) if c.kind(i) == "ground" and not _parked(c, i) and c.poly(i)[0].min() > -3500 and prefer_box(c.poly(i)[0].mean(), c.poly(i)[1].mean())]
        gnd = [(0.0, i) for i in inside]
        if len(gnd) < len(quads):
            extra = len(quads) - len(gnd); first = c.npoly; c.polys.extend(bytes(32 * extra)); c._deadcache = None; gnd += [(0.0, j) for j in range(first, first + extra)]
        unused_inside = inside[len(quads):]
    elif prefer_box:                                            # ground that is being replaced anyway makes the best donor: use it first
        b = prefer_box; inside = []
        for i in range(c.npoly):
            x, y, z = c.poly(i)
            if x.min() > -3500 and c.kind(i) == "ground" and b[0] <= x.mean() <= b[1] and b[2] <= y.mean() <= b[3] and z.max() - z.min() < 3 and abs((x[2] - x[0]) * (y[3] - y[1]) - (y[2] - y[0]) * (x[3] - x[1])) / 2 > 0.5: inside.append((0.0, i))
        got = {i for _, i in inside}; gnd = inside + [g for g in gnd if g[1] not in got]
    assert len(gnd) >= len(quads), ("not enough donor ground polygons in section", c.sid, len(gnd), len(quads))
    c._dirty = True; slot = [i for _, i in gnd[:len(quads)]]; c._new_slots = slot; L = _lists(c); drop = set(slot) | set(unused_inside)
    for ci in L: L[ci] = [e for e in L[ci] if e[0] not in drop]
    for (corners, z, uv), i in zip(quads, slot):
        zv = np.broadcast_to(np.asarray(z, float), (4,)); base = int(round(float(zv.min()))); zds = [int(round((float(v) - base) * 256)) for v in zv]
        qx = [int(round(v * 8)) for v in corners[:, 0]]; qy = [int(round(v * 8)) for v in corners[:, 1]]; c.polys[i * 32:i * 32 + 32] = struct.pack("<BBHhH4h4h4h", surface[0], bbox_byte(qx, qy), surface[2], base, 0xFFFF, *qx, *qy, *zds)
        for ci, m in _masks(c, list(corners[:, 0]), list(corners[:, 1])): _insert_ground_ref(c, L[ci], i, m)
    if False:      # (disabled: the reference table can now grow) out of cell references: drop refs of OLD ground polygons that lie completely under a new polygon (they are redundant there)
        newset = set(slot); redundant = set()
        def under(px, py, pz):
            for (corners, z, uv), _ in zip(quads, slot):
                zz = np.broadcast_to(np.asarray(z, float), (4,))
                for tri in ((0, 1, 2), (0, 2, 3)):
                    A, B, C = corners[tri[0]], corners[tri[1]], corners[tri[2]]; d = (B[1] - C[1]) * (A[0] - C[0]) + (C[0] - B[0]) * (A[1] - C[1])
                    if abs(d) < 1e-9: continue
                    w0 = ((B[1] - C[1]) * (px - C[0]) + (C[0] - B[0]) * (py - C[1])) / d; w1 = ((C[1] - A[1]) * (px - C[0]) + (A[0] - C[0]) * (py - C[1])) / d; w2 = 1 - w0 - w1
                    if min(w0, w1, w2) >= -1e-3 and abs(w0 * zz[tri[0]] + w1 * zz[tri[1]] + w2 * zz[tri[2]] - pz) < 3.0: return True
            return False
        for i in range(c.npoly):
            if i in newset: continue
            x, y, z = c.poly(i)
            if x.min() < -3500 or c.kind(i) != "ground": continue
            if under(x.mean(), y.mean(), z.mean()) and sum(under(x[k], y[k], z[k]) for k in range(4)) >= 3: redundant.add(i)
        for ci in L: L[ci] = [e for e in L[ci] if e[0] not in redundant]
        c.reclaimed = len(redundant)
    _store_lists(c, L); return len(slot)

def merge_quads(qs, tol=0.6, maxn=5):
    """collision only: fuse consecutive ribbon quads [L0,R0,R1,L1] into longer ones while the in-between edges stay within tol metres of the straight line -> fewer polygons and cell references"""
    out = []; i = 0
    while i < len(qs):
        j = i + 1
        while j < len(qs) and j - i < maxn:
            ok = True; a, b = qs[i], qs[j]
            L0, R0 = a[0][0], a[0][1]; L1, R1 = b[0][3], b[0][2]; zL0, zR0, zR1, zL1 = a[1][0], a[1][1], b[1][2], b[1][3]
            if not all(abs(qs[k][0][3][0] - qs[k + 1][0][0][0]) < 1e-6 for k in range(i, j)): break
            L = np.array(L1 - L0, float); n = max(1, j - i + 1); tot = sum(np.linalg.norm(qs[k][0][3] - qs[k][0][0]) for k in range(i, j + 1)) or 1; acc = 0
            for k in range(i, j):
                acc += np.linalg.norm(qs[k][0][3] - qs[k][0][0]); t = acc / tot
                for (P, P0, P1, z, zz0, zz1) in ((qs[k][0][3], L0, L1, qs[k][1][3], zL0, zL1), (qs[k][0][2], R0, R1, qs[k][1][2], zR0, zR1)):
                    if np.linalg.norm(P - (P0 + (P1 - P0) * t)) > tol or abs(z - (zz0 + (zz1 - zz0) * t)) > 0.2: ok = False
            if not ok: break
            j += 1
        a, b = qs[i], qs[j - 1]
        out.append((np.array([a[0][0], a[0][1], b[0][2], b[0][3]], float), np.array([a[1][0], a[1][1], b[1][2], b[1][3]], float), a[2])); i = j
    return out


class Topology:
    """Which collision section the engine uses at a point: the section OUTLINE polygons of L4Rx.BUN (chunk 0x80034150 / 0x34152), exactly as
    TrackTopology::FindSection does (decompiled in troikoss/speed-re). Collision packs are looked up by section id with ids whose number part is in
    [offset, 2*offset) folded down by `offset` (CollisionSectionIndex), so STREAM section 1735 serves topology section 1715."""
    def __init__(self, l4_bytes):
        t = bun.parse(l4_bytes); self.secs = []; self.offset = 20
        def walk(nodes):
            for n in nodes:
                if n.magic == 0x80034150:
                    for k in n.kids:
                        if k.magic == 0x34151 and len(k.data) >= 4: self.offset = struct.unpack_from("<I", k.data, 0)[0] or 20
                        if k.magic == 0x34152:
                            d = k.data; o = 0
                            while o + 0x24 <= len(d):
                                sid, cnt = struct.unpack_from("<hh", d, o + 8); mn = struct.unpack_from("<2f", d, o + 0xc); mx = struct.unpack_from("<2f", d, o + 0x14)
                                pts = [struct.unpack_from("<2f", d, o + 0x24 + 8 * i) for i in range(cnt)]; self.secs.append((sid, mn, mx, pts)); o += cnt * 8 + 0x24
                elif n.kids: walk(n.kids)
        walk(t)
    @staticmethod
    def _inside(pt, poly):
        x, y = pt; c = False; n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]; x2, y2 = poly[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
        return c
    def searched(self, sid):                                      # only these go into the list FindSection walks (the others are 'other sections')
        num = sid % 100; letter = chr(sid // 100 + 64); return 'A' <= letter <= 'T' and 1 <= num < self.offset
    def owners(self, pt): return {s[0] for s in self.secs if self.searched(s[0]) and s[1][0] <= pt[0] <= s[2][0] and s[1][1] <= pt[1] <= s[2][1] and self._inside(pt, s[3])}
    def base(self, sid):
        num = sid % 100; return sid - self.offset if self.offset <= num < 2 * self.offset else sid
    def collision_sections(self, cols, pts):
        """collision section ids (keys of `cols`) the engine may load at any of these points"""
        bases = set()
        for pt in pts: bases |= self.owners(pt)
        return sorted(sid for sid in cols if self.base(sid) in bases)

def _poly_dist(poly, p):
    return float(np.min(np.hypot(poly[:, 0] - p[0], poly[:, 1] - p[1])))

def _dense(points, step=4.0):
    pts = [np.array(q[:2], float) for q in points]; out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.hypot(*(b - a)) // step)); out += [a + (b - a) * k / n for k in range(1, n + 1)]
    return np.array(out)

def clear_corridor(tree, cols, td, points, radius, kinds=("walls", "props", "other", "signs", "buildings"), maxsize=80.0, keep=()):
    """Make room for a new road: PARK (move far below the map) every placed model of those kinds whose centre is within `radius` of the path, and
    remove the cell references of every collision wall polygon there. Trees/props carry their own collision, so leaving them blocks the car."""
    poly = _dense(points); secs = [n for n in tree if n.magic == 0x80034100]; parked = 0; walls = 0; ks = {}
    for ii, (si, row, j, oi, R, T, aabb) in enumerate(td.inst):
        if ii in keep or td._inst_cat(ii) not in kinds: continue
        size = max(aabb[3] - aabb[0], aabb[4] - aabb[1])
        if size > maxsize: continue
        hit = np.any((poly[:, 0] >= aabb[0] - radius) & (poly[:, 0] <= aabb[3] + radius) & (poly[:, 1] >= aabb[1] - radius) & (poly[:, 1] <= aabb[4] + radius))
        if not hit: continue
        if size > 14 and td._inst_cat(ii) in ("walls",):   # long thin piece: only remove when the path really crosses its footprint (not its empty bbox corner)
            dd = min(_poly_dist(poly, (aabb[0] + (aabb[3] - aabb[0]) * f, aabb[1] + (aabb[4] - aabb[1]) * g)) for f in np.linspace(0, 1, 9) for g in np.linspace(0, 1, 9))
            if dd > radius + 6: continue
        pk = next(c for c in secs[si].kids if c.magic == 0x34103); d = bytearray(pk.data); a = list(struct.unpack_from("<6f", d, j)); a[2] -= 2000; a[5] -= 2000
        t = list(struct.unpack_from("<3f", d, j + 0x20)); t[2] -= 2000; struct.pack_into("<6f", d, j, *a); struct.pack_into("<3f", d, j + 0x20, *t); pk.data = bytes(d); parked += 1
    for sid, c in cols.items():
        ids = []
        for i in range(c.npoly):
            if c.kind(i) not in ("wall_tall", "wall_mid"): continue
            x, y, z = c.poly(i)
            if x.min() > -3500 and min(_poly_dist(poly, (x[a] * (1 - f) + x[(a + 1) % 4] * f, y[a] * (1 - f) + y[(a + 1) % 4] * f)) for a in range(4) for f in (0.0, 0.5)) <= radius: ids.append(i)
        if ids: remove_polys(c, ids); walls += len(ids)
    return parked, walls

def clip_quad_to_rect(corners, z, rect, eps=0.05):
    """Collision polygons must lie INSIDE their section's grid (all 153,625 original ones do; polygons poking out crash the engine's area gather).
    Sutherland-Hodgman clip of a convex quad (corners 4x2, heights z[4]) to rect=(x0,x1,y0,y1); returns a list of (corners 4x2, z[4]) quads (fan-triangulated, 4th = 3rd vertex)."""
    poly = [(float(corners[i][0]), float(corners[i][1]), float(z[i])) for i in range(4)]
    x0, x1, y0, y1 = rect[0] + eps, rect[1] - eps, rect[2] + eps, rect[3] - eps
    def clip(pts, inside, inter):
        out = []
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            if inside(b):
                if not inside(a): out.append(inter(a, b))
                out.append(b)
            elif inside(a): out.append(inter(a, b))
        return out
    def ix(v): return lambda a, b: (v, a[1] + (b[1] - a[1]) * (v - a[0]) / (b[0] - a[0]), a[2] + (b[2] - a[2]) * (v - a[0]) / (b[0] - a[0]))
    def iy(v): return lambda a, b: (a[0] + (b[0] - a[0]) * (v - a[1]) / (b[1] - a[1]), v, a[2] + (b[2] - a[2]) * (v - a[1]) / (b[1] - a[1]))
    for ins, it in ((lambda q: q[0] >= x0, ix(x0)), (lambda q: q[0] <= x1, ix(x1)), (lambda q: q[1] >= y0, iy(y0)), (lambda q: q[1] <= y1, iy(y1))):
        if not poly: break
        poly = clip(poly, ins, it)
    if len(poly) < 3: return []
    area = 0.5 * abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))))
    if area < 0.5: return []
    out = []
    if len(poly) == 4: out.append((np.array([[p[0], p[1]] for p in poly]), np.array([p[2] for p in poly])))
    else:
        for k in range(1, len(poly) - 1):
            t = [poly[0], poly[k], poly[k + 1], poly[k + 1]]; out.append((np.array([[p[0], p[1]] for p in t]), np.array([p[2] for p in t])))
    return out

def _grid_rect(c): return (c.ox, c.ox + c.W * 32, c.oy, c.oy + c.H * 32)

def _zat(x, y, z, px, py):
    sg = 0
    for k in range(4):
        cr = (x[(k + 1) % 4] - x[k]) * (py - y[k]) - (y[(k + 1) % 4] - y[k]) * (px - x[k])
        if abs(cr) < 1e-9: continue
        sg2 = 1 if cr > 0 else -1
        if sg == 0: sg = sg2
        elif sg != sg2: return None
    for tri in ((0, 1, 2), (0, 2, 3)):
        A, B, C = [(x[k], y[k], z[k]) for k in tri]; d = (B[1] - C[1]) * (A[0] - C[0]) + (C[0] - B[0]) * (A[1] - C[1])
        if abs(d) < 1e-9: continue
        w0 = ((B[1] - C[1]) * (px - C[0]) + (C[0] - B[0]) * (py - C[1])) / d; w1 = ((C[1] - A[1]) * (px - C[0]) + (A[0] - C[0]) * (py - C[1])) / d; w2 = 1 - w0 - w1
        if min(w0, w1, w2) >= -1e-6: return w0 * A[2] + w1 * B[2] + w2 * C[2]
    return None

def ground_conflicts(c, slots, tol=0.08):
    """ORIGINAL ground polygons that lie ABOVE a new ground polygon at the same spot: the engine takes the highest surface, so the car pops up onto them.
    Returns report lines naming each polygon so it can be deleted (select the collision polygon, Delete)."""
    new = set(slots); used = set()
    for ci, lst in _lists(c).items(): used.update(e[0] for e in lst)
    old = []
    for i in used - new:
        if c.kind(i) != "ground": continue
        x, y, z = c.poly(i)
        if x.min() > -3500: old.append((i, x, y, z))
    agg = {}
    for n in slots:
        nx, ny, nz = c.poly(n)
        for px in np.arange(nx.min(), nx.max(), 3.0):
            for py in np.arange(ny.min(), ny.max(), 3.0):
                zn = _zat(nx, ny, nz, px, py)
                if zn is None: continue
                for i, x, y, z in old:
                    if x.min() > px or x.max() < px or y.min() > py or y.max() < py: continue
                    zo = _zat(x, y, z, px, py)
                    if zo is not None and zo - zn > tol: a = agg.setdefault(i, [0, 0.0, x.mean(), y.mean(), zo]); a[0] += 1; a[1] = max(a[1], zo - zn)
    return [f"WARNING section {c.sid}: ORIGINAL ground polygon {i} (centre {a[2]:.0f},{a[3]:.0f}, height {a[4]:.1f}) lies up to {a[1]:.1f} m ABOVE your new ground at {a[0]} spots - the car jumps onto it there. Select it (collision polygon) and press Delete." for i, a in sorted(agg.items(), key=lambda kv: -kv[1][0])]

def ensure_grid(c, x0, x1, y0, y1):
    """Extend a section's collision GRID (columns/rows of 32 m cells; header W,H,origin + cell table) so it covers the rectangle. The engine only looks for polygons inside
    the grid, so ground pasted outside it would not exist. New cells are empty; the old cells keep their places."""
    import math
    gx0, gx1, gy0, gy1 = c.ox, c.ox + c.W * 32, c.oy, c.oy + c.H * 32
    if x0 >= gx0 and x1 <= gx1 and y0 >= gy0 and y1 <= gy1: return False
    a = max(0, math.ceil((gx0 - x0) / 32.0)); b = max(0, math.ceil((x1 - gx1) / 32.0)); d = max(0, math.ceil((gy0 - y0) / 32.0)); e = max(0, math.ceil((y1 - gy1) / 32.0))
    W2, H2 = c.W + a + b, c.H + d + e; assert W2 * H2 < 40000, "collision grid would become too large"
    cells = bytearray(W2 * H2 * 4)
    for cy in range(c.H):
        row = bytes(c.cells[cy * c.W * 4:(cy + 1) * c.W * 4]); o = ((cy + d) * W2 + a) * 4; cells[o:o + len(row)] = row
    c.cells = cells; c.ox -= 32.0 * a; c.oy -= 32.0 * d; c.W, c.H = W2, H2
    struct.pack_into("<HH", c.header, 4, W2, H2); struct.pack_into("<ff", c.header, 8, c.ox, c.oy); c._dirty = True; c._deadcache = None
    return True

def refresh_col_bounds(c):
    """header bounds (min xyz at +0x10, max xyz at +0x20) = bounding box of all real polygons (verified identical on all 255 original sections)"""
    xs = []; ys = []; zs = []
    for i in range(c.npoly):
        x, y, z = c.poly(i)
        if x.min() < -3500: continue
        xs += list(x); ys += list(y); zs += list(z)
    if xs:
        struct.pack_into("<3f", c.header, 16, min(xs), min(ys), min(zs)); struct.pack_into("<3f", c.header, 32, max(xs), max(ys), max(zs))

def section_for(cols, x, y):
    best = None
    for sid, c in cols.items():
        if c.ox <= x <= c.ox + c.W * 32 and c.oy <= y <= c.oy + c.H * 32 and (best is None or c.W * c.H < best.W * best.H): best = c
    return best

def apply(tree, ops, log=print, getdata=None, topo=None):
    objs = world.load_objects(tree); cols = world.load_collisions(tree); places = host.index_placements(tree); pm = host.match_placements(objs, places); packs = tex.load_packs(tree)
    ops = list(ops); freed = []; prims = []; report = []; ground = []; _used = {}; soft = []
    for op in ops:
        k = op["op"]
        if k == "remove_object":
            o = objs[op["id"]]; cap = o.capacity; pl = pm.get(id(o)); remove_object(o)
            if cap > 0 and pl is not None: freed.append(host.Host(o, pl, cap))
            report.append(f"removed {o.name} (frees {cap} quads of room)")
        elif k == "transform_object":
            o = objs[op["id"]]; transform_object(o, pm.get(id(o)), op["center"], op["position"], op["quaternion"], op["scale"]); report.append(f"moved {o.name}")
        elif k == "move_vertices":
            o = objs[op["id"]]; lo, hi = o.bounds(); B1 = o.ch[0x134B01]; d = bytearray(B1.data); f = fill(d)
            for i, x, y, z in op["moves"]: struct.pack_into("<3f", d, f + int(i) * 24, x, y, z)
            B1.data = bytes(d); refresh_bounds(o, lo, hi, pm.get(id(o))); report.append(f"moved {len(op['moves'])} vertices of {o.name}")
        elif k == "move_instance":
            secs = [n for n in tree if n.magic == 0x80034100]; pk = next(c for c in secs[op["sec"]].kids if c.magic == 0x34103); d = bytearray(pk.data); j = op["off"]
            struct.pack_into("<6f", d, j, *op["aabb"]); struct.pack_into("<3f", d, j + 0x20, *op["T"])
            struct.pack_into("<9h", d, j + 0x2c, *[max(-32768, min(32767, int(round(v * 8192)))) for v in op["R"]]); pk.data = bytes(d); report.append(f"moved placement {op.get('name','')}")
        elif k == "duplicate_instance":
            # copy a placed model to a new spot by REPURPOSING a small prop placement of the same section (placements cannot be added; rewriting one is size-neutral)
            from .server import category
            secs = [n for n in tree if n.magic == 0x80034100]; pk = next(c for c in secs[op["sec"]].kids if c.magic == 0x34103); d = bytearray(pk.data); po = len(d) % 64
            used = _used.setdefault(op["sec"], {op["off"]}); cand = []
            kq = {c.magic: c for c in secs[op["sec"]].kids}[0x34102].data; qo = len(kq) % 68
            for j in range(po, len(d) - 63, 64):
                if j in used: continue
                ti = struct.unpack_from("<H", d, j + 0x18)[0]; a = struct.unpack_from("<6f", d, j)
                if (ti + 1) * 68 + qo > len(kq): continue
                nm = kq[qo + ti * 68:qo + ti * 68 + 32].split(b"\0")[0].decode("latin1")
                if category(nm) == "props" and a[3] - a[0] < 30 and a[5] - a[2] < 30: cand.append(((a[3] - a[0]) * (a[4] - a[1]) * (a[5] - a[2]), j))
            assert cand, ("no spare prop placement in section", op["sec"]); cand.sort(); _, v = cand[0]; used.add(v)
            d[v:v + 64] = d[op["off"]:op["off"] + 64]
            struct.pack_into("<6f", d, v, *op["aabb"]); struct.pack_into("<3f", d, v + 0x20, *op["T"]); struct.pack_into("<9h", d, v + 0x2c, *[max(-32768, min(32767, int(round(x * 8192)))) for x in op["R"]])
            pk.data = bytes(d); report.append(f"duplicated {op.get('name','')} (sacrificed one small prop placement, row offset {v})")
        elif k == "clear_walls_box":
            b = op["box"]; n_ = 0
            for sid, c in cols.items():
                ids = [i for i in range(c.npoly) if c.kind(i) in ("wall_tall", "wall_mid") and c.poly(i)[0].min() > -3500 and any(b[0] <= c.poly(i)[0][a] <= b[1] and b[2] <= c.poly(i)[1][a] <= b[3] for a in range(4))]
                if ids: remove_polys(c, ids); n_ += len(ids)
            report.append(f"removed {n_} collision wall polygons in the box {b}")
        elif k == "import_walls":
            td = getdata(op["src"]); poly = _dense(op["route"]); r = op.get("radius", 14.0); seen = set(); n_new = 0; skipped = 0
            if op.get("clear_dest", True):
                for sid, c in cols.items():
                    ids = [i for i in range(c.npoly) if c.kind(i) in ("wall_tall", "wall_mid") and c.poly(i)[0].min() > -3500 and _poly_dist(poly, (c.poly(i)[0].mean(), c.poly(i)[1].mean())) <= r + 30]
                    if ids: remove_polys(c, ids)
            for sid, rc in td.cols.items():
                for i in range(rc.npoly):
                    kd = rc.kind(i)
                    if kd not in ("wall_tall", "wall_mid"): continue
                    x, y, z = rc.poly(i)
                    if x.min() < -3500 or _poly_dist(poly, (x.mean(), y.mean())) > r: continue
                    key = (kd, tuple(np.round(x, 1)), tuple(np.round(y, 1)))
                    if key in seen: continue
                    seen.add(key); raw = bytearray(rc.polys[i * 32:i * 32 + 32]); raw[0] = 0; raw = bytes(raw)          # group byte 0 = always active (group numbers belong to the SOURCE region's barrier table)
                    for dsid in topo.collision_sections(cols, [(x.mean(), y.mean()), (x[0], y[0]), (x[2], y[2])]):
                        c = cols[dsid]; r_ = _grid_rect(c)
                        if x.min() < r_[0] or x.max() > r_[1] or y.min() < r_[2] or y.max() > r_[3]: skipped += 1; continue
                        slots = free_slots(c, "tall" if kd == "wall_tall" else "mid")
                        if not slots: skipped += 1; continue
                        sl = slots[0]; c.polys[sl * 32:sl * 32 + 32] = raw; L = _lists(c)
                        for ci, m in _masks(c, list(x), list(y)): L[ci].append((sl, m))
                        _store_lists(c, L); n_new += 1
            report.append(f"import_walls from {op['src']}: {len(seen)} wall polygons ({n_new} section copies written, {skipped} skipped: no free wall slot in that section)")
        elif k == "paste":
            assert getdata is not None, "paste needs the other region's data"
            td = getdata(op["src"]); qx, qy, qz, qw = op.get("quat", [0, 0, 0, 1]); sc = np.array(op.get("scl", [1, 1, 1]), float)
            Rm = np.array([[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)], [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)], [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])
            cen = np.array(op["centroid"], float); P = np.array(op["pos"], float); ntri = 0; imported = []
            have = {kk for pk in packs for kk in pk.recs}
            for it in op["items"]:
                for key, pos, uv, tris in td.item_geometry(it["kind"], it["id"]):
                    if key and key not in have:                               # bring the texture across
                        spk = td.keymap.get(key)
                        if spk is not None:
                            from PIL import Image
                            r_ = spk.recs[key]; img = Image.fromarray(np.ascontiguousarray(tex.decode(spk, key)), "RGBA"); fmt_ = 0x24 if r_["fmt"] == 0x24 else 0x22; w_, h_ = r_["w"], r_["h"]
                            avail = sorted({(r["w"], r["h"]) for pk in packs for r in pk.recs.values() if r["fmt"] == fmt_}, key=lambda d: abs(d[0] - w_) + abs(d[1] - h_))
                            if (w_, h_) not in avail and avail: w_, h_ = avail[0]
                            try: tex.add_texture(packs, r_["name"], img, w_, h_, fmt_, key); have.add(key); imported.append(r_["name"])
                            except AssertionError as ex: report.append(f"paste: could not import texture {r_['name']}: {ex}"); key = 0
                    w = ((pos - cen) * sc) @ Rm.T + P
                    for a in range(0, len(tris), 600):                        # chunks of <=600 triangles per hosted primitive
                        t = tris[a:a + 600]; used, inv = np.unique(t.ravel(), return_inverse=True)
                        verts = [(float(w[u][0]), float(w[u][1]), float(w[u][2]), float(uv[u][0]), float(uv[u][1])) for u in used]
                        idx = inv.tolist(); idx += [idx[i] for i in range(0, len(idx), 3) for i in (i, i + 2, i + 1)]       # both windings
                        prims.append(host.Prim(verts, idx, 0xFFB0B0B0, key if key in have else 0)); ntri += len(t)
            report.append(f"paste from {op['src']}: {len(op['items'])} objects, {ntri} triangles (double-sided), {len(set(imported))} texture(s) imported: {sorted(set(imported))[:6]}")
            if op.get("collision", True) and op.get("size"):
                sz = np.array(op["size"], float); hx, hy = sz[0] / 2 + 0.5, sz[1] / 2 + 0.5
                fp_src = np.array([[cen[0] - hx, cen[1] - hy, cen[2]], [cen[0] + hx, cen[1] - hy, cen[2]], [cen[0] + hx, cen[1] + hy, cen[2]], [cen[0] - hx, cen[1] + hy, cen[2]]])
                fp = (((fp_src - cen) * sc) @ Rm.T + P)[:, :2]; flip = np.linalg.det(Rm @ np.diag(sc)) < 0
                def in_fp(px, py, fp=fp):
                    sg = 0
                    for a in range(4):
                        b_ = (a + 1) % 4; cr = (fp[b_][0] - fp[a][0]) * (py - fp[a][1]) - (fp[b_][1] - fp[a][1]) * (px - fp[a][0])
                        if abs(cr) < 1e-9: continue
                        s2 = 1 if cr > 0 else -1
                        if sg == 0: sg = s2
                        elif sg != s2: return False
                    return True
                gq = []; wl = 0; seen_ = set()
                for ssid, sc_ in td.cols.items():
                    if sc_.ox > cen[0] + hx or sc_.ox + sc_.W * 32 < cen[0] - hx or sc_.oy > cen[1] + hy or sc_.oy + sc_.H * 32 < cen[1] - hy: continue
                    for i in range(sc_.npoly):
                        if _parked(sc_, i): continue
                        x, y, z = sc_.poly(i)
                        if x.min() < -3500 or not (cen[0] - hx <= x.mean() <= cen[0] + hx and cen[1] - hy <= y.mean() <= cen[1] + hy): continue
                        kd = sc_.kind(i); key_ = (kd, tuple(np.round(x, 1)), tuple(np.round(y, 1)), tuple(np.round(z, 1)))
                        if key_ in seen_: continue
                        seen_.add(key_); w = ((np.stack([x, y, z], 1) - cen) * sc) @ Rm.T + P
                        if kd == "ground":
                            if abs((w[2][0] - w[0][0]) * (w[3][1] - w[1][1]) - (w[2][1] - w[0][1]) * (w[3][0] - w[1][0])) / 2 > 0.3: gq.append((w[:, :2].copy(), w[:, 2].copy(), [(0, 0), (1, 0), (1, 1), (0, 1)]))
                        else:
                            ops.append({"op": "add_wall", "region": op.get("region", "L4RA"), "sid": None, "kind": "tall" if kd == "wall_tall" else "mid", "p0": [float(w[0][0]), float(w[0][1])], "p1": [float(w[2][0]), float(w[2][1])], "z": [float(w[:, 2].min()), float(w[:, 2].max())], "sides": "back" if flip else "front", "_from_paste": True}); wl += 1
                # the pasted area REPLACES whatever collision walls stood there
                nrm = 0
                for sid_, c_ in cols.items():
                    ids = [i for i in range(c_.npoly) if c_.kind(i) in ("wall_tall", "wall_mid") and not _parked(c_, i) and c_.poly(i)[0].min() > -3500 and in_fp(c_.poly(i)[0].mean(), c_.poly(i)[1].mean())]
                    if ids: remove_polys(c_, ids); nrm += len(ids)
                if gq: ground.append((gq, in_fp))
                report.append(f"paste collision: {len(gq)} ground polygon(s) and {wl} wall polygon(s) copied from {op['src']}; {nrm} existing destination wall polygon(s) in that area replaced")
        elif k == "paste_collision":
            qx, qy, qz, qw = op.get("quat", [0, 0, 0, 1]); sc = np.array(op.get("scl", [1, 1, 1]), float)
            Rm = np.array([[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)], [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)], [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]])
            cen = np.array(op["centroid"], float); P = np.array(op["pos"], float); flip = np.linalg.det(Rm @ np.diag(sc)) < 0; gq = []; wl = 0
            for pg in op["polys"]:
                w = ((np.stack([pg["x"], pg["y"], pg["z"]], 1) - cen) * sc) @ Rm.T + P
                if pg["k"] == "ground":
                    if abs((w[2][0] - w[0][0]) * (w[3][1] - w[1][1]) - (w[2][1] - w[0][1]) * (w[3][0] - w[1][0])) / 2 > 0.3: gq.append((w[:, :2].copy(), w[:, 2].copy(), [(0, 0), (1, 0), (1, 1), (0, 1)]))
                else:
                    ops.append({"op": "add_wall", "region": op.get("region", "L4RA"), "sid": None, "kind": "tall" if pg["k"] == "wall_tall" else "mid", "p0": [float(w[0][0]), float(w[0][1])], "p1": [float(w[2][0]), float(w[2][1])], "z": [float(w[:, 2].min()), float(w[:, 2].max())], "sides": "back" if flip else "front"}); wl += 1
            def in_quads(px, py, gq=gq):
                for cq, zq, _ in gq:
                    sg = 0; okq = True
                    for a in range(4):
                        b_ = (a + 1) % 4; cr = (cq[b_][0] - cq[a][0]) * (py - cq[a][1]) - (cq[b_][1] - cq[a][1]) * (px - cq[a][0])
                        if abs(cr) < 1e-9: continue
                        s2 = 1 if cr > 0 else -1
                        if sg == 0: sg = s2
                        elif sg != s2: okq = False; break
                    if okq: return True
                return False
            if gq: ground.append((gq, in_quads))
            report.append(f"paste_collision: {len(gq)} ground polygon(s) and {wl} wall polygon(s) pasted")
        elif k == "copy_from_region":
            report.append(f"copy_from_region ({len(op.get('items', []))} objects from {op.get('src')}): PREVIEW ONLY - not written into the map yet")
        elif k == "add_texture":
            import base64, io
            from PIL import Image
            key = tex.add_texture(packs, op["name"], Image.open(io.BytesIO(base64.b64decode(op["image"]))), int(op["w"]), int(op["h"]), int(op.get("fmt", 0x22)), op.get("key"))
            report.append(f"added NEW texture {op['name']} ({op['w']}x{op['h']}, key {key})")
        elif k == "set_texture":
            oid = op["id"]
            if isinstance(oid, str):                      # a placed prop ('i123'): its textures belong to the shared model, so edit that model (every copy changes)
                oid = getdata(op.get("region", "L4RA")).inst[int(oid.lstrip("i"))][3]
            o = objs[oid]; sn = next((q for q in o.node.kids if q.magic == 0x134012), None); n = 0
            if sn:
                d = bytearray(sn.data); f = fill(d)
                for i in range(f, len(d) - 7, 8):
                    if struct.unpack_from("<I", d, i)[0] == op["from_key"]: struct.pack_into("<I", d, i, op["to_key"]); n += 1
                sn.data = bytes(d)
            report.append(f"texture on {o.name}: {n} slot(s) changed")
        elif k == "replace_texture":
            from PIL import Image; import io, base64
            im = Image.open(io.BytesIO(base64.b64decode(op["image"]))); n = 0; nm = op["key"]
            for pk in packs:
                if op["key"] in pk.recs: tex.replace_texture(pk, op["key"], im); n += 1; nm = pk.recs[op["key"]]["name"]
            report.append(f"replaced texture {nm} in {n} pack(s)")
        elif k == "remove_polys":
            remove_polys(cols[op["sid"]], op["polys"]); report.append(f"deleted {len(op['polys'])} collision polygon(s) in section {op['sid']}")
        elif k == "add_wall":
            sids = ({op["sid"]} if op.get("sid") is not None else set()) | (set(topo.collision_sections(cols, [tuple(op["p0"]), tuple(op["p1"]), ((op["p0"][0] + op["p1"][0]) / 2, (op["p0"][1] + op["p1"][1]) / 2)])) if topo is not None else set())
            for sid in sorted(sids):
                c_ = cols[sid]; need = 2 if op.get("sides", "both") == "both" else 1; kk = "tall" if op["kind"] == "tall" else "mid"; mid_pt = ((op["p0"][0] + op["p1"][0]) / 2, (op["p0"][1] + op["p1"][1]) / 2)
                have = len(free_slots(c_, kk))
                if have < need and op.get("reuse", False):
                    # no free slot: repurpose the NEAREST existing walls of the same kind - they are the likeliest to be in the way / obsolete (the note names each one)
                    cand = sorted((float(np.hypot(c_.poly(i)[0].mean() - mid_pt[0], c_.poly(i)[1].mean() - mid_pt[1])), i) for i in range(*_wall_run(c_, kk)) if c_.kind(i) == f"wall_{kk}" and c_.poly(i)[0].min() > -3500 and not _parked(c_, i))
                    take = cand[:need - have]
                    if take:
                        remove_polys(c_, [i for _, i in take])
                        report.append(f"NOTE: section {sid} had no free {kk}-wall slot, so I REPLACED {len(take)} existing {kk} wall polygon(s) nearest to your wall: " + ", ".join(f"#{i} at ({c_.poly(i)[0].mean():.0f},{c_.poly(i)[1].mean():.0f}) {d:.0f} m away" for d, i in take))
                try: ii = add_wall(c_, op["kind"], op["p0"], op["p1"], op.get("z", [30, 34]), op.get("sides", "both")); report.append(f"added {op['kind']} wall (polys {ii}) in section {sid}")
                except AssertionError as e:
                    other = "mid" if op["kind"] == "tall" else "tall"
                    try:
                        zz = op.get("z", [30, 34]); ii = add_wall(c_, other, op["p0"], op["p1"], zz, op.get("sides", "both")); report.append(f"NOTE: section {sid} has no free {op['kind']}-wall slot (and none nearby to replace), so this wall was built as a {other} wall instead (polys {ii})")
                    except AssertionError as e2:
                        if op.get("best_effort"): report.append(f"SKIPPED wall in section {sid}: {e2}"); continue
                        raise AssertionError(f"{e} ({other} walls are full too: {e2}). Delete a few existing walls near this spot (select collision polygons and press Delete) to free slots.")
        elif k == "add_box":
            for c_, z_, col in box_quads(op["center"], op["size"], op["quaternion"], op.get("color", 0xFFB0B0B0)): prims.append(host.quad_prim(c_, z_, col, op.get("key", 0)))
            report.append("added box (6 quads)")
        elif k in ("add_road", "add_quad"):
            if k == "add_road" and op.get("clear") and getdata is not None:
                np_, nw_ = clear_corridor(tree, cols, getdata(op.get("region", "L4RA")), op["points"], op["width"] / 2 + op.get("clear_margin", 5.0)); report.append(f"cleared the corridor: {np_} placed models parked, {nw_} collision walls removed")
            qs = road_quads(op) if k == "add_road" else [(np.array([p[:2] for p in op["corners"]], float), np.array([p[2] for p in op["corners"]], float), op.get("uv") or [(0, 0), (1, 0), (1, 1), (0, 1)])]
            _n0 = len(prims)
            if op.get("visual", True) is False: pass
            elif op.get("double"):
                for corners, z, uv in qs: prims.append(host.quad_prim(corners, z, op.get("color", 0xFFB0B0B0), op.get("key", 0), uv))
            elif k == "add_road": prims.extend(host.ribbon_prims(qs, op.get("color", 0xFFB0B0B0), op.get("key", 0)))
            else:
                for corners, z, uv in qs: prims.append(host.quad_prim1(corners, z, op.get("color", 0xFFB0B0B0), op.get("key", 0), uv))
            if op.get("collision"): ground.append((qs, op.get("donor_box"))); soft.extend(prims[_n0:])
            report.append(f"{k}: {len(qs)} quad(s)" + (" + solid ground" if op.get("collision") else ""))
        elif k == "add_mesh":
            T = op["tris"]; U = op.get("uvs") or [[0, 0, 1, 0, 0, 1]] * len(T)
            for t, u in zip(T, U): prims.append(host.tri_prim([t[0:3], t[3:6], t[6:9]], [u[0:2], u[2:4], u[4:6]], op.get("color", 0xFFB0B0B0), op.get("key", 0), op.get("double", True)))
            report.append(f"add_mesh: {len(T)} triangle(s)")
    if prims:
        _bad = [h for h in freed if not re.search(r"TERRAIN|ROAD", h.name)]
        if _bad: report.append(f"NOTE: not hosting new surfaces into {len(_bad)} deleted object(s) that are not terrain/road pieces (e.g. {_bad[0].name}); the game crashes when it draws surfaces hosted in tunnel/other objects")
        pool = [h for h in freed if re.search(r"TERRAIN|ROAD", h.name)]
        td_h = getdata(next((o.get("region", "L4RA") for o in ops if o.get("region")), "L4RA")) if getdata is not None else None
        # A hosted surface is only drawn while the STREAMED SECTION of its host object is loaded, i.e. while the player is near the HOST: so every new surface must be hosted by an
        # object close to where the surface is. Group the primitives by place and give every group its own nearby hosts.
        clusters = []
        for p_ in prims:
            vx = float(np.mean([v[0] for v in p_.verts])); vy = float(np.mean([v[1] for v in p_.verts]))
            for cl in clusters:
                if np.hypot(cl["x"] - vx, cl["y"] - vy) < 250: cl["p"].append(p_); n_ = len(cl["p"]); cl["x"] += (vx - cl["x"]) / n_; cl["y"] += (vy - cl["y"]) / n_; break
            else: clusters.append({"x": vx, "y": vy, "p": [p_]})
        def _ctr(h): lo, hi, _c = td_h.meta[objs.index(h.obj)]; return ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
        auto_all = []
        for cl in clusters:
            near = sorted(((float(np.hypot(_ctr(h)[0] - cl["x"], _ctr(h)[1] - cl["y"])), h) for h in pool), key=lambda t: t[0]) if td_h is not None else [(0.0, h) for h in pool]
            trial = [h for d, h in near if d < 500]; done = False
            if trial:
                try:
                    plan = host.layout(trial, cl["p"]); used = [h for h, _, _ in plan]; rep = host.write_prims(used, cl["p"]); pool = [h for h in pool if all(h is not u for u in used)]; done = True
                    report.append(f"hosted {len(cl['p'])} primitive(s) near ({cl['x']:.0f},{cl['y']:.0f}) into deleted objects: {rep}")
                except AssertionError: pass
            if done: continue
            if td_h is None: raise AssertionError("not enough host objects: delete one more object (each host takes up to 16 textures)")
            # no usable deleted object near this group: delete the nearest suitable terrain/road pieces and use their room
            taken = {id(h.obj) for h in freed}; cand = []
            for oi, (lo, hi, cat) in td_h.meta.items():
                if cat not in ("terrain", "road") or max(hi[0] - lo[0], hi[1] - lo[1]) > 260: continue
                try: o = objs[oi]
                except (KeyError, IndexError): continue
                pl = pm.get(id(o))
                if not re.search(r"TERRAIN|ROAD", o.name) or id(o) in taken or pl is None or o.capacity <= 0: continue
                cand.append((float(np.hypot((lo[0] + hi[0]) / 2 - cl["x"], (lo[1] + hi[1]) / 2 - cl["y"])), oi))
            cand.sort(); trial = []; auto = []; ok = False
            for dist, oi in cand[:24]:
                o = objs[oi]; cap = o.capacity; pl = pm.get(id(o)); remove_object(o); h = host.Host(o, pl, cap); freed.append(h); trial.append(h); auto.append((o.name, oi, round(dist)))
                try: rep = host.write_prims(trial, cl["p"]); ok = True; break
                except AssertionError: continue
            if not ok: raise AssertionError(f"could not find a free object near ({cl['x']:.0f},{cl['y']:.0f}) to carry the new surfaces")
            auto_all += auto
            report.append(f"hosted {len(cl['p'])} primitive(s) near ({cl['x']:.0f},{cl['y']:.0f}) after AUTOMATICALLY deleting {len(auto)} nearby object(s): {auto} (delete or keep these yourself in the viewer to choose which)")
    for qs, dbox in ground:
        qm = merge_quads(qs) if len(qs) > 1 else qs; by = {}
        for q in qm:
            ex = [(q[0][i] + q[0][(i + 1) % 4]) / 2 for i in range(4)]
            pts_ = [tuple(v) for v in q[0]] + [tuple(q[0].mean(0))] + [tuple(v) for v in ex]
            sids = topo.collision_sections(cols, pts_) if topo is not None else []
            if not sids: c = section_for(cols, *q[0].mean(0)); assert c is not None, "no collision section covers that road"; sids = [c.sid]
            zq = np.broadcast_to(np.asarray(q[1], float), (4,))
            for sid in sids:
                ensure_grid(cols[sid], float(q[0][:, 0].min()) - 0.5, float(q[0][:, 0].max()) + 0.5, float(q[0][:, 1].min()) - 0.5, float(q[0][:, 1].max()) + 0.5); by.setdefault(sid, []).append((q[0], zq, q[2]))
        for sid, lst in by.items():
            report.append(f"solid ground: {add_ground(cols[sid], lst, prefer_box=dbox)} polygon(s) in section {sid} (road merged from {len(qs)} quads)")
            report += ground_conflicts(cols[sid], cols[sid]._new_slots)
    for c in cols.values():
        if getattr(c, "_dirty", False): refresh_col_bounds(c)
        world.store_collision(c)
    tex.commit(packs); return report
