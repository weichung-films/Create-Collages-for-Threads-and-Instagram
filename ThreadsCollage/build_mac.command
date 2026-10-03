#!/bin/bash
# Threads Collage : one time build of ThreadsCollage.app and its installer
# (ThreadsCollage_[version].dmg) for macOS.
# Double click this file in Finder (first time: Control click > Open > Open).
# You only need to do this once. Later updates are installed from inside the
# app with "Install update...".

cd "$(dirname "$0")" || exit 1
LAUNCHER_VERSION="1.2.0"     # keep in step with launcher.py
BUNDLE_ID="local.threadscollage.app"

echo ""
echo "  Threads Collage : one time build of the app and its installer"
echo "  =============================================================="
echo ""

fail() { echo ""; echo "  Build failed: $1"; echo ""; [ -n "$CI" ] || read -r -p "  Press Return to close." _; exit 1; }

# 1. Find a Python 3.9+ with a modern Tk (8.6). The python.org installer has one;
#    Apple's built in /usr/bin/python3 ships Tk 8.5, which draws this app badly.
PY=""
CANDS=(/Library/Frameworks/Python.framework/Versions/3.*/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3)
[ -n "$CI" ] && CANDS=(python3)          # on GitHub: use the Python the workflow set up
for cand in "${CANDS[@]}"; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys, tkinter; assert sys.version_info >= (3, 9) and tkinter.TkVersion >= 8.6' >/dev/null 2>&1; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "  A suitable Python was not found."
  echo "  Install Python 3.11 or newer from https://www.python.org/downloads/macos/"
  echo "  (the standard macOS installer), then double click this file again."
  echo ""
  echo "  If you use Homebrew instead:  brew install python python-tk"
  [ -n "$CI" ] || read -r -p "  Press Return to close." _
  exit 1
fi
echo "  Using $("$PY" --version) at $PY"

# 2. Private environment with the libraries
echo "  [1/5] Creating a private Python environment..."
[ -d .venv-mac ] || "$PY" -m venv .venv-mac || fail "could not create the environment"
# shellcheck disable=SC1091
source .venv-mac/bin/activate

echo "  [2/5] Installing Pillow, HEIC support, drag and drop, PyInstaller..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt pyinstaller || fail "could not install the libraries"

# 3. Icon, version, then build the .app (launcher plus bundled app file)
echo "  [3/5] Drawing the app icon..."
python make_icon.py || fail "could not draw the icon"
APPVER=$(python -c "import re;print(re.search(r'^APP_VERSION = .([0-9.]+).', open('threads_collage.py', encoding='utf-8').read(), re.M).group(1))")
echo "        App version $APPVER"
ICON_ARGS=()
[ -f icon.icns ] && ICON_ARGS=(--icon icon.icns)

echo "  [4/5] Packaging ThreadsCollage.app ..."
pyinstaller --noconfirm --clean --windowed --name ThreadsCollage \
  --osx-bundle-identifier "$BUNDLE_ID" "${ICON_ARGS[@]}" \
  --add-data "threads_collage.py:." --add-data "icon.png:." \
  --collect-submodules PIL --collect-all tkinterdnd2 --collect-all pillow_heif \
  launcher.py || fail "PyInstaller reported an error (see above)"

APP="dist/ThreadsCollage.app"
PLIST="$APP/Contents/Info.plist"
[ -f "$PLIST" ] || fail "the app bundle was not created"

# 5. Version details, Retina, accept images and folders dropped on the Dock icon
echo "  [5/5] Setting version details, signing, and making the installer..."
PB=/usr/libexec/PlistBuddy
"$PB" -c "Set :CFBundleShortVersionString $LAUNCHER_VERSION" "$PLIST" 2>/dev/null || "$PB" -c "Add :CFBundleShortVersionString string $LAUNCHER_VERSION" "$PLIST"
"$PB" -c "Set :CFBundleVersion $LAUNCHER_VERSION" "$PLIST" 2>/dev/null || "$PB" -c "Add :CFBundleVersion string $LAUNCHER_VERSION" "$PLIST"
"$PB" -c "Set :NSHighResolutionCapable true" "$PLIST" 2>/dev/null || "$PB" -c "Add :NSHighResolutionCapable bool true" "$PLIST"
"$PB" -c "Set :CFBundleDisplayName Threads Collage" "$PLIST" 2>/dev/null || "$PB" -c "Add :CFBundleDisplayName string Threads Collage" "$PLIST"
"$PB" -c "Delete :CFBundleDocumentTypes" "$PLIST" 2>/dev/null
"$PB" -c "Add :CFBundleDocumentTypes array" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0 dict" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeName string Images and folders" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0:CFBundleTypeRole string Viewer" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0:LSHandlerRank string Alternate" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes array" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:0 string public.image" "$PLIST"
"$PB" -c "Add :CFBundleDocumentTypes:0:LSItemContentTypes:1 string public.folder" "$PLIST"

# Editing Info.plist invalidates PyInstaller's signature; Apple Silicon Macs
# refuse to open unsigned apps, so re-sign locally (ad hoc, no account needed).
codesign --force --deep --sign - "$APP" || fail "could not sign the app"

rm -rf ThreadsCollage.app
ditto "$APP" ThreadsCollage.app || fail "could not copy the app"

# Installer: a disk image with the app and a shortcut to Applications
DMG="ThreadsCollage_${APPVER}.dmg"
STAGE=$(mktemp -d)
ditto ThreadsCollage.app "$STAGE/ThreadsCollage.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
ok=""
for attempt in 1 2 3; do              # hdiutil is occasionally "busy"; retry briefly
  if hdiutil create -volname "Threads Collage $APPVER" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null; then
    ok=1; break
  fi
  echo "        disk image attempt $attempt failed, retrying..."; sleep 5
done
[ -n "$ok" ] || fail "could not create the disk image"
rm -rf "$STAGE"

echo ""
echo "  Done. In this folder you now have:"
echo "    $DMG   the installer: open it and drag the app into Applications."
echo "                               Copy it to any other Mac with the same kind of chip."
echo "    ThreadsCollage.app            the app itself, ready to use."
echo ""
[ -n "$CI" ] || open -R "$DMG" 2>/dev/null
[ -n "$CI" ] || read -r -p "  Press Return to close." _
