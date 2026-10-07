@echo off
rem Launch the Beamline Editor using the project's virtual environment (Windows cmd / PowerShell).
rem Usage: run_beamline_editor.bat [file_to_open ...]
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=%SCRIPT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Virtual environment not found. Run create_venv.bat first. 1>&2
    exit /b 1
)

rem Relative file arguments are resolved by main.py against the current directory,
rem so do not change directory before launching.
"%VENV_PYTHON%" "%SCRIPT_DIR%src\main.py" %*
