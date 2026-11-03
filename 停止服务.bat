@echo off
call "%~dp0deploy.bat" stop
exit /b %ERRORLEVEL%
