#!/bin/sh
# One-step launcher (Linux / macOS): creates a virtual environment, installs the dependencies, asks for your game's TRACKS folder once and starts UG2 MapKit.
cd "$(dirname "$0")" || exit 1
CFG=ug2mapkit.config
[ -d .venv ] || { python3 -m venv .venv || exit 1; }
. .venv/bin/activate
python -c "import numpy, PIL" 2>/dev/null || pip install -q -r requirements.txt || exit 1
if [ ! -f "$CFG" ]; then
  printf "Path of a COPY of the game's TRACKS folder (it is only read): "; read -r TRACKS
  printf "Path of the game's real TRACKS folder to install builds into (empty = do not install): "; read -r INSTALL
  printf "Command that starts the game (empty = no Launch button): "; read -r LAUNCH
  printf 'TRACKS=%s\nINSTALL=%s\nLAUNCH=%s\n' "$TRACKS" "$INSTALL" "$LAUNCH" > "$CFG"
fi
. "./$CFG" 2>/dev/null
EXTRA="$*"
set -- serve "$TRACKS" --workspace "${WORKSPACE:-ug2-workspace}"
[ -n "$INSTALL" ] && set -- "$@" --install "$INSTALL"
[ -n "$LAUNCH" ] && set -- "$@" --launch "$LAUNCH"
# shellcheck disable=SC2086
exec python -m ug2map "$@" $EXTRA
