# Using the editor

Start the program ([INSTALL.md](INSTALL.md)) and open <http://127.0.0.1:8765/>.

## Moving around

* Left-drag orbit, right-drag pan, wheel zoom.
* **Arrow keys** fly the camera along the direction it looks (Up/Down) and sideways (Left/Right); PageUp/PageDown move straight up/down; hold Shift to go faster.
* The search box finds objects by name (try `SHOP`, `PALM`, `HIGHWAY`); *Go* jumps to coordinates.
* Views menu: toggle panels, top-down camera, minimap. The **Layers** tab switches visual layers, collision (green ground, red tall walls, orange mid walls), backdrops, route nodes and the shop / spawn markers on and off.

## Selecting

* Click an object, a collision polygon, a marker, or something you created.
* Shift adds to a selection. **Ctrl+drag** box-selects objects, **Ctrl+Alt+drag** box-selects collision polygons.
* Ctrl+C / Ctrl+V copy and paste. Copying objects also copies the ground and collision walls under them; selecting collision polygons and copying them pastes only collision. Ctrl+G groups, Delete deletes, Ctrl+Z undoes.
* **W / E / R** switch the gizmo between move, rotate and scale. The numeric boxes in the top bar set exact values; *snap* rounds to a grid.

## Tools (left palette)

| Tool | What it does |
|---|---|
| quad, road, curve | draw drivable surfaces on the ground. Points snap to nearby ground-collision edges, other quads/roads and model vertices (Alt disables). *Weld all quads* (Edits tab) or **J** closes gaps. |
| wall | collision walls (tall = solid, mid = 3 m). The collision array grows when a section is out of slots. |
| box, model | add a box or import an OBJ model with a texture. |
| vertices | reshape a terrain/road/wall piece or a prop by dragging its corner points. |
| shop | place a shop / garage / crib marker (see [FORMATS.md](FORMATS.md) for what markers do and do not do). |
| measure, ground, dup, del | measure distances, drop an object to the ground, duplicate, delete. |

## Catalogue, places, launch

* **Catalogue** (bottom dock): every placed model of the map by name with how often it is used. Click one: the camera flies to an example and selects it; press **D** to duplicate it and drag the copy where you want it.
* **places…** (next to *Go*): quick travel to every shop, the crib, car lots and a spawn point.
* **Launch game** (top bar) appears when you start the program with `--launch "<command that starts the game>"`. Restart the game after every build.

## Editing meshes

Vertices tool: click an object, then choose what to select in the options bar: **vertex**, **edge** or **face** (Shift adds), and drag the arrows. Coincident points move together, so faces stay closed. Props share their model: changing one changes every copy.

### Blender (or any 3D tool) round trip

1. Select objects, Inspector → **Export selection (OBJ)**. The file is written to `<workspace>/exports/` in world coordinates, Z up, with the textures as `usemtl k<key>`.
2. In Blender: File → Import → Wavefront OBJ with **Forward = Y, Up = Z**. Edit. Export over the same file (Selection Only, Forward = Y, Up = Z).
3. The viewer notices the saved file; press **Import edited OBJ**. The originals are hidden and your meshes are added as new geometry (hosted in removed terrain pieces, see FORMATS.md). Keep the material names (`k...`) to keep the textures.

## Collision surface types

Select collision polygons, Inspector → **Collision surface type**, enter an id, **Apply**. The id decides grip and sound; `1` is normal ground. The Inspector shows the current type of the selection. (Which id is which surface is not documented yet; try values and report what you find.)

## Textures

The bottom dock lists every texture of the map. Select an object, pick a texture, and use *Apply chosen texture* or *Replace with my image…* (keeps size and format). *New texture…* adds one.

## Build and test

* **Check** is a dry run, **Build** writes `<workspace>/build/TRACKS`. The top bar shows whether all edits are built, and the Edits tab lists what was written.
* **Install into game** copies the changed files into the game's TRACKS folder (set with `--install`), after saving the originals. Then start the game. The game only reads the map at start, so restart it after every build.
* The build report warns about common problems, for example original ground polygons that lie above your new ground (the car would pop up onto them).
* *show BUILT collision* in the Layers tab draws the collision of the built file, so you can see pasted and added ground and walls.

## Good to know

* Textures of **props** (trees, lamps, buildings placed in the map) belong to a shared model: changing one changes every copy.
* New visible surfaces need room: the build deletes nearby terrain/road pieces to host them and tells you which. Delete pieces yourself to choose which ones go.
* The game starts the free-roam car at one of its spawn markers, which depend on the map.
