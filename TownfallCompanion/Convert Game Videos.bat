@echo off
rem OPTIONAL PRE-CACHE: the companion already starts caching missing videos automatically when the
rem game starts. Run this only if you want to prepare every video before launching the game, so no
rem conversion needs to run during play. Needs RAD Video Tools, FFmpeg and vgmstream in the tools folder.
title Townfall Companion - optional video pre-cache
call "%~dp0companion\run.bat" convert_videos.py %*
echo.
pause
