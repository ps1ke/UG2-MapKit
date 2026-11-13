"""Raw data explorer + size-preserving patches for the chunks of L4Rx.BUN and ROUTES files whose meaning is not (fully) known yet:
fog, rain, weather and sound-reverb ("echo") parameters, shop and zone tables, ...

    op: patch_chunk {file, magic, index, offset, old, new}     file: 'L4RA.BUN' or 'ROUTESL4RA/<name>.bin'
        index = n-th top-level chunk with that magic, offset = byte offset inside the chunk payload (after its padding),
        old = expected bytes (hex), new = replacement bytes (hex, same length). If 'old' does not match the build FAILS instead of corrupting data."""
import os, struct
from . import bun
from .world import fill

def _path(tracks, file):
    p = os.path.normpath(os.path.join(tracks, file))
    if not p.startswith(os.path.normpath(tracks) + os.sep): raise ValueError("bad file")
    if not (file.upper().startswith("L4R") or file.startswith("ROUTES")): raise ValueError("only L4Rx.BUN and ROUTES files can be explored")
    return p

def _walk(nodes, depth=0):
    for n in nodes:
        yield n, depth
        if n.kids: yield from _walk(n.kids, depth + 1)

def chunk_table(tracks, file):
    t = bun.parse(open(_path(tracks, file), "rb").read()); rows = {}
    for n in t:
        r = rows.setdefault(n.magic, {"magic": n.magic, "count": 0, "bytes": 0, "kids": n.kids is not None}); r["count"] += 1; r["bytes"] += len(n.data) if n.data is not None else 0
    return sorted(rows.values(), key=lambda r: r["magic"])

def entries(tracks, file, magic):
    t = bun.parse(open(_path(tracks, file), "rb").read()); out = []
    for n in [x for x in t if x.magic == magic]:
        d = n.data or b""; out.append({"len": len(d), "pad": fill(d)})
    return out

def peek(tracks, file, magic, index, offset=0, length=256):
    t = bun.parse(open(_path(tracks, file), "rb").read()); n = [x for x in t if x.magic == magic][index]; d = n.data or b""; o = fill(d); body = d[o:]
    seg = body[offset:offset + length]; rows = []
    for i in range(0, len(seg), 16):
        b = seg[i:i + 16]; f = [struct.unpack_from("<f", b, k)[0] for k in range(0, len(b) - 3, 4)]
        rows.append({"off": offset + i, "hex": b.hex(), "f32": [None if f_ != f_ or abs(f_) > 1e9 else round(f_, 4) for f_ in f], "u32": [struct.unpack_from("<I", b, k)[0] for k in range(0, len(b) - 3, 4)]})
    return {"size": len(body), "rows": rows}

def apply(tracks, out, ops, dry=False):
    rep = []; by = {}
    for o in ops: by.setdefault(o["file"], []).append(o)
    for file, lst in by.items():
        src = os.path.join(out, file) if (not dry and os.path.exists(os.path.join(out, file))) else _path(tracks, file)
        t = bun.parse(open(src, "rb").read())
        for o in lst:
            cands = [x for x in t if x.magic == int(o["magic"])]; n = cands[int(o.get("index", 0))]; d = bytearray(n.data); base = fill(bytes(d)) + int(o["offset"])
            old = bytes.fromhex(o["old"]); new = bytes.fromhex(o["new"])
            if len(old) != len(new): raise AssertionError(f"patch {file} {hex(int(o['magic']))}+{o['offset']}: old and new differ in length")
            if bytes(d[base:base + len(old)]) != old: raise AssertionError(f"patch {file} chunk {hex(int(o['magic']))}[{o.get('index', 0)}] +{o['offset']}: the bytes there are {bytes(d[base:base + len(old)]).hex()}, not {o['old']}: the file differs from the one the patch was made for (another game version, or another project already changed it)")
            d[base:base + len(new)] = new; n.data = bytes(d)
        rep.append(f"{file}: {len(lst)} byte patch(es)")
        if not dry:
            dst = os.path.join(out, file); os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst) and os.stat(dst).st_nlink > 1: os.remove(dst)
            open(dst + ".new", "wb").write(bun.serialize(t)); os.replace(dst + ".new", dst)
    return rep
