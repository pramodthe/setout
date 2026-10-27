@echo off
REM Serves the viewer at http://localhost:8000
REM Not strictly needed - index.html loads scene.js, so opening the file
REM directly works too. Use this if you switch to fetching scene.json.
cd /d "%~dp0..\viewer"
echo Open http://localhost:8000 in your browser
python -m http.server 8000
