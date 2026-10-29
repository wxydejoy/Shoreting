@echo off
REM Prefer the VBS launcher so scheduled tasks do not flash a console.
wscript.exe //nologo "%~dp0run-probe.vbs"
