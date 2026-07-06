@echo off
REM Build the ultrafast DXF parser into dxfcore.dll using MSVC (cl.exe).
REM
REM Run this from a "x64 Native Tools Command Prompt for VS" (Start menu, under
REM Visual Studio) so that cl.exe is on PATH. Produces dxfcore.dll right here,
REM which pydxf\loader.py loads via ctypes.
setlocal
cd /d "%~dp0"

where cl >nul 2>nul
if errorlevel 1 (
  echo [error] cl.exe not found on PATH.
  echo         Open the "x64 Native Tools Command Prompt for VS" and re-run this.
  exit /b 1
)

cl /nologo /std:c++17 /O2 /EHsc /LD dxf_parse.cpp /Fe:dxfcore.dll
if errorlevel 1 (
  echo [error] build failed.
  exit /b 1
)

REM Keep only the .dll; ctypes does not need the import lib / export table.
del /q dxf_parse.obj dxfcore.exp dxfcore.lib >nul 2>nul
echo built %cd%\dxfcore.dll
endlocal
