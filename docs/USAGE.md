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
