@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (py "%~dp0CppMc.py" %*) || (python "%~dp0CppMc.py" %*)
if errorlevel 1 pause
