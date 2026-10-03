@echo off
setlocal
cd /d "%~dp0"
title Building Threads Collage

echo.
echo  Threads Collage : one time build of the app and its installer
echo  ==============================================================
echo  Produces ThreadsCollage_Setup_[version].exe, a normal installer you can
echo  run on this PC or copy to any other Windows 10/11 PC. Later updates are
echo  installed from inside the app with "Install update...".
echo.

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
rem on GitHub: use the Python the workflow set up
if defined CI set PY=python

%PY% --version >nul 2>nul
if errorlevel 1 (
  echo  Python was not found.
  echo  Install Python 3.11 or newer from https://www.python.org/downloads/windows/
  echo  and tick "Add python.exe to PATH" during setup, then run this file again.
  if not defined CI pause
  exit /b 1
)

echo  [1/5] Creating a private Python environment...
if not exist .venv %PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat

echo  [2/5] Installing Pillow, HEIC support, drag and drop, PyInstaller...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || goto :fail

echo  [3/5] Drawing the app icon...
python make_icon.py || goto :fail
python -c "import re;print(re.search(r'^APP_VERSION = .([0-9.]+).', open('threads_collage.py', encoding='utf-8').read(), re.M).group(1))" > appver.txt
set /p APPVER=<appver.txt
del appver.txt
echo        App version %APPVER%

echo  [4/5] Packaging the app...
pyinstaller --noconfirm --clean --onedir --windowed --name ThreadsCollage --icon icon.ico ^
  --add-data "threads_collage.py;." --add-data "icon.ico;." --add-data "icon.png;." ^
  --version-file version_info.txt ^
  --collect-submodules PIL --collect-all tkinterdnd2 --collect-all pillow_heif ^
  launcher.py || goto :fail

echo  [5/5] Building the installer...
call :find_nsis
if not defined MAKENSIS (
  echo        NSIS, the free installer builder, is not installed. Installing it now...
  echo        Windows may ask for permission once.
  winget install -e --id NSIS.NSIS --accept-source-agreements --accept-package-agreements >nul 2>nul
  call :find_nsis
)
if not defined MAKENSIS (
  echo.
  echo  The app was built, but NSIS could not be installed automatically.
  echo  Install it from https://nsis.sourceforge.io/Download and run this file again
  echo  to get the installer. Meanwhile the app itself is ready to use here:
  echo     dist\ThreadsCollage\ThreadsCollage.exe
  if not defined CI pause
  exit /b 1
)
"%MAKENSIS%" /V2 /DAPP_VERSION=%APPVER% installer.nsi || goto :fail

echo.
echo  Done. ThreadsCollage_Setup_%APPVER%.exe is in this folder.
echo  Double click it to install Threads Collage on this PC, or copy it to
echo  any other Windows 10/11 PC and install it there. No Python needed.
echo.
if not defined CI explorer /select,"%~dp0ThreadsCollage_Setup_%APPVER%.exe"
if not defined CI pause
exit /b 0

:find_nsis
set MAKENSIS=
if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
if exist "%ProgramFiles%\NSIS\makensis.exe" set "MAKENSIS=%ProgramFiles%\NSIS\makensis.exe"
if exist "%LOCALAPPDATA%\Programs\NSIS\makensis.exe" set "MAKENSIS=%LOCALAPPDATA%\Programs\NSIS\makensis.exe"
if not defined MAKENSIS (
  for /f "delims=" %%m in ('where makensis 2^>nul') do set "MAKENSIS=%%m"
)
exit /b 0

:fail
echo.
echo  Build failed. Scroll up for the error message.
if not defined CI pause
exit /b 1
