@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
set "ACTION=%~1"
if not defined ACTION set "ACTION=start"

if defined PYTHON_BIN (
  "%PYTHON_BIN%" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
  if errorlevel 1 goto missing
  goto custom
)
if exist "%ROOT%runtime\python\python.exe" (
  "%ROOT%runtime\python\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
  if not errorlevel 1 goto bundled
)
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
  if not errorlevel 1 goto venv
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
if not errorlevel 1 goto launcher
python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
if not errorlevel 1 goto python
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" >nul 2>nul
if not errorlevel 1 goto python3
goto missing

:custom
"%PYTHON_BIN%" "%ROOT%deployment\manage.py" "%ACTION%"
goto done
:bundled
"%ROOT%runtime\python\python.exe" "%ROOT%deployment\manage.py" "%ACTION%"
goto done
:venv
"%ROOT%.venv\Scripts\python.exe" "%ROOT%deployment\manage.py" "%ACTION%"
goto done
:launcher
py -3 "%ROOT%deployment\manage.py" "%ACTION%"
goto done
:python
python "%ROOT%deployment\manage.py" "%ACTION%"
goto done
:python3
python3 "%ROOT%deployment\manage.py" "%ACTION%"
goto done
:missing
echo Python 3.9+ was not found.
echo Install approved offline Python, or place a full runtime in runtime\python.
echo You can also set PYTHON_BIN to the full path of python.exe.
echo PowerShell and CIM are required for safe Windows process management.
set "RESULT=1"
goto finish
:done
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" (
  echo Operation completed. The server runs independently of this window.
) else (
  echo Operation failed. Review the message above and .runtime\server.log.
)
:finish
if not defined XMC_NO_PAUSE pause
exit /b %RESULT%
