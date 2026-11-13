# Contributing

Bug reports and pull requests are welcome. Please include:

* what you did (project, ops, build report) and what the game or the viewer did;
* for crashes: the Windows/Wine version, and whether the build works with only the suspect project switched on.

Please never attach game files. Projects shared as `.ug2proj` contain only edits, which is fine.

## Development

```
pip install -r requirements.txt
python -m unittest discover tests
```

Tests that need the game data run only when `UG2_TRACKS` points at an unmodified `TRACKS` folder.

## Layout

| Path | Role |
|---|---|
| `ug2map/bun.py`, `world.py`, `tex.py` | read the container, objects, textures, collision |
| `ug2map/edit.py`, `host.py`, `markers.py`, `relocate.py` | apply operations, write geometry, markers, fix the block table |
| `ug2map/projects.py` | projects, stack, import/export, conflicts |
| `ug2map/server.py`, `__main__.py`, `viewer.html` | HTTP API and the browser editor |
| `docs/` | install, usage, project format, file format notes |
