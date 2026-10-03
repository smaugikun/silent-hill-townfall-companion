@echo off
rem Starts Townfall Companion: the phone's page and its link to the game. Keep this window open while you
rem play, and close it to stop. It shows the address to open on the phone, and the PIN.
title Townfall Companion
call "%~dp0companion\run.bat" bridge.py %*
if errorlevel 1 pause
