@echo off
rem Launcher so `ajna` works from any directory once this folder is on PATH.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
where python >nul 2>&1 && (
  python -m ajna %*
) || (
  py -3 -m ajna %*
)
exit /b %ERRORLEVEL%
