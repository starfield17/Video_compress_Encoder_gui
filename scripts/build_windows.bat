@echo off
setlocal
cd /d "%~dp0\.."

set PYTHON_BIN=python
if not "%~1"=="" set PYTHON_BIN=%~1

echo Using Python: %PYTHON_BIN%
%PYTHON_BIN% scripts\build_nuitka.py --clean
exit /b %ERRORLEVEL%
