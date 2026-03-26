@echo off
cd /d "%~dp0"
powershell -Command "python main.py %*"
