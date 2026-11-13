"""Build a viewer scene (JSON) for a region of the track: visual meshes, collision polygons, route nodes."""
from __future__ import annotations
import base64, glob, os, struct
import numpy as np
from . import bun, world, tex

def b64(a): return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()

def category(name: str) -> str:
    n = name.upper()
    if "ROAD" in n: return "road"
    if "TERRAIN" in n: return "terrain"
    if "PROPS" in n: return "props"
    return "other"

def strip_to_tris(idx: np.ndarray) -> np.ndarray:
    """0x134B03 is a TRIANGLE LIST (index counts are 3k or 3k+1; verified on roads/terrain/props). Name kept for callers."""
    t = idx[: len(idx) // 3 * 3].reshape(-1, 3)
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    return t[ok].astype(np.uint32).ravel()

TEX_SIDE = 128

def build_scene(tracks_dir: str, region, track="L4RA"):
    x0, x1, y0, y1 = region
    tree = bun.parse(open(os.path.join(tracks_dir, f"STREAM{track}.BUN"), "rb").read())
    objs = world.load_objects(tree); out = {"region": list(region), "objects": [], "collision": [], "routes": [], "stats": {}}
    packs = tex.load_packs(tree); keymap = {}
    for pk in packs:
        for k in pk.recs: keymap.setdefault(k, pk)
    need = set()
    for oi, o in enumerate(objs):
        if 0x134B01 not in o.ch or 0x134B03 not in o.ch: continue
        lo, hi = o.bounds()
        if lo is None or hi[0] < x0 or lo[0] > x1 or hi[1] < y0 or lo[1] > y1: continue
        d1 = o.ch[0x134B01].data; f1 = world.fill(d1); n = (len(d1) - f1) // 24
        if n <= 0: continue
        vb = np.frombuffer(d1, "<f4", n * 6, f1).reshape(-1, 6); v = vb[:, :3]
        d3 = o.ch[0x134B03].data; f3 = world.fill(d3); idx = np.frombuffer(d3[f3:f3 + (len(d3) - f3) // 2 * 2], "<u2").astype(np.int64)
        if len(idx) < 3: continue
        slots = []; sn = next((k for k in o.node.kids if k.magic == 0x134012), None)
        if sn:
            sd = sn.data; sf = world.fill(sd)
            slots = [struct.unpack_from("<I", sd, i)[0] for i in range(sf, len(sd) - 7, 8)]
        recs = []; r2 = o.ch.get(0x134B02)
        if r2:
            rd = r2.data; rf = world.fill(rd)
            for i in range(rf, len(rd) - 59, 60):
                cnt, = struct.unpack_from("<I", rd, i + 0x0c); mat, = struct.unpack_from("<I", rd, i + 0x1c); st, = struct.unpack_from("<I", rd, i + 0x34); recs.append((st, cnt, mat))
        if not recs: recs = [(0, len(idx), 0)]
        groups = {}
        for st, cnt, mat in recs:
            sub = idx[st:st + cnt]
            if len(sub) < 3: continue
            tri = strip_to_tris(sub); tri = tri[: len(tri) // 3 * 3]
            if len(tri) == 0: continue
            tri = tri.reshape(-1, 3); tri = tri[(tri < n).all(1)].ravel()
            if len(tri) == 0: continue
            key = slots[mat] if mat < len(slots) else 0
            if key not in keymap: key = 0
            groups.setdefault(key, []).append(tri)
        if not groups: continue
        for k in groups:
            if k: need.add(k)
        size = hi - lo; cat = category(o.name)
        if max(size[0], size[1]) > 450 or size[2] > 160: cat = "backdrop"          # sky domes, skyline, huge planes: hidden by default
        vv = np.where(np.isfinite(v), v, 0).astype(np.float32); uv = np.where(np.isfinite(vb[:, 4:6]), vb[:, 4:6], 0).astype(np.float32)
        out["objects"].append({"id": oi, "name": o.name, "cat": cat, "cap": o.capacity, "bbox": [float(q) for q in np.concatenate([lo, hi])],
                               "pos": b64(vv), "uv": b64(uv), "groups": [{"key": k, "idx": b64(np.concatenate(l).astype(np.uint32))} for k, l in groups.items()]})
    out["textures"] = {}
    for k in need:
        try: out["textures"][str(k)] = tex.png_data_url(tex.decode(keymap[k], k), TEX_SIDE)
        except Exception: pass
    cols = world.load_collisions(tree); n_by_kind = {"ground": 0, "wall_tall": 0, "wall_mid": 0}
    for sid, c in cols.items():
        if c.ox > x1 or c.ox + c.W * 32 < x0 or c.oy > y1 or c.oy + c.H * 32 < y0: continue
        tris = {"ground": [], "wall_tall": [], "wall_mid": []}
        for i in range(c.npoly):
            x, y, z = c.poly(i)
            if x.max() < x0 or x.min() > x1 or y.max() < y0 or y.min() > y1: continue
            k = c.kind(i)
            if k == "wall_tall": z = np.array([20.0, 45.0, 45.0, 20.0]) if False else np.array([20.0, 20.0, 45.0, 45.0])
            if x.min() < -3000: continue                       # polys parked far away by edits
            n_by_kind[k] += 1
            if k == "ground": tris[k].append([[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]], [x[0], y[0], z[0]], [x[2], y[2], z[2]], [x[3], y[3], z[3]]])
            else:
                zz = c.poly(i)[2]; zb = (20.0, 36.0) if k == "wall_tall" else (float(zz.min()), float(zz.max()))
                p = [[x[j], y[j]] for j in range(4)]
                for j in range(4):
                    a, b = p[j], p[(j + 1) % 4]
                    if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1e-6: continue
                    tris[k].append([[a[0], a[1], zb[0]], [b[0], b[1], zb[0]], [b[0], b[1], zb[1]], [a[0], a[1], zb[0]], [b[0], b[1], zb[1]], [a[0], a[1], zb[1]]])
        entry = {"sid": sid, "grid": [c.ox, c.oy, c.W, c.H]}
        for k, lst in tris.items():
            if lst: entry[k] = b64(np.array(lst, np.float32).reshape(-1, 3))
        out["collision"].append(entry)
    out["stats"] = {"objects": len(out["objects"]), **n_by_kind}
    seen = set(); rd = os.path.join(tracks_dir, f"ROUTES{track}")
    for f in sorted(glob.glob(os.path.join(rd, "Paths4*.bin"))):
        try: t = bun.parse(open(f, "rb").read())
        except Exception: continue
        for top in t:
            if top.magic != 0x80034147: continue
            for k in top.kids:
                if k.magic != 0x34148: continue
                for i in range(0, len(k.data) - 23, 24):
                    x, y = struct.unpack_from("<2f", k.data, i)
                    if np.isfinite([x, y]).all() and x0 <= x <= x1 and y0 <= y <= y1 and (round(x), round(y)) not in seen: seen.add((round(x), round(y))); out["routes"].append([x, y])
    return out
