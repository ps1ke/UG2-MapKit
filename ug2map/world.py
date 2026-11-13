"""Load the parts of an NFSU2 track (STREAML4R?.BUN) that a map editor needs.

Chunk map (verified; see docs/FORMATS_ADDENDUM.md):
  0x80134010  geometry object      -> 0x134011 header (name, bounds), 0x80134100 mesh group
      mesh group: 0x134B01 vertices (24 B: x y z f32, ABGR u32, u v f32, after 0x11 filler), 0x134B02 mesh records (60 B), 0x134B03 u16 indices (triangle strips)
  0x80034100  section placements   -> 0x34101 id, 0x34102 type names (68 B), 0x34103 placements (64 B: AABB min xyz/max xyz, type idx u16 at +0x18), 0x34104 piece AABB tree
  0x80034130  section collision    -> 0x34131 header, 0x34134 polys (32 B), 0x34136 cell refs (poly u16, mask u16), 0x34137 cells (start u16, count u16)
"""
from __future__ import annotations
import re, struct
from dataclasses import dataclass, field
import numpy as np
from . import bun

def fill(d: bytes) -> int:
    i = 0
    while i < len(d) and d[i] == 0x11: i += 1
    return i & ~3

def walk_objects(nodes):
    for n in nodes:
        if n.magic == 0x80134010: yield n
        if n.kids: yield from walk_objects(n.kids)

@dataclass
class Obj:
    node: object
    name: str
    group: object = None
    ch: dict = field(default_factory=dict)
    @property
    def verts(self):
        d = self.ch[0x134B01].data; f = fill(d); n = (len(d) - f) // 24
        return np.frombuffer(d, "<f4", n * 6, f).reshape(-1, 6)[:, :3] if n else np.zeros((0, 3), "f4")
    @property
    def capacity(self) -> int:
        """quads this object can host if its mesh is overwritten (4 vertices + 12 strip indices per quad)"""
        if not all(k in self.ch for k in (0x134B01, 0x134B02, 0x134B03)): return 0
        r = self.ch[0x134B02].data
        if len(r) - fill(r) not in (60, 120): return 0
        n = (len(self.ch[0x134B01].data) - fill(self.ch[0x134B01].data)) // 24
        return min(n // 4, (len(self.ch[0x134B03].data) // 2) // 12)          # triangle list: exactly 12 indices per quad, no strip-join slack
    def bounds(self):
        v = self.verts; v = v[np.isfinite(v).all(1) & (abs(v[:, :2]).max(1) < 9000)] if len(v) else v
        return (v.min(0), v.max(0)) if len(v) else (None, None)

def load_objects(tree) -> list[Obj]:
    out = []
    for o in walk_objects(tree):
        hd = next(k for k in o.kids if k.magic == 0x134011)
        m = re.findall(rb"[A-Za-z0-9_]{5,}", hd.data); name = m[0].decode("latin1") if m else "?"
        g = next((k for k in o.kids if k.magic == 0x80134100), None)
        out.append(Obj(o, name, g, {k.magic: k for k in g.kids} if g else {}))
    return out

@dataclass
class Collision:
    node: object
    sid: int
    W: int; H: int; ox: float; oy: float
    polys: bytearray; refs: bytearray; cells: bytearray; header: bytearray
    def poly(self, i):
        zb, = struct.unpack_from("<h", self.polys, i * 32 + 4)
        x = np.array(struct.unpack_from("<4h", self.polys, i * 32 + 8)) / 8; y = np.array(struct.unpack_from("<4h", self.polys, i * 32 + 16)) / 8
        z = zb + np.array(struct.unpack_from("<4h", self.polys, i * 32 + 24)) / 256
        return x, y, z
    def kind(self, i):
        x, y, z = self.poly(i)
        if z.max() - z.min() > 200: return "wall_tall"
        if abs((x[2] - x[0]) * (y[3] - y[1]) - (y[2] - y[0]) * (x[3] - x[1])) / 2 < 0.5 and z.max() - z.min() > 2.0: return "wall_mid"
        return "ground"
    @property
    def npoly(self): return len(self.polys) // 32
    @property
    def ref_capacity(self): return len(self.refs) // 4

def load_collisions(tree) -> dict[int, Collision]:
    out = {}
    for n in tree:
        if n.magic != 0x80034130: continue
        ch = {k.magic: k for k in n.kids}; h = ch[0x34131].data
        sid, = struct.unpack_from("<I", h, 0); W, H = struct.unpack_from("<HH", h, 4); ox, oy = struct.unpack_from("<ff", h, 8)
        out[sid] = Collision(n, sid, W, H, ox, oy, bytearray(ch[0x34134].data), bytearray(ch[0x34136].data), bytearray(ch[0x34137].data), bytearray(h))
    return out

def store_collision(c: Collision):
    ch = {k.magic: k for k in c.node.kids}
    ch[0x34131].data = bytes(c.header); ch[0x34134].data = bytes(c.polys); ch[0x34136].data = bytes(c.refs); ch[0x34137].data = bytes(c.cells)
