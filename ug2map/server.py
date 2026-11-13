"""Whole-map data server for the viewer: parses the track once, then serves 256 m tiles, an overview, route nodes and drive traces."""
from __future__ import annotations
import base64, glob, json, math, os, struct, threading
import numpy as np
from . import bun, world, tex

TILE = 256.0
def b64(a): return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
def category(name):
    n = name.upper()
    if n.startswith("PAN_") or n == "SKYDOME": return "backdrop"
    if n.startswith("XB_"): return "buildings"
    if n.startswith("XW_"): return "walls"
    if n.startswith("SGN_"): return "signs"
    if n.startswith("XO_") or n.startswith("XU_") or n.startswith("ZPM_") or "PROPS" in n: return "props"
    if "RDP" in n or "ROAD" in n or "CONCRETE" in n: return "road"
    if n.startswith("TRN_"): return "terrain"
    return "other"
CATS = ["road", "terrain", "buildings", "walls", "props", "signs", "other"]
def tris_from_list(idx):
    t = idx[: len(idx) // 3 * 3].reshape(-1, 3); ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2]); return t[ok].astype(np.uint32)

from . import edit as _edit

class TrackData:
    def __init__(self, tracks_dir, compare_dir=None, track="L4RA", log=print):
        self.dir = tracks_dir; self.track = track; self.lock = threading.Lock(); self.cache = {}
        log("parsing STREAM%s.BUN ..." % track)
        self.tree = bun.parse(open(os.path.join(tracks_dir, f"STREAM{track}.BUN"), "rb").read())
        self.objs = world.load_objects(self.tree); self.cols = world.load_collisions(self.tree)
        self.packs = tex.load_packs(self.tree); self.keymap = {}
        for pk in self.packs:
            for k in pk.recs: self.keymap.setdefault(k, pk)
        log("indexing %d objects ..." % len(self.objs))
        self.meta = {}
        for oi, o in enumerate(self.objs):
            if 0x134B01 not in o.ch or 0x134B03 not in o.ch: continue
            lo, hi = o.bounds()
            if lo is None: continue
            sz = hi - lo; back = max(sz[0], sz[1]) > 450 or sz[2] > 160
            self.meta[oi] = (lo, hi, "backdrop" if back else category(o.name))
        self.bcache = {}; self._build_instances()
        self.changed_obj = set(); self.changed_poly = {}; self.changed_inst = set()
        if compare_dir and os.path.exists(os.path.join(compare_dir, f"STREAM{track}.BUN")):
            log("comparing with original ...")
            t0 = bun.parse(open(os.path.join(compare_dir, f"STREAM{track}.BUN"), "rb").read()); o0 = world.load_objects(t0); c0 = world.load_collisions(t0)
            for oi, (a, b) in enumerate(zip(o0, self.objs)):
                if a.ch.get(0x134B01) and b.ch.get(0x134B01) and (a.ch[0x134B01].data != b.ch[0x134B01].data or a.ch[0x134B03].data != b.ch[0x134B03].data): self.changed_obj.add(oi)
            self.changed_inst = set(); sec0 = [n for n in t0 if n.magic == 0x80034100]
            for ii, (si, row, j, oi, R, T, aabb) in enumerate(self.inst):
                if si < len(sec0) and (sec0[si].kids is not None):
                    pa = next(c for c in sec0[si].kids if c.magic == 0x34103).data; pb = next(c for c in self.secs[si].kids if c.magic == 0x34103).data
                    if pa[j:j + 64] != pb[j:j + 64]: self.changed_inst.add(ii)
            for sid, c in self.cols.items():
                o = c0.get(sid)
                if o is None: continue
                ch = [i for i in range(min(c.npoly, o.npoly)) if c.polys[i * 32:i * 32 + 32] != o.polys[i * 32:i * 32 + 32]]
                if ch: self.changed_poly[sid] = set(ch)
            log("changed objects: %d, sections with changed collision: %d" % (len(self.changed_obj), len(self.changed_poly)))
        self.routes = self._routes()
        self.bounds = self._bounds()
        log("ready: x %.0f..%.0f  y %.0f..%.0f" % tuple(self.bounds))
    @staticmethod
    def _key_of(o):
        hd = next(k for k in o.node.kids if k.magic == 0x134011); return struct.unpack_from("<I", hd.data, world.fill(hd.data) + 0x10)[0]
    def _build_instances(self):
        """every placement (0x34103, 64 B): AABB, type, translation @+0x20, 3x3 i16/8192 @+0x2c (row-vector convention: v' = v @ R + T). Verified on 99.96% of 48k placements."""
        keymap = {}
        for i, o in enumerate(self.objs): keymap.setdefault(self._key_of(o), i)
        self.secs = [n for n in self.tree if n.magic == 0x80034100]; self.inst = []; AB = []
        for si, n in enumerate(self.secs):
            k = {c.magic: c for c in n.kids}; q, p = k[0x34102].data, k[0x34103].data; qo, po = len(q) % 68, len(p) % 64
            types = [struct.unpack_from("<3I", q, i + 0x20) for i in range(qo, len(q) - 67, 68)]
            for row, j in enumerate(range(po, len(p) - 63, 64)):
                ti, fl = struct.unpack_from("<HH", p, j + 0x18)
                if ti >= len(types): continue
                oi = next((keymap[kk] for kk in types[ti] if kk in keymap), None)
                if oi is None or oi not in self.meta: continue
                aabb = struct.unpack_from("<6f", p, j)
                if not np.isfinite(aabb).all(): continue
                R = np.array(struct.unpack_from("<9h", p, j + 0x2c), np.float64).reshape(3, 3) / 8192.0; T = np.array(struct.unpack_from("<3f", p, j + 0x20), np.float64)
                self.inst.append((si, row, j, oi, R, T, aabb)); AB.append(aabb)
        self.AB = np.array(AB, np.float32); self.pkeymap = keymap; self.refd = {x[3] for x in self.inst}
    @staticmethod
    def _mat(R, T):
        return [float(v) for v in (R[0][0], R[0][1], R[0][2], 0, R[1][0], R[1][1], R[1][2], 0, R[2][0], R[2][1], R[2][2], 0, T[0], T[1], T[2], 1)]
    def _inst_cat(self, ii):
        si, row, j, oi, R, T, aabb = self.inst[ii]; sz = np.array(aabb[3:]) - np.array(aabb[:3])
        if max(sz[0], sz[1]) > 450 or sz[2] > 160 or self.meta[oi][2] == "backdrop": return "backdrop"
        return category(self.objs[oi].name)
    def _routes(self):
        import glob as g; pts = {}; rd = os.path.join(self.dir, f"ROUTES{self.track}")
        for f in sorted(g.glob(os.path.join(rd, "Paths4*.bin"))):
            try: t = bun.parse(open(f, "rb").read())
            except Exception: continue
            for top in t:
                if top.magic != 0x80034147: continue
                for k in top.kids:
                    if k.magic != 0x34148: continue
                    for i in range(0, len(k.data) - 23, 24):
                        x, y = struct.unpack_from("<2f", k.data, i)
                        if np.isfinite([x, y]).all() and abs(x) < 6000 and abs(y) < 6000: pts[(round(x), round(y))] = (x, y)
        return np.array(list(pts.values()), np.float32)
    def item_geometry(self, kind, id):
        """world-space geometry of one placed model ('inst') or baked object ('obj'): [(texture key, pos Nx3, uv Nx2, tris Mx3)]"""
        import base64
        if kind == "inst":
            si, row, j, oi, R, T, aabb = self.inst[int(id)]; e = self._proto(oi)
        else: oi = int(id); e = self._baked(oi, self.meta[oi][2])
        if e is None: return []
        pos = np.frombuffer(base64.b64decode(e["pos"]), "<f4").reshape(-1, 3).astype(np.float64); uv = np.frombuffer(base64.b64decode(e["uv"]), "<f4").reshape(-1, 2).astype(np.float64)
        if kind == "inst": pos = pos @ R + T
        return [(g["key"], pos, uv, np.frombuffer(base64.b64decode(g["idx"]), "<u4").reshape(-1, 3).astype(np.int64)) for g in e["groups"]]
    def clip(self, items):
        """preview payload for the viewer: per item, groups with world positions (b64 f32) ..."""
        out = []
        for it in items:
            for key, pos, uv, tris in self.item_geometry(it["kind"], it["id"]):
                out.append({"item": it["id"], "kind": it["kind"], "key": key, "pos": b64(pos.astype(np.float32)), "uv": b64(uv.astype(np.float32)), "idx": b64(tris.astype(np.uint32).ravel())})
        return out
    def ground_z(self, x, y):
        """highest ground-collision surface under (x, y), or None"""
        best = None
        for sid, c in self.cols.items():
            if not (c.ox <= x <= c.ox + c.W * 32 and c.oy <= y <= c.oy + c.H * 32): continue
            for i in range(c.npoly):
                px, py, pz = c.poly(i)
                if px.min() > x or px.max() < x or py.min() > y or py.max() < y or c.kind(i) != "ground": continue
                for tri in ((0, 1, 2), (0, 2, 3)):
                    A, B, C = [(px[k], py[k], pz[k]) for k in tri]; d = (B[1] - C[1]) * (A[0] - C[0]) + (C[0] - B[0]) * (A[1] - C[1])
                    if abs(d) < 1e-9: continue
                    w0 = ((B[1] - C[1]) * (x - C[0]) + (C[0] - B[0]) * (y - C[1])) / d; w1 = ((C[1] - A[1]) * (x - C[0]) + (A[0] - C[0]) * (y - C[1])) / d; w2 = 1 - w0 - w1
                    if min(w0, w1, w2) >= -1e-6:
                        z = w0 * A[2] + w1 * B[2] + w2 * C[2]
                        if best is None or z > best: best = float(z)
        return best
    def ground_z_near(self, x, y):
        z = self.ground_z(x, y)
        if z is not None: return z
        import math
        for r in (4, 8, 12, 20, 30):
            for k in range(8):
                z = self.ground_z(x + r * math.cos(k * math.pi / 4), y + r * math.sin(k * math.pi / 4))
                if z is not None: return z
        return None
    def textures(self):
        out = []; seen = set()
        for pi, pk in enumerate(self.packs):
            for k, r in pk.recs.items():
                if k in seen: continue
                seen.add(k); out.append([k, r["name"], r["w"], r["h"], r["fmt"], pi])
        return out
    def thumb(self, key, size=64):
        c = self.__dict__.setdefault("_thumbs", {})
        if (key, size) in c: return c[(key, size)]
        pk = self.keymap.get(key)
        if pk is None: return None
        from PIL import Image; import io
        im = Image.fromarray(np.ascontiguousarray(tex.decode(pk, key)), "RGBA"); im.thumbnail((size, size)); b = io.BytesIO(); im.save(b, "PNG"); c[(key, size)] = b.getvalue(); return c[(key, size)]
    def search(self, q, limit=300):
        q = q.lower(); out = []
        for ii, (si, row, j, oi, R, T, aabb) in enumerate(self.inst):
            n = self.objs[oi].name
            if q in n.lower(): out.append([ii, n, self._inst_cat(ii), float((aabb[0] + aabb[3]) / 2), float((aabb[1] + aabb[4]) / 2)])
            if len(out) >= limit: break
        return out
    def catalog(self):
        """every placed model name with its category, how often it is placed and one example (for the catalogue panel)"""
        out = {}
        for ii, (si, row, j, oi, R, T, aabb) in enumerate(self.inst):
            cat = self._inst_cat(ii)
            if cat.startswith("bd_") or cat in ("backdrop", "terrain", "road"): continue
            n = self.objs[oi].name; e = out.setdefault(n, {"name": n, "cat": cat, "count": 0, "example": ii, "x": float((aabb[0] + aabb[3]) / 2), "y": float((aabb[1] + aabb[4]) / 2)}); e["count"] += 1
        return sorted(out.values(), key=lambda e: (e["cat"], e["name"]))
    def to_obj(self, items, title="UG2 MapKit export"):
        """Wavefront OBJ of placed models / baked objects in WORLD coordinates (Z up). Materials are written as `usemtl k<texture key>` so the import can put the textures back."""
        L = [f"# {title}", "# import in Blender with Forward = Y, Up = Z; export again with the same axes and Selection Only"]; base = 1; vt0 = 1
        for n, it in enumerate(items):
            for key, pos, uv, tris in self.item_geometry(it["kind"], it["id"]):
                L.append(f"o {it.get('name') or 'item'}_{n}"); L.append(f"usemtl k{key}")
                L += [f"v {p[0]:.4f} {p[1]:.4f} {p[2]:.4f}" for p in pos]; L += [f"vt {u[0]:.5f} {1 - u[1]:.5f}" for u in uv]
                L += [f"f {t[0] + base}/{t[0] + vt0} {t[1] + base}/{t[1] + vt0} {t[2] + base}/{t[2] + vt0}" for t in tris]
                base += len(pos); vt0 += len(uv)
        return "\n".join(L) + "\n"
    @staticmethod
    def backdrop_kind(name):
        n = name.upper()
        if n.startswith("SKYDOME") or "CLOUD" in n: return "sky"
        if "HILL" in n or n.startswith("TRN_SH_"): return "hills"
        if n.startswith("PAN_"): return "skyline"
        return "other"
    def backdrop(self, kind="all"):
        """backdrop (sky dome, hills, skyline panoramas, far buildings) as PLACED models: {instances, protos, objects}"""
        out = {"instances": [], "protos": {}, "objects": []}
        for ii in range(len(self.inst)):
            si, row, j, oi, R, T, aabb = self.inst[ii]
            if self._inst_cat(ii) != "backdrop": continue
            k = self.backdrop_kind(self.objs[oi].name)
            if kind not in ("all", k): continue
            pe = self._proto(oi)
            if pe is None: continue
            out["protos"].setdefault(oi, pe)
            out["instances"].append({"id": ii, "proto": oi, "name": self.objs[oi].name, "cat": "bd_" + k, "m": self._mat(R, T), "aabb": [float(v) for v in aabb], "changed": False, "sec": si, "row": row, "off": j})
        for oi, m in self.meta.items():
            if m[2] == "backdrop" and oi not in self.refd:
                k = self.backdrop_kind(self.objs[oi].name)
                if kind in ("all", k):
                    e = self._baked(oi, "backdrop")
                    if e: e = dict(e); e["cat"] = "bd_" + k; out["objects"].append(e)
        return out
    def sections(self): return [{"sid": s, "grid": [c.ox, c.oy, c.W, c.H]} for s, c in self.cols.items()]
    def _bounds(self):
        xs = [c.ox for c in self.cols.values()] + [c.ox + c.W * 32 for c in self.cols.values()]; ys = [c.oy for c in self.cols.values()] + [c.oy + c.H * 32 for c in self.cols.values()]
        return [min(xs), max(xs), min(ys), max(ys)]
    # ---- overview (minimap) ----
    def overview(self):
        g, tw, md, ch = [], [], [], []
        for sid, c in self.cols.items():
            for i in range(c.npoly):
                x, y, z = c.poly(i)
                if x.min() < -3500: continue
                k = c.kind(i); p = (float(x.mean()), float(y.mean()))
                (g if k == "ground" else tw if k == "wall_tall" else md).append(p)
                if k == "ground" and i in self.changed_poly.get(sid, ()): ch.append(p)
        ob = []
        for ii in range(len(self.inst)):
            c_ = self._inst_cat(ii)
            if c_ != "backdrop": a = self.inst[ii][6]; ob.append(((a[0] + a[3]) / 2, (a[1] + a[4]) / 2, c_))
        cats = CATS
        return {"bounds": self.bounds, "ground": b64(np.array(g[::3], np.float32)), "tall": b64(np.array(tw[::2], np.float32)), "mid": b64(np.array(md, np.float32)),
                "changed": b64(np.array(ch, np.float32)), "routes": b64(self.routes.astype(np.float32)),
                "objects": {c: b64(np.array([(a, b) for a, b, k in ob if k == c], np.float32)) for c in cats}}
    # ---- tiles ----
    def ensure_built(self, out_dir):
        """load the collision of the BUILT track file (after Build) so the viewer can show what the game will really have"""
        fp = os.path.join(out_dir, f"STREAM{self.track}.BUN")
        try: mt = os.path.getmtime(fp)
        except OSError: return False
        if getattr(self, "_built_mt", None) == mt: return True
        t = bun.parse(open(fp, "rb").read()); cb = world.load_collisions(t); ch = {}
        for sid, c in cb.items():
            b = self.cols.get(sid)
            if b is None: ch[sid] = set(range(c.npoly)); continue
            ch[sid] = {i for i in range(c.npoly) if i >= b.npoly or bytes(c.polys[i * 32:i * 32 + 32]) != bytes(b.polys[i * 32:i * 32 + 32])}
        self.cols_built = cb; self.built_changed = ch; self._built_mt = mt; return True
    def tile(self, region, have, have_protos=(), no_tex=False, built=False):
        x0, x1, y0, y1 = region; out = {"instances": [], "protos": {}, "collision": {}, "textures": {}}; need = set(); have = set(have); hp = set(have_protos)
        hit = np.nonzero((self.AB[:, 3] >= x0) & (self.AB[:, 0] <= x1) & (self.AB[:, 4] >= y0) & (self.AB[:, 1] <= y1))[0]
        for ii in hit:
            cat = self._inst_cat(ii)
            if cat == "backdrop" or (cat in ("road", "terrain") and self.inst[ii][3] in self.meta): continue
            si, row, j, oi, R, T, aabb = self.inst[ii]; pe = self._proto(oi)
            if pe is None: continue
            if oi not in hp and oi not in out["protos"]:
                out["protos"][oi] = pe
                for gr in pe["groups"]:
                    if gr["key"]: need.add(gr["key"])
            out["instances"].append({"id": int(ii), "proto": oi, "name": self.objs[oi].name, "cat": cat, "m": self._mat(R, T), "aabb": [float(v) for v in aabb], "changed": ii in self.changed_inst or oi in self.changed_obj, "sec": si, "row": row, "off": j})
        out["objects"] = []
        for oi, (lo, hi, cat) in self.meta.items():
            if cat == "backdrop" or hi[0] < x0 or lo[0] > x1 or hi[1] < y0 or lo[1] > y1: continue
            if cat not in ("road", "terrain"):
                if oi in self.refd: continue
                if max(abs(lo[0] + hi[0]), abs(lo[1] + hi[1])) / 2 < 300: continue   # unplaced local-space model (LOD variant), would sit at the origin
            e = self._baked(oi, cat)
            if e is None: continue
            out["objects"].append(e)
            for gr in e["groups"]:
                if gr["key"]: need.add(gr["key"])
        for k in (() if no_tex else need):
            if k in have: continue
            try: out["textures"][str(k)] = tex.png_data_url(tex.decode(self.keymap[k], k), 256)
            except Exception: pass
        tris = {"ground": [], "ground_new": [], "wall_tall": [], "wall_mid": []}; pids = {k: [] for k in tris}
        _cols = self.cols_built if (built and getattr(self, "cols_built", None)) else self.cols
        for sid, c in _cols.items():
            if c.ox > x1 or c.ox + c.W * 32 < x0 or c.oy > y1 or c.oy + c.H * 32 < y0: continue
            newset = (self.built_changed.get(sid, set()) if _cols is not self.cols else self.changed_poly.get(sid, set()))
            for i in range(c.npoly):
                x, y, z = c.poly(i)
                if x.min() < -3500 or x.max() < x0 or x.min() > x1 or y.max() < y0 or y.min() > y1: continue
                if _edit._parked(c, i): continue            # deleted polygon (no cell references any more): the engine ignores it, so the viewer must not draw it
                k = c.kind(i)
                if k == "ground":
                    kk = "ground_new" if i in newset else "ground"
                    tris[kk].append([[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]], [x[0], y[0], z[0]], [x[2], y[2], z[2]], [x[3], y[3], z[3]]]); pids[kk] += [[sid, i], [sid, i]]
                else:
                    zb = (-10.0, 80.0) if k == "wall_tall" else (float(z.min()), float(z.max())); p = [[x[j], y[j]] for j in range(4)]
                    for j in range(4):
                        a, b = p[j], p[(j + 1) % 4]
                        if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-6: continue
                        tris[k].append([[a[0], a[1], zb[0]], [b[0], b[1], zb[0]], [b[0], b[1], zb[1]], [a[0], a[1], zb[0]], [b[0], b[1], zb[1]], [a[0], a[1], zb[1]]]); pids[k] += [[sid, i], [sid, i]]
        for k, l in tris.items():
            if l: out["collision"][k] = {"pos": b64(np.array(l, np.float32).reshape(-1, 3)), "pid": b64(np.array(pids[k], np.int32))}
        return out
    def _proto(self, oi):
        if oi in self.cache: return self.cache[oi]
        o = self.objs[oi]; lo, hi, _ = self.meta[oi]
        d1 = o.ch[0x134B01].data; f1 = world.fill(d1); n = (len(d1) - f1) // 24
        if n <= 0: self.cache[oi] = None; return None
        vb = np.frombuffer(d1, "<f4", n * 6, f1).reshape(-1, 6); v = np.where(np.isfinite(vb[:, :3]), vb[:, :3], 0).astype(np.float32)
        d3 = o.ch[0x134B03].data; f3 = world.fill(d3); idx = np.frombuffer(d3[f3:f3 + (len(d3) - f3) // 2 * 2], "<u2").astype(np.int64)
        slots = []; sn = next((k for k in o.node.kids if k.magic == 0x134012), None)
        if sn: sd = sn.data; sf = world.fill(sd); slots = [struct.unpack_from("<I", sd, i)[0] for i in range(sf, len(sd) - 7, 8)]
        recs = []; r2 = o.ch.get(0x134B02)
        if r2:
            rd = r2.data; rf = world.fill(rd)
            for i in range(rf, len(rd) - 59, 60):
                cnt, = struct.unpack_from("<I", rd, i + 0x0c); mat, = struct.unpack_from("<I", rd, i + 0x1c); st, = struct.unpack_from("<I", rd, i + 0x34); recs.append((st, cnt, mat))
        if not recs: recs = [(0, len(idx), 0)]
        groups = {}
        for st, cnt, mat in recs:
            t = tris_from_list(idx[st:st + cnt])
            if len(t) == 0: continue
            t = t[(t < n).all(1)]
            if len(t) == 0: continue
            key = slots[mat] if mat < len(slots) else 0
            if key not in self.keymap: key = 0
            groups.setdefault(key, []).append(t.ravel())
        if not groups: self.cache[oi] = None; return None
        uv = np.where(np.isfinite(vb[:, 4:6]), vb[:, 4:6], 0).astype(np.float32)
        okv = np.isfinite(vb[:, :3]).all(1) & (np.abs(vb[:, :2]).max(1) < 9000); lo_l, hi_l = (vb[okv, :3].min(0), vb[okv, :3].max(0)) if okv.any() else (lo, hi)
        e = {"proto": oi, "name": o.name, "cap": o.capacity, "lbox": [float(q) for q in np.concatenate([lo_l, hi_l])], "pos": b64(v), "uv": b64(uv),
             "groups": [{"key": k, "idx": b64(np.concatenate(l).astype(np.uint32))} for k, l in groups.items()]}
        self.cache[oi] = e; return e
    def _baked(self, oi, cat):
        if oi in self.bcache: return self.bcache[oi]
        o = self.objs[oi]; lo, hi, _ = self.meta[oi]
        d1 = o.ch[0x134B01].data; f1 = world.fill(d1); n = (len(d1) - f1) // 24
        if n <= 0: self.bcache[oi] = None; return None
        vb = np.frombuffer(d1, "<f4", n * 6, f1).reshape(-1, 6); v = np.where(np.isfinite(vb[:, :3]), vb[:, :3], 0).astype(np.float32)
        d3 = o.ch[0x134B03].data; f3 = world.fill(d3); idx = np.frombuffer(d3[f3:f3 + (len(d3) - f3) // 2 * 2], "<u2").astype(np.int64)
        slots = []; sn = next((k for k in o.node.kids if k.magic == 0x134012), None)
        if sn: sd = sn.data; sf = world.fill(sd); slots = [struct.unpack_from("<I", sd, i)[0] for i in range(sf, len(sd) - 7, 8)]
        recs = []; r2 = o.ch.get(0x134B02)
        if r2:
            rd = r2.data; rf = world.fill(rd)
            for i in range(rf, len(rd) - 59, 60):
                cnt, = struct.unpack_from("<I", rd, i + 0x0c); mat, = struct.unpack_from("<I", rd, i + 0x1c); st, = struct.unpack_from("<I", rd, i + 0x34); recs.append((st, cnt, mat))
        if not recs: recs = [(0, len(idx), 0)]
        groups = {}
        for st, cnt, mat in recs:
            t = tris_from_list(idx[st:st + cnt])
            if len(t) == 0: continue
            t = t[(t < n).all(1)]
            if len(t) == 0: continue
            key = slots[mat] if mat < len(slots) else 0
            if key not in self.keymap: key = 0
            groups.setdefault(key, []).append(t.ravel())
        if not groups: self.bcache[oi] = None; return None
        uv = np.where(np.isfinite(vb[:, 4:6]), vb[:, 4:6], 0).astype(np.float32)
        e = {"id": oi, "name": o.name, "cat": cat, "cap": o.capacity, "changed": oi in self.changed_obj, "bbox": [float(q) for q in np.concatenate([lo, hi])],
             "pos": b64(v), "uv": b64(uv), "groups": [{"key": k, "idx": b64(np.concatenate(l).astype(np.uint32))} for k, l in groups.items()]}
        self.bcache[oi] = e; return e
    # ---- traces (drive logs recorded by tools/poslog.py) ----
    @staticmethod
    def trace_files(d): return sorted(os.path.basename(f) for f in glob.glob(os.path.join(d, "pos*.txt")))
    @staticmethod
    def trace(d, name):
        pts = []; jumps = []; prev = None
        for l in open(os.path.join(d, os.path.basename(name))):
            s = l.split()
            if len(s) != 4: continue
            try: t, x, y, z = s[0], float(s[1]), float(s[2]), float(s[3])
            except ValueError: continue
            if abs(x) < 1 and abs(y) < 1: continue
            if prev and math.hypot(x - prev[0], y - prev[1]) > 12 and abs(x) < 4000: jumps.append([prev[0], prev[1], prev[2], x, y, z, t])
            pts.append([x, y, z]); prev = (x, y, z)
        return {"pts": pts[::2], "jumps": jumps, "n": len(pts)}
