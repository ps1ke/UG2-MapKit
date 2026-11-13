# Projects: a standard way to share map changes

A **project** is a package of *changes*, never of game files. You can make one, give the `.ug2proj` file to somebody, and they can switch it on or off, combine it with other people's projects and build the result from their own, untouched copy of the game.

```
my-project/
  project.json     manifest
  edits.json       {"ops": [ ... ], "groups": [ ... ]}   the keyed changes
  assets/          textures (PNG/JPG), imported models and other bulk data the ops refer to
```

`.ug2proj` is just this folder as a zip file.

## How a build works

```
untouched game files  ->  imported project 1  ->  imported project 2  ->  ...  ->  the project you edit  ->  build
```

* The game folder you point MapKit at is never modified by projects. Every build starts from it again.
* Switching a project off, reordering, or removing it is simply a rebuild. MapKit never tries to "undo" changes inside a built map.
* The **last** project wins when two projects change the same thing. The build report lists every such conflict.
* Only the files that actually changed are written to `<workspace>/build/TRACKS` and installed.

## Using projects in the viewer (Projects tab)

* **Project you are editing**: name, version, author, description. Every edit you make goes into this project. **Export .ug2proj** downloads it.
* **New project / Open**: switch to another project of this workspace (the page reloads).
* **Import .ug2proj**: adds somebody else's project to the *imported* list. If the id already exists you are asked whether to replace it.
* **Imported projects**: tick to switch on/off, ↑ ↓ to change the load order, ✕ to remove. Problems (missing dependency, conflicts, wrong order) are shown in orange.
* Imported projects are applied at build time. The 3D view shows only the project you are editing; use *show BUILT collision* and a test run in the game to see the combined result.

## `project.json`

```json
{
  "format": 1,
  "id": "desert-airport",
  "name": "Desert airport",
  "version": "1.2.0",
  "author": "Your name",
  "description": "Longer text shown in the Projects tab",
  "target": "nfsu2-map",
  "requires":   [ { "id": "base-roads", "version": ">=1.0,<2" } ],
  "conflicts":  [ "classic-lighting" ],
  "load_after": [ "hd-textures" ],
  "created": "2026-11-13"
}
```

| Field | Meaning |
|---|---|
| `id` | unique, lowercase letters, digits, `-` `_`, max 48 characters. Folder name and identity. |
| `version` | `1.2.0` style. Compared numerically. |
| `requires` | projects that must be imported, switched on, **above** this one, in the given version range (`>=`, `>`, `<=`, `<`, `==`, comma separated). |
| `conflicts` | projects that must not be switched on together with this one. |
| `load_after` | soft ordering hint. |
| `target` | what the project edits. Only `nfsu2-map` exists today; the format is meant to also carry cars, handling and other game data later. |

## `edits.json` and the operations

Operations are *symbolic*: they name an object id, a placed-instance slot, a collision polygon number or a coordinate. They never contain byte offsets of the game files, which is why independent projects can be merged. The complete list is at the top of [`ug2map/edit.py`](../ug2map/edit.py); the most common ones:

| op | what it does |
|---|---|
| `remove_object`, `transform_object` | hide / move / rotate / scale a baked terrain, road or building piece |
| `move_instance`, `duplicate_instance` | move, hide or copy a placed prop, tree or building |
| `move_vertices` | reshape an object (props: every copy changes) |
| `set_texture`, `replace_texture`, `add_texture` | switch / overwrite / add textures |
| `add_quad`, `add_road`, `add_box`, `add_mesh` | new visible surfaces and imported models; `collision:true` makes them drivable |
| `add_wall`, `remove_polys`, `clear_walls_box` | collision walls and polygons |
| `paste`, `paste_collision` | copy objects (with the ground and walls under them) or collision from any region |
| `move_marker`, `add_marker` | shop / garage / spawn markers |

Bulky data is stored as files: textures in `assets/tex_<hash>.png`, imported meshes in `assets/mesh_<hash>.json`, large collision selections in `assets/coll_<hash>.json`. The ops point at them with `image_file`, `mesh_file`, `polys_file`. The viewer stores and reads this transparently.

## Conflict detection

For every enabled project MapKit collects the *keys* its ops overwrite (an object id, an instance slot, a polygon, a marker, a texture key, a new texture name). A key written by two projects is reported as `CONFLICT ... the later one wins`. Operations that only add things (new quads, roads, walls) never conflict.

## What is not solved yet

* **Unique ids for new assets.** New textures are identified by their name (the game hashes it), so two projects adding different textures with the same name collide. The planned fix is to namespace every new asset as `<project-id>/<name>` and let the build assign the final game name; today the report only warns.
* **Dependencies on edits.** Edits of project B that sit on top of project A refer to A's objects by id. If A changes, B may point at something that moved; MapKit does not rebase, it reports problems.
* **Preview of imported projects** in the 3D view.
