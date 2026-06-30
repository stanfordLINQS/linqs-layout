@echo off
REM Build the standalone "LINQS Layout" Windows app, and (if Inno Setup is
REM installed) the installer .exe.
REM
REM   packaging\build_win.bat
REM
REM Prereqs: Python 3.x on PATH, and dxfcore.dll already built (run
REM dxfcore\build.bat from a "x64 Native Tools Command Prompt for VS" first; this
REM script will attempt it if cl.exe is on PATH). Inno Setup's iscc on PATH is
REM optional — without it you still get the unpacked app folder.
REM
REM Produces: dist\LINQS Layout\LINQS Layout.exe
REM           dist\LINQS-Layout-Setup-<version>.exe   (if iscc is available)
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ==^> native core
if not exist "dxfcore\dxfcore.dll" (
  call "dxfcore\build.bat" || goto :err
)
if not exist "dxfcore\dxfcore.dll" (
  echo [error] dxfcore\dxfcore.dll missing. Build it from a VS Native Tools prompt:
  echo         dxfcore\build.bat
  goto :err
)

echo ==^> build venv
python -m venv .build-venv || goto :err
call ".build-venv\Scripts\activate.bat" || goto :err
python -m pip install -q --upgrade pip
python -m pip install -q numpy moderngl PySide6 Pillow pyinstaller || goto :err

echo ==^> pyinstaller
if exist "build" rmdir /s /q "build"
if exist "dist\LINQS Layout" rmdir /s /q "dist\LINQS Layout"
python -m PyInstaller --noconfirm --clean packaging\LINQSLayout-win.spec || goto :err

for /f "delims=" %%v in ('python packaging\_version.py') do set "APPVER=%%v"
echo ==^> version !APPVER!

where iscc >nul 2>nul
if errorlevel 1 (
  echo [note] Inno Setup ^(iscc^) not found on PATH; skipping installer build.
  echo        App folder: dist\LINQS Layout\LINQS Layout.exe
  goto :done
)

echo ==^> installer
iscc /DMyAppVersion=!APPVER! packaging\windows\installer.iss || goto :err
echo Built: dist\LINQS-Layout-Setup-!APPVER!.exe

:done
echo Done.
endlocal
exit /b 0

:err
echo [error] build failed.
endlocal
exit /b 1
