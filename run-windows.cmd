@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b 1
set PYTHONUTF8=1
if defined AIRISK_PYTHON goto configured
if exist "%~dp0.venv-windows\Scripts\python.exe" goto existing
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 goto py312
py -3.11 -c "import sys" >nul 2>&1
if not errorlevel 1 goto py311
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto python
echo Python was not found. Install your organization's approved Python 3.12 build.
echo Alternatively set AIRISK_PYTHON to the full path of an approved python.exe.
exit /b 1

:configured
"%AIRISK_PYTHON%" "%~dp0run_local.py" %*
exit /b %errorlevel%

:existing
"%~dp0.venv-windows\Scripts\python.exe" "%~dp0run_local.py" %*
exit /b %errorlevel%

:py312
py -3.12 "%~dp0run_local.py" %*
exit /b %errorlevel%

:py311
py -3.11 "%~dp0run_local.py" %*
exit /b %errorlevel%

:python
python "%~dp0run_local.py" %*
exit /b %errorlevel%
