@echo off
call "%~dp0deploy.bat" restart
exit /b %ERRORLEVEL%
