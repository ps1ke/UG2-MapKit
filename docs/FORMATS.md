# NFSU2 world format notes

What this project learned about the PC version's world files, from reading the files and testing builds in the game. "Verified" means seen working in the game; everything else is inferred. Corrections are welcome.

## Files

| File | Content |
|---|---|
| `STREAML4Rx.BUN` | the streamed world of region *x*: objects, textures, placements, collision |
| `L4Rx.BUN` | region header data: section outlines, a block table for the stream file, route and animation data |
| `ROUTESL4Rx/*.bin` | route paths and `TrackPosMarkers*.bin` (spawn points, start lines, shop markers) |

`L4RA` is the free-roam city. Event circuits live in the other regions.

## Chunk container (`bun.py`)

`magic:u32, size:u32, payload`. Magics with bit 31 set (`0x8003....`) contain child chunks. Zero-magic chunks are padding; a payload may start with `0x11` fill bytes up to an alignment. Writing the file back byte-identically is a built-in self-test (`bun.serialize(bun.parse(f)) == f`).

## Block table (`L4Rx.BUN`, chunk `0x34110`)

80-byte records. `[5]` is the **absolute offset** of a block inside `STREAML4Rx.BUN`, `[6]` its size, `[7]` the resident prefix. When anything in the stream file grows, these offsets must be re-addressed (`relocate.py`); stale offsets were the cause of every "file growth crashes the game" report. Verified: an unchanged file maps to a byte-identical table; grown files load.

## Objects (`0x80134010`)

* Vertices `0x134B01`: 24 bytes (xyz f32, colour, uv). Positions are in **world coordinates**.
* Indices `0x134B03`: triangle **lists** (not strips).
* Mesh records `0x134B02` (60 bytes): bounding box, index count, texture slot, index start.
* Slots `0x134012`: 8-byte entries, texture key per material. Object header `0x134011` holds bounds.
* Placements (`0x34103`, 64 bytes) give each object an AABB and, for *instances*, a transform. Props, trees and buildings are **instances** of shared models; baked terrain and road pieces are placed once.

### Adding geometry ("hosting")

Chunks can grow, but the engine finds geometry through the object tables, so new surfaces are written **into deleted objects**: the object's vertex/index/material tables are rewritten, its bounds and placement AABB updated. Rules found by testing:

* Pad unused index space with real duplicate triangles; never zero-area ones.
* An object is only drawn while its *streamed section* is loaded, i.e. near the host. Host new geometry in objects close to where it appears (the build does this per group of new surfaces).
* Hosts must be terrain/road pieces. Hosting inside tunnel objects crashed the game when drawn.
* A host takes up to 16 materials.

## Textures (`0xb3300000` packs)

124-byte records (name, key, offset, size, width/height, format `0x08` P8, `0x22` DXT1, `0x24` DXT3). The key is the CRC-style hash of the name. New textures need a template of the same size and format. Replacing keeps size and format.

## Collision (`0x80034130`, one per section)

* Header `0x34131`: section id, grid size W×H (32 m cells), origin, bounds. The bounds equal the bounding box of all polygons (checked on all 255 sections).
* Polygons `0x34134`, 32 bytes: group, bbox corner indices, surface/flags, base height, x[4], y[4] (1/8 m), z deltas (1/256 m). Tall walls have huge z range; mid walls are thin quads with a height. **Walls are one-sided**: the vertex order decides which side blocks.
* Cells `0x34137` (start, count per cell) and refs `0x34136` (polygon, 4×4 sub-cell mask). Original masks are subsets of the polygon's bbox mask.
* **Order matters**: the ground query stops scanning a cell at the first wall-flagged polygon, so ground references must come before wall references (`edit._insert_ground_ref`).
* The grid can be extended by whole cells (`edit.ensure_grid`) and the polygon array can grow (new ground/walls are appended); verified for ground, used for walls on the strength of the same mechanism.
* Which section answers for a point is decided by section outlines in `L4Rx.BUN` (`0x34152`); only ids with number part 1–19 and a letter are searched, and collision packs are folded by 20 (`edit.Topology`).
* Parking a polygon outside the int16 range corrupts the pack's bounds and broke physics for the whole section; delete polygons by removing their references only.

## Markers (`TrackPosMarkers*.bin`, chunk `0x34146`)

0x30-byte records: name hash (`h = h*33 + c`, start `-1`) at +8, position at +0x10, 16-bit heading at +0x20, track number at +0x24, name at +0x28. The free-roam file has 8 `ForwardStartingPosition` spawn points, a start line and 23 shop/garage names (`CC_PERF_SHOP`, `CRIB_1`, ...). Verified: moving or adding a marker moves the minimap pointer. **Not verified:** that an added marker creates a working shop. The drive-in zones and doors of shops are separate data in `L4RA.BUN` chunk `0x37090` (box, rotation, linked to the door props `XO_DOORPIECE*` / `ANM_DOORANIM` via `0x37080`); moving such an entry moves the shop's door.

## Game behaviour worth knowing

* Free-roam resets the car when there is no ground below the predicted position.
* Teleporting far away in a running game crashes or resets it because the destination section is not streamed in yet.
* Spawn points are chosen from the markers; the spawn depends on the map.
