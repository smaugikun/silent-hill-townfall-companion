@echo off
rem Converts the game's videos for the phone, from your own Townfall install. It needs RAD Video Tools,
rem FFmpeg and vgmstream in the tools folder, and says what is missing. Once is enough. Without it the
rem phone works too, but shows no story videos.
title Townfall Companion - converting the game's videos
call "%~dp0companion\run.bat" convert_videos.py %*
echo.
pause
