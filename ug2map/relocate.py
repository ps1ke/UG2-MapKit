"""Growing a STREAMxxxx.BUN: every streaming block is addressed by ABSOLUTE file offset in chunk 0x34110 of L4Rx.BUN
(80-byte records, u32 index: [0] block id, [4] flag(1=file block), [5] file offset, [6] size, [7] 'resident/first-load' size, [12] hash).
After growing chunks we serialize with the same 0x800/0x1000 alignment padding, then rewrite offsets/sizes here.
"""
import struct
import numpy as np
from . import bun

def find_table(nodes):
    for x in nodes:
        if x.magic == 0x34110: return x
        if x.kids:
            r = find_table(x.kids)
            if r: return r

def relocate(table_file_bytes, old_nodes, old_len, new_len, new_starts):
    """returns patched bytes of L4Rx.BUN. old_nodes need .start (from bun.parse)."""
    t = bun.parse(table_file_bytes); n = find_table(t); a = np.frombuffer(n.data, "<u4").reshape(-1, 20).copy()
    starts = [x.start for x in old_nodes]; ends = starts[1:] + [old_len]
    mp = dict(zip(starts, new_starts)); mp[old_len] = new_len
    def nb(pos):                       # map a block boundary (must be a chunk boundary)
        assert pos in mp, ("table offset is not a chunk boundary", pos); return mp[pos]
    def grown_before(pos):             # total growth of chunks that lie completely before pos
        d = 0
        for s, e, ns in zip(starts, ends, new_starts):
            if e <= pos: d = ns + 0 - s + 0 if False else d
        return d
    # per-chunk new sizes for the size2 rule
    nsz = [(new_starts[i + 1] if i + 1 < len(new_starts) else new_len) - new_starts[i] for i in range(len(starts))]
    osz = [ends[i] - starts[i] for i in range(len(starts))]
    import bisect
    for r in a:
        if r[4] != 1: continue
        off, sz, sz2 = int(r[5]), int(r[6]), int(r[7]); no = nb(off); ne = nb(off + sz); r[5] = no; r[6] = ne - no
        if sz2 == sz: r[7] = ne - no
        else:                          # prefix length: add growth of the chunks that end inside the prefix
            i0 = bisect.bisect_left(starts, off); g = 0; end2 = off + sz2
            for i in range(i0, len(starts)):
                if starts[i] >= off + sz: break
                if ends[i] <= end2: g += nsz[i] - osz[i]
            r[7] = sz2 + g
    n.data = a.tobytes(); return bun.serialize(t)
