"""AI / race route files (ROUTESL4Rx/Paths*.bin). Chunk 0x34148 of each file holds the route nodes, 24 bytes each:
    +0 x:f32  +4 y:f32  +8 .. +19 link / type bytes  +20 distance along the route:f32
Only x and y are edited. The other chunks of the file (0x34149, 0x3414a ...) are lookup data that are not fully understood, so a moved node is EXPERIMENTAL:
test in the game. op: move_route_node {file, index, pos:[x, y]}"""
import glob, os, struct
from . import bun

def _nodes_chunk(t):
    for top in t:
        if top.magic == 0x80034147:
            for k in top.kids:
                if k.magic == 0x34148: return k
    return None

def list_files(tracks, region="L4RA"):
    out = []
    for f in sorted(glob.glob(os.path.join(tracks, "ROUTES" + region, "Paths*.bin"))):
        try: k = _nodes_chunk(bun.parse(open(f, "rb").read()))
        except Exception: continue
        if k is not None: out.append({"file": os.path.basename(f), "nodes": len(k.data) // 24})
    return out

def nodes(tracks, region, file):
    p = os.path.join(tracks, "ROUTES" + region, os.path.basename(file)); k = _nodes_chunk(bun.parse(open(p, "rb").read())); out = []
    for i in range(len(k.data) // 24):
        x, y = struct.unpack_from("<2f", k.data, i * 24); out.append([i, x, y])
    return out

def apply(tracks, out, region, ops, dry=False):
    rep = []; by = {}
    for o in ops: by.setdefault(os.path.basename(o["file"]), []).append(o)
    for f, lst in by.items():
        src = os.path.join(tracks, "ROUTES" + region, f)
        if not os.path.exists(src): rep.append(f"{f}: no such route file"); continue
        t = bun.parse(open(src, "rb").read()); k = _nodes_chunk(t); d = bytearray(k.data); n = 0
        for o in lst:
            i = int(o["index"])
            if 0 <= i < len(d) // 24: struct.pack_into("<2f", d, i * 24, float(o["pos"][0]), float(o["pos"][1])); n += 1
        k.data = bytes(d); rep.append(f"{f}: moved {n} route node(s)")
        if not dry:
            dst = os.path.join(out, "ROUTES" + region); os.makedirs(dst, exist_ok=True); fp = os.path.join(dst, f)
            open(fp + ".new", "wb").write(bun.serialize(t)); os.replace(fp + ".new", fp)
    return rep
