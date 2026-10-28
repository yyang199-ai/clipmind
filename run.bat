@echo off
rem ClipMind launcher for Windows users.
rem Requires Python 3.9+ on PATH. See README.md for full instructions.
title ClipMind - your clipboard, with a brain
cd /d "%~dp0"
python -m clipmind serve
if errorlevel 1 pause
