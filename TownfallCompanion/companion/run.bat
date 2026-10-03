@echo off
rem Runs one of the companion's Python scripts (bridge.py, convert_videos.py) with the Python installed
rem on this PC: the "py" launcher if there is one, else "python". The .bat files next to the companion
rem folder use it. No blocks in parentheses: a path with "(x86)" in it would end them early.
setlocal
set "PYTHON="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON python --version >nul 2>&1
if not defined PYTHON if not errorlevel 1 set "PYTHON=python"
if not defined PYTHON goto nopython
%PYTHON% "%~dp0%~1" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%

:nopython
echo Python was not found. Townfall Companion needs Python 3.10 or newer:
echo   https://www.python.org/downloads/
echo When you install it, tick "Add python.exe to PATH". Then start this again.
exit /b 1
