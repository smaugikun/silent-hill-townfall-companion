@echo off
rem OPTIONAL PRE-CACHE: the companion already converts each game video automatically the first time
rem the phone needs it. Run this only if you want to prepare every video in advance so first playback
rem starts immediately. Needs RAD Video Tools, FFmpeg and vgmstream in the tools folder.
title Townfall Companion - optional video pre-cache
call "%~dp0companion\run.bat" convert_videos.py %*
echo.
pause
