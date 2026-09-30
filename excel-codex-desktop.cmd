@echo off
rem Double-click: the Codex desktop app / IDE extension use the Excel bridge
rem while this window stays open; closing it puts your Codex config back.
rem A newer release is installed first (EXCEL_BRIDGE_AUTO_UPDATE=0 turns that off).
if exist "%~dp0excel-codex.exe" goto exe
call "%~dp0excel-codex.cmd" desktop %*
exit /b %errorlevel%
:exe
"%~dp0excel-codex.exe" update --launcher
if errorlevel 10 if not errorlevel 11 goto install
set "EXCEL_BRIDGE_JUST_UPDATED="
"%~dp0excel-codex.exe" desktop %*
if errorlevel 1 pause
exit /b %errorlevel%
:install
rem No `call`: control moves to apply.cmd for good, so it can replace this
rem file; it starts the new one when it is done.
"%~dp0.update\apply.cmd" %*
