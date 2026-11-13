"""Track position markers (chunk 0x34146 of ROUTESL4Rx/TrackPosMarkers*.bin): free-roam spawn points, shop / garage / crib triggers, start lines.
Each marker is 0x30 bytes: +8 name hash (h = h*33+c, start -1), +0x10 position xyz, +0x20 16-bit heading, +0x24 track number, +0x28 name.
ops: move_marker {hash,index,pos,[angle]}   add_marker {hash,pos,[angle]}   (index = n-th marker with that hash in the file)"""
import json, os, struct
from . import bun
from .world import fill

FILES = ("TrackPosMarkersFreeRoam.bin", "TrackPosMarkersAll.bin")
NAMES = {int(k): v for k, v in json.load(open(os.path.join(os.path.dirname(__file__), "marker_names.json"))).items()}
BY_NAME = {v: k for k, v in NAMES.items()}

def H(s):
    h = 0xFFFFFFFF
    for ch in s.encode(): h = (h * 33 + ch) & 0xFFFFFFFF
    return h

def _load(path):
    t = bun.parse(open(path, "rb").read()); n = t[0]; d = n.data; o = fill(d)
    rows = [bytearray(d[o + k * 0x30:o + (k + 1) * 0x30]) for k in range((len(d) - o) // 0x30)]
    return t, n, d[:o], rows

def _info(i, r):
    h, = struct.unpack_from("<I", r, 8); pos = struct.unpack_from("<3f", r, 0x10); ang, = struct.unpack_from("<H", r, 0x20); tr, = struct.unpack_from("<i", r, 0x24)
    return {"i": i, "hash": h, "name": NAMES.get(h, "0x%08x" % h), "pos": [float(v) for v in pos], "angle": ang, "track": tr}

def list_markers(tracks, region="L4RA"):
    p = os.path.join(tracks, "ROUTES" + region, FILES[0])
    if not os.path.exists(p): return []
    t, n, pre, rows = _load(p); out = []; seen = {}
    for i, r in enumerate(rows):
        m = _info(i, r); m["index"] = seen.get(m["hash"], 0); seen[m["hash"]] = m["index"] + 1; out.append(m)
    return out

def apply(tracks, out, region, ops, dry=False):
    rep = []
    for fname in FILES:
        src = os.path.join(tracks, "ROUTES" + region, fname)
        if not os.path.exists(src): continue
        t, n, pre, rows = _load(src); moved = added = 0
        for op in ops:
            h = int(op["hash"])
            if op["op"] == "move_marker":
                idx = [i for i, r in enumerate(rows) if struct.unpack_from("<I", r, 8)[0] == h]
                k = int(op.get("index", 0))
                if k >= len(idx): continue
                r = rows[idx[k]]; struct.pack_into("<3f", r, 0x10, *[float(v) for v in op["pos"]])
                if op.get("angle") is not None: struct.pack_into("<H", r, 0x20, int(op["angle"]) & 0xFFFF)
                moved += 1
            elif op["op"] == "add_marker":
                tmpl = next((r for r in rows if struct.unpack_from("<I", r, 8)[0] == h), None)
                if tmpl is None: continue
                r = bytearray(tmpl); struct.pack_into("<3f", r, 0x10, *[float(v) for v in op["pos"]])
                if op.get("angle") is not None: struct.pack_into("<H", r, 0x20, int(op["angle"]) & 0xFFFF)
                rows.append(r); added += 1
        if moved or added:
            n.data = bytes(pre) + b"".join(bytes(r) for r in rows); rep.append(f"{fname}: moved {moved}, added {added} marker(s)")
            if not dry:
                dst = os.path.join(out, "ROUTES" + region); os.makedirs(dst, exist_ok=True); fp = os.path.join(dst, fname)
                if os.path.exists(fp) and os.stat(fp).st_nlink > 1: os.remove(fp)
                open(fp + ".new", "wb").write(bun.serialize(t)); os.replace(fp + ".new", fp)
    return rep
