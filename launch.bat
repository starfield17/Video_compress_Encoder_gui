@echo off
setlocal
cd /d "%~dp0"

if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" (
        "%CONDA_PREFIX%\python.exe" main.py %*
        exit /b %ERRORLEVEL%
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "Set-Location -LiteralPath '%~dp0';" ^
  "if ($env:CONDA_PREFIX -and (Test-Path (Join-Path $env:CONDA_PREFIX 'python.exe'))) { & (Join-Path $env:CONDA_PREFIX 'python.exe') 'main.py' @args; exit $LASTEXITCODE }" ^
  "elseif (Get-Command python -ErrorAction SilentlyContinue) { & python 'main.py' @args; exit $LASTEXITCODE }" ^
  "elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 'main.py' @args; exit $LASTEXITCODE }" ^
  "else { Write-Error 'Python was not found on PATH.'; exit 1 }" ^
  %*
if %ERRORLEVEL% NEQ 9009 (
    exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python main.py %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 main.py %*
    exit /b %ERRORLEVEL%
)

echo Python was not found on PATH.
exit /b 1
