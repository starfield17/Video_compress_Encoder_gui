@echo off
setlocal
cd /d "%~dp0\.."

set PYTHON_BIN=python
if not "%~1"=="" set PYTHON_BIN=%~1

echo Using Python: %PYTHON_BIN%
%PYTHON_BIN% scripts\build_pyinstaller.py --clean --windowed
exit /b %ERRORLEVEL%
