"""Lossless NFSU2 .BUN chunk tree: parse -> modify -> serialize (byte-identical when unmodified).

Padding chunks (magic 0) are kept as nodes. Top-level padding is recomputed on
write so the chunk after it keeps its original alignment (0x800 or 0x1000...).
"""
import struct

class Node:
    def __init__(self, magic, data=None, kids=None, align=0):
        self.magic, self.data, self.kids, self.align = magic, data, kids, align

def parse(buf, start=0, end=None, top=True):
    end = len(buf) if end is None else end
    out, p = [], start
    while p + 8 <= end:
        magic, size = struct.unpack_from("<II", buf, p)
        ps, pe = p + 8, p + 8 + size
        assert pe <= end, "bad chunk %08x at %d" % (magic, p)
        if magic == 0:
            out.append(Node(0, data=bytes(buf[ps:pe])))
        elif magic & 0x80000000:
            out.append(Node(magic, kids=parse(buf, ps, pe, False)))
        else:
            out.append(Node(magic, data=bytes(buf[ps:pe])))
        out[-1].start = p
        p = pe
    if top:  # alignment of the chunk that follows each padding node
        for i, n in enumerate(out[:-1]):
            if n.magic == 0:
                nxt = out[i + 1].start
                n.align = next((a for a in (0x1000, 0x800, 0x400, 0x100) if nxt % a == 0), 0)
                if n.align:  # whole alignment blocks of padding beyond the minimum
                    mn = (-n.start) % n.align
                    if mn < 8: mn += n.align
                    n.extra = (nxt - n.start) - mn
    return out

def serialize(nodes, top=True):
    out = bytearray()
    for n in nodes:
        if n.magic == 0:
            if top and n.align:
                need = (-len(out)) % n.align
                if need < 8: need += n.align
                need += getattr(n, "extra", 0)
                out += struct.pack("<II", 0, need - 8) + b"\0" * (need - 8)
            else:
                out += struct.pack("<II", 0, len(n.data)) + n.data
            continue
        payload = serialize(n.kids, False) if n.kids is not None else n.data
        out += struct.pack("<II", n.magic, len(payload)) + payload
    return bytes(out)


def serialize_map(nodes):
    """like serialize(top=True) but also returns each top-level node's NEW start offset (for fixing the absolute-offset block table 0x34110 in L4Rx.BUN)"""
    out = bytearray(); starts = []
    for n in nodes:
        starts.append(len(out))
        if n.magic == 0:
            if n.align:
                need = (-len(out)) % n.align
                if need < 8: need += n.align
                need += getattr(n, "extra", 0)
                out += struct.pack("<II", 0, need - 8) + b"\0" * (need - 8)
            else: out += struct.pack("<II", 0, len(n.data)) + n.data
            continue
        payload = serialize(n.kids, False) if n.kids is not None else n.data
        out += struct.pack("<II", n.magic, len(payload)) + payload
    return bytes(out), starts
