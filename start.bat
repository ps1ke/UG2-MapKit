@echo off
rem One-step launcher (Windows): creates a virtual environment, installs the dependencies, asks for your game's TRACKS folder once and starts UG2 MapKit.
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv || exit /b 1 )
call .venv\Scripts\activate.bat
python -c "import numpy, PIL" 2>nul || pip install -q -r requirements.txt || exit /b 1
if not exist ug2mapkit.config (
  set /p TRACKS=Path of a COPY of the game's TRACKS folder (it is only read): 
  set /p INSTALL=Path of the game's real TRACKS folder to install builds into (empty = do not install): 
  set /p LAUNCH=Command that starts the game (empty = no Launch button): 
  (echo TRACKS=%TRACKS%& echo INSTALL=%INSTALL%& echo LAUNCH=%LAUNCH%) > ug2mapkit.config
)
for /f "usebackq tokens=1,* delims==" %%a in ("ug2mapkit.config") do set %%a=%%b
set ARGS=serve "%TRACKS%" --workspace ug2-workspace
if not "%INSTALL%"=="" set ARGS=%ARGS% --install "%INSTALL%"
if not "%LAUNCH%"=="" set ARGS=%ARGS% --launch "%LAUNCH%"
python -m ug2map %ARGS%
