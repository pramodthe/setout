@echo off
REM Rebuild the 3D scene from rules.json
cd /d "%~dp0..\generator"
python detail_generator.py %*
