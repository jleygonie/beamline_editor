@echo off
rem Create (or update) the virtual environment for the Beamline Editor (Windows cmd / PowerShell).
rem Usage: create_venv.bat [python_executable]   (default: py, or python if the py launcher is missing)
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"

set "PYTHON=%~1"
if "%PYTHON%"=="" (
    where py >nul 2>nul && (set "PYTHON=py") || (set "PYTHON=python")
)

if not exist "%VENV_DIR%\" (
    echo Creating virtual environment in %VENV_DIR% using %PYTHON% ...
    "%PYTHON%" -m venv "%VENV_DIR%" || goto :error
) else (
    echo Virtual environment already exists in %VENV_DIR%, updating packages ...
)

"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip || goto :error
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%SCRIPT_DIR%requirements.txt" || goto :error

echo Done. Launch the editor with run_beamline_editor.bat
exit /b 0

:error
echo Failed to set up the virtual environment. 1>&2
exit /b 1
