@echo off
REM Rebuild the NCC 3D scenes from ncc/rules/rules.json
cd /d "%~dp0..\ncc\generator"
python detail_generator.py %*
