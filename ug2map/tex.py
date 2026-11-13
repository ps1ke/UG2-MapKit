"""Track texture packs (TPK) -> RGBA images.  Layout verified against STREAML4RA.BUN (see docs/FORMATS_ADDENDUM.md):
container 0xb3300000: header 0xb3310000 / 0x33310004 = 124-byte records, pixels in 0xb3320000 / 0x33320002 (payload starts with 0x11 filler;
record offsets are relative to the first non-filler byte).  Record: +0x0c name[24], +0x24 key u32, +0x30 offset, +0x34 palette offset, +0x38 size,
+0x3c palette size, +0x44 width u16, +0x46 height u16, +0x4a format u8 (0x08 P8, 0x22 DXT1, 0x24 DXT3).
"""
from __future__ import annotations
import base64, io, struct
import numpy as np
from .world import fill

class Pack:
    def __init__(self, node):
        h = next(k for k in node.kids if k.magic == 0xb3310000); px = next(k for k in node.kids if k.magic == 0xb3320000); self.node, self.hdr, self.pxc = node, h, px
        self.px = next(k for k in px.kids if k.magic == 0x33320002); self.data = self.px.data; self.start = fill(self.data); self.dirty = False
        rec = next((k.data for k in h.kids if k.magic == 0x33310004), b""); self.recs = {}
        for i in range(0, len(rec) - 123, 124):
            r = rec[i:i + 124]; key, = struct.unpack_from("<I", r, 0x24)
            self.recs[key] = dict(name=r[0x0c:0x24].split(b"\0")[0].decode("latin1"), off=struct.unpack_from("<I", r, 0x30)[0], poff=struct.unpack_from("<I", r, 0x34)[0],
                                  size=struct.unpack_from("<I", r, 0x38)[0], psize=struct.unpack_from("<I", r, 0x3c)[0], w=struct.unpack_from("<H", r, 0x44)[0],
                                  h=struct.unpack_from("<H", r, 0x46)[0], fmt=r[0x4a])

def load_packs(tree): return [Pack(n) for n in tree if n.magic == 0xb3300000]

def commit(packs):
    for p in packs:
        if p.dirty: p.px.data = p.data

def _c565(v):
    r = ((v >> 11) & 31) * 255 // 31; g = ((v >> 5) & 63) * 255 // 63; b = (v & 31) * 255 // 31; return np.stack([r, g, b], -1).astype(np.uint8)

def _color_block(b, dxt1):
    c0 = b[..., 0].astype(np.uint16) | (b[..., 1].astype(np.uint16) << 8); c1 = b[..., 2].astype(np.uint16) | (b[..., 3].astype(np.uint16) << 8)
    bits = b[..., 4].astype(np.uint32) | (b[..., 5].astype(np.uint32) << 8) | (b[..., 6].astype(np.uint32) << 16) | (b[..., 7].astype(np.uint32) << 24)
    p0, p1 = _c565(c0).astype(np.int32), _c565(c1).astype(np.int32)
    four = (c0 > c1) | (not dxt1)
    p2 = np.where(four[..., None], (2 * p0 + p1) // 3, (p0 + p1) // 2); p3 = np.where(four[..., None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], -2)                                   # (..., 4, 3)
    a3 = np.where(four, 255, 0)                                             # selector 3 transparent in DXT1 c0<=c1 mode
    sh = (np.arange(16, dtype=np.uint32) * 2); idx = ((bits[..., None] >> sh) & 3).astype(np.int64)  # (..., 16)
    rgb = np.take_along_axis(pal, idx[..., None].repeat(3, -1), axis=-2)
    alpha = np.where(idx == 3, a3[..., None], 255)
    return rgb.astype(np.uint8), alpha.astype(np.uint8)

def decode_dxt(data, w, h, kind):
    bx, by = (w + 3) // 4, (h + 3) // 4; bs = 8 if kind == 1 else 16
    arr = np.frombuffer(data, np.uint8, bx * by * bs).reshape(by, bx, bs)
    rgb, alpha = _color_block(arr[..., bs - 8:], kind == 1)
    if kind == 3:
        ab = arr[..., :8]; nib = np.stack([ab & 15, ab >> 4], -1).reshape(by, bx, 16).astype(np.uint16); alpha = (nib * 17).astype(np.uint8)
    img = np.concatenate([rgb, alpha[..., None]], -1).reshape(by, bx, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(by * 4, bx * 4, 4)
    return img[:h, :w]

def decode(pack: Pack, key):
    r = pack.recs[key]; d = pack.data; s = pack.start + r["off"]; w, h = r["w"], r["h"]
    if r["fmt"] == 0x22: return decode_dxt(d[s:], w, h, 1)
    if r["fmt"] == 0x24: return decode_dxt(d[s:], w, h, 3)
    if r["fmt"] == 0x08:
        pal = np.frombuffer(d, np.uint8, 1024, pack.start + r["poff"]).reshape(256, 4); idx = np.frombuffer(d, np.uint8, w * h, s)
        return pal[idx].reshape(h, w, 4)
    raise ValueError("format %#x" % r["fmt"])

def png_data_url(img, max_side=256):
    from PIL import Image
    im = Image.fromarray(np.ascontiguousarray(img), "RGBA")
    if max(im.size) > max_side: im.thumbnail((max_side, max_side))
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=False); return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

# ------------------------------------------------------------------ encoding (size-preserving texture replacement)
def _to565(rgb):
    r = np.rint(rgb[..., 0] * 31 / 255).astype(np.int32); g = np.rint(rgb[..., 1] * 63 / 255).astype(np.int32); b = np.rint(rgb[..., 2] * 31 / 255).astype(np.int32)
    return (r << 11) | (g << 5) | b

def encode_dxt(img, kind):
    """img (h,w,4) uint8 -> DXT1 (kind 1) or DXT3 (kind 3) bytes, one mip level. Simple range-fit encoder (bounding-box endpoints)."""
    h, w = img.shape[:2]; bx, by = (w + 3) // 4, (h + 3) // 4
    pad = np.pad(img, ((0, by * 4 - h), (0, bx * 4 - w), (0, 0)), mode="edge").astype(np.float32)
    blk = pad.reshape(by, 4, bx, 4, 4).transpose(0, 2, 1, 3, 4).reshape(by, bx, 16, 4)
    rgb = blk[..., :3]; c_hi = _to565(rgb.max(2)); c_lo = _to565(rgb.min(2))
    swap = c_hi < c_lo; c0 = np.where(swap, c_lo, c_hi); c1 = np.where(swap, c_hi, c_lo); flat = c0 == c1
    c0 = np.where(flat & (c0 < 65535), c0 + 1, c0)                         # keep c0 > c1 (4-colour mode)
    def x565(c): return np.stack([((c >> 11) & 31) * 255 / 31, ((c >> 5) & 63) * 255 / 63, (c & 31) * 255 / 31], -1)
    p0, p1 = x565(c0), x565(c1); pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 2)     # (by,bx,4,3)
    d = ((rgb[:, :, :, None, :] - pal[:, :, None, :, :]) ** 2).sum(-1); idx = d.argmin(-1).astype(np.uint32)   # (by,bx,16)
    bits = (idx << (np.arange(16, dtype=np.uint32) * 2)).sum(-1).astype(np.uint32)
    out = np.zeros((by, bx, 8), np.uint8); out[..., 0] = c0 & 255; out[..., 1] = c0 >> 8; out[..., 2] = c1 & 255; out[..., 3] = c1 >> 8
    for k in range(4): out[..., 4 + k] = (bits >> (8 * k)) & 255
    if kind == 1: return out.tobytes()
    a4 = np.rint(blk[..., 3] / 17).astype(np.uint8); alpha = (a4[..., 0::2] | (a4[..., 1::2] << 4)).astype(np.uint8)
    return np.concatenate([alpha, out], -1).tobytes()

def _levels(w, h, fmt, size):
    """mip level list (w,h,bytes) whose total equals the record's size, or None"""
    def lev(wi, hi): return max(1, (wi + 3) // 4) * max(1, (hi + 3) // 4) * (8 if fmt == 0x22 else 16) if fmt in (0x22, 0x24) else wi * hi
    out = []; wi, hi = w, h; tot = 0
    for _ in range(12):
        b = lev(wi, hi); out.append((wi, hi, b)); tot += b
        if tot == size: return out
        if tot > size or (wi == 1 and hi == 1): break
        wi, hi = max(1, wi // 2), max(1, hi // 2)
    return None

def replace_texture(pack: Pack, key, image):
    """image: PIL Image. Resizes to the texture's own size, encodes in its own format, writes ALL mip levels in place."""
    from PIL import Image
    r = pack.recs[key]; w, h, fmt, size = r["w"], r["h"], r["fmt"], r["size"]
    lv = _levels(w, h, fmt, size) or [(w, h, size)]
    buf = bytearray(pack.data); base = pack.start + r["off"]; pos = base; im = image.convert("RGBA")
    for (lw, lh, nb) in lv:
        level = np.asarray(im.resize((lw, lh), Image.LANCZOS))
        if fmt == 0x22: data = encode_dxt(level, 1)
        elif fmt == 0x24: data = encode_dxt(level, 3)
        elif fmt == 0x08:
            q = im.convert("RGB").resize((lw, lh), Image.LANCZOS).quantize(256); pal = np.array(q.getpalette()[:768], np.uint8).reshape(-1, 3)
            pal = np.concatenate([pal, np.full((len(pal), 1), 255, np.uint8)], 1); pal = np.pad(pal, ((0, 256 - len(pal)), (0, 0)))
            if pos == base: buf[pack.start + r["poff"]:pack.start + r["poff"] + 1024] = pal.tobytes()
            data = np.asarray(q, np.uint8).tobytes()
        else: raise ValueError("format %#x" % fmt)
        assert len(data) == nb, ("encoded size mismatch", len(data), nb); buf[pos:pos + nb] = data; pos += nb
    pack.data = bytes(buf); pack.dirty = True
    return len(lv)


def _levels_for(w, h, fmt, size):
    return _levels(w, h, fmt, size) or [(w, h, size)]

def add_texture(packs, name, image, w, h, fmt=0x22, key=None, pack=None):
    """ADD a brand-new texture to a pack (the track file grows). image: PIL Image. Needs an existing texture with the same size+format as a template
    (it supplies the 124-byte record layout and the 32-byte table entry). Keeps the engine's invariants: records and the key table are sorted by key,
    pixel offsets are multiples of 128, every chunk keeps its 128-byte address alignment (padding chunks)."""
    import zlib
    from PIL import Image
    from . import bun
    assert fmt in (0x22, 0x24), "only DXT1 (0x22) / DXT3 (0x24) textures can be added"
    tpl = None
    for pk in packs:
        h_ = {c.magic: c for c in pk.hdr.kids}; recs = h_[0x33310004].data
        for i in range(0, len(recs) - 123, 124):
            r = recs[i:i + 124]
            if struct.unpack_from("<HH", r, 0x44) == (w, h) and r[0x4a] == fmt: tpl = (bytes(r), bytes(h_[0x33310005].data[(i // 124) * 32:(i // 124) * 32 + 32])); break
        if tpl: break
    if tpl is None:
        have = sorted({(r["w"], r["h"], r["fmt"]) for pk in packs for r in pk.recs.values()}); raise AssertionError(("no texture of that size/format exists to copy the layout from", (w, h, hex(fmt)), have[:20]))
    allkeys = {k for pk in packs for k in pk.recs}
    if key is None: key = zlib.crc32(name.encode()) & 0xFFFFFFFF
    assert key not in allkeys, ("texture key already exists", name, key)
    pk = pack or max(packs, key=lambda q: len(q.data))
    rec = bytearray(tpl[0]); size = struct.unpack_from("<I", rec, 0x38)[0]
    rec[0x0c:0x24] = name.encode("latin1")[:23].ljust(24, b"\0"); struct.pack_into("<I", rec, 0x24, key)
    cur = len(pk.data) - pk.start; off = -(-cur // 128) * 128
    lv = _levels_for(w, h, fmt, size); im = image.convert("RGBA"); body = bytearray()
    for (lw, lh, nb) in lv:
        data = encode_dxt(np.asarray(im.resize((lw, lh), Image.LANCZOS)), 1 if fmt == 0x22 else 3); assert len(data) == nb; body += data
    body += b"\0" * ((-len(body)) % 128)
    struct.pack_into("<I", rec, 0x30, off); struct.pack_into("<I", rec, 0x34, 0)
    newpx = bytes(pk.data) + b"\0" * (off - cur) + bytes(body)
    # header tables, inserted at the sorted position
    hk = {c.magic: c for c in pk.hdr.kids}; keys = hk[0x33310002]; recs = hk[0x33310004]; ent = hk[0x33310005]
    kl = [struct.unpack_from("<I", keys.data, i)[0] for i in range(0, len(keys.data), 8)]; pos = sum(1 for k in kl if k < key)
    old_hdr = len(bun.serialize([pk.hdr], False))
    keys.data = keys.data[:pos * 8] + struct.pack("<II", key, 0) + keys.data[pos * 8:]
    recs.data = recs.data[:pos * 124] + bytes(rec) + recs.data[pos * 124:]
    ent.data = ent.data[:pos * 32] + tpl[1] + ent.data[pos * 32:]
    dh = len(bun.serialize([pk.hdr], False)) - old_hdr
    if dh % 128: pk.hdr.kids.append(bun.Node(0, data=b"\0" * ((-dh - 8) % 128)))
    pk.px.data = newpx; pk.data = newpx; pk.recs[key] = dict(name=name, off=off, poff=0, size=size, psize=0, w=w, h=h, fmt=fmt); pk.dirty = False
    return key
