# Installation

UG2 MapKit is a small Python program with a browser interface. It needs **your own copy** of *Need for Speed: Underground 2* (PC); no game files are included.

## 1. Requirements

* Python **3.10 or newer** (3.11+ recommended). Check with `python3 --version`.
* About 1 GB of free RAM while a map is open (the city has ~10,000 objects).
* The game install. You only need its `TRACKS` folder, for example
  * Windows: `C:\Program Files (x86)\EA GAMES\Need for Speed Underground 2\TRACKS`
  * Linux / Wine: `<wine prefix>/drive_c/Program Files (x86)/EA GAMES/Need for Speed Underground 2/TRACKS`
* A modern browser (Chrome, Edge, Firefox). WebGL must work.

## 2. Get the program

```
git clone https://github.com/ps1ke/UG2-MapKit.git
cd UG2-MapKit
python3 -m venv .venv
# Windows:  .venv\Scripts\activate        Linux / macOS:  source .venv/bin/activate
pip install -r requirements.txt
```

(`pip install .` also works and adds an `ug2map` command; the examples below use `python -m ug2map`, which works without installing.)

## 2b. The easy way: the launcher

`start.sh` (Linux / macOS) or `start.bat` (Windows, double-click) creates the virtual environment, installs the dependencies, asks **once** for your game folders and starts the program. It stores the answers in `ug2mapkit.config` (not part of the repository); delete that file to be asked again. Then skip to *5. Check that it works*.

## 3. Make a safe copy of the game files (recommended)

Copy the whole `TRACKS` folder somewhere else, e.g. `~/ug2-original/TRACKS`, and point MapKit at the **copy**. MapKit only ever *reads* this folder. Your game is only touched when you press **Install into game** (and the originals are backed up first).

## 4. Start it

```
python -m ug2map serve ~/ug2-original/TRACKS --workspace ~/ug2-workspace --install "<game folder>/TRACKS"
```

* `--workspace` is where your projects, builds and backups are stored (created if missing). Default: `./ug2-workspace`.
* `--launch "<command that starts the game>"` is optional and adds a *Launch game* button.
* `--install` is optional. Without it the build is written to `<workspace>/build/TRACKS` and you copy the files yourself.
* The first start indexes ~10,000 objects and takes about a minute. The browser opens at <http://127.0.0.1:8765/>.
* The server only listens on `127.0.0.1` (your own computer). It has no login, so do not expose it to a network.

## 5. Check that it works

Open the page. You should see the city from above. Click a building: the Inspector shows its name. Press **F1** for the shortcuts.

## Building without the browser

```
python -m ug2map build ~/ug2-original/TRACKS --workspace ~/ug2-workspace --install "<game folder>/TRACKS"
```

builds the project stack (see [PROJECTS.md](PROJECTS.md)) and installs it.

## Updating

The viewer checks GitHub once a day (in the background) and shows an orange **update available** link in the top bar when a newer version exists. It only notifies; nothing is installed for you. To update, run `git pull` in the program folder. The check reads one small public file of the repository through the GitHub API (`ug2map/__init__.py`) and sends nothing about you or your files; turn it off with `--no-update-check`.

## Uninstalling your changes

In the viewer press **Restore originals** (Edits tab), or copy the files from `<workspace>/backups/` back into the game's `TRACKS` folder. Verifying the game files with your store client also restores them.

## Troubleshooting

| Problem | Fix |
|---|---|
| `No module named numpy` / `PIL` | activate the virtual environment and run `pip install -r requirements.txt` |
| Blank page | WebGL is off or blocked: try another browser or enable hardware acceleration |
| `FileNotFoundError: STREAML4RA.BUN` | the path is not the `TRACKS` folder (it must contain `STREAML4RA.BUN`) |
| The game crashes after installing a build | switch projects off one by one and rebuild; use **Restore originals** and report the project and the build report |
