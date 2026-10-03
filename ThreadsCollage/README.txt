THREADS COLLAGE  (Windows 10 / 11 and macOS)
============================================

Turns photos into collages of 2 or 3 images for Threads. Every photo is
always shown in full; nothing is cropped. Windows and Mac run the same app.

READY MADE INSTALLERS WITHOUT INSTALLING ANYTHING (recommended for Mac)
  GitHub can build the Mac app (Apple Silicon and Intel) and the Windows
  installer on its own machines, so your computers need nothing installed.
  Follow GET_THE_MAC_APP_FROM_GITHUB.txt (about 10 minutes, once).

INSTALLING FROM AN INSTALLER (no Python, nothing to build)
  Windows  Double click ThreadsCollage_Setup_[version].exe. If Windows shows
           "Windows protected your PC", click More info, then Run anyway
           (the installer is not signed with a paid certificate). It installs
           for your user only, without an administrator prompt, adds a Start
           menu shortcut, and can add a desktop shortcut. Remove it any time in
           Settings > Apps.
  Mac      Open ThreadsCollage_[version].dmg and drag ThreadsCollage into the
           Applications shortcut. On a Mac other than the one that built it,
           the first launch is blocked once: open System Settings > Privacy &
           Security and click "Open Anyway" (older macOS: Control click the app
           and choose Open). The Mac installer only runs on the same kind of
           chip as the Mac that built it (Apple Silicon or Intel).

MAKING THE INSTALLERS (once per system)
  Windows (about 5 minutes)
    1. Install Python 3.11 or newer from https://www.python.org/downloads/windows/
       During setup tick "Add python.exe to PATH".
    2. Double click build_exe.bat. It builds the app, draws its icon, installs
       the free NSIS installer builder if needed (Windows may ask permission
       once), and creates ThreadsCollage_Setup_[version].exe in this folder.
    (Prefer not to build? Double click run_without_building.bat instead.)

  Mac (about 5 minutes)
    1. Install Python 3.11 or newer from https://www.python.org/downloads/macos/
       using the standard macOS installer. (Apple's built in Python is too old
       for this app's interface.)
    2. In Finder, Control click build_mac.command and choose Open, then Open
       again. It builds ThreadsCollage.app, draws its icon, and creates
       ThreadsCollage_[version].dmg in this folder.
    (Prefer not to build? Control click run_mac_without_building.command > Open.)

    If macOS says a .command file "cannot be opened", open Terminal, type
        xattr -dr com.apple.quarantine 
    (with a space at the end), drag this folder onto the Terminal window,
    press Return, then try again.

  You only need new installers for setting up new computers. Computers that
  already have the app update themselves through "Install update...".

UPDATING (no rebuild, no reinstall, same on Windows and Mac)
  When you receive a new threads_collage.py (or a .zip containing it):
    * click "Install update..." at the bottom left of the app, or
    * drag the file onto the app window.
  The app checks the file, keeps a backup of the current version, installs
  the new one and restarts. Your settings are kept.
  If an update ever fails to start, the previous version opens automatically.

  Where things live:
    Windows  %LOCALAPPDATA%\ThreadsCollage\app\threads_collage.py   the app
             %LOCALAPPDATA%\ThreadsCollage\settings.json            settings
    Mac      ~/Library/Application Support/ThreadsCollage/app/threads_collage.py
             ~/Library/Application Support/ThreadsCollage/settings.json

MAC NOTES
  * Keyboard shortcuts use Command instead of Ctrl (Command + / Command -
    for text size, Command 0 to reset). Command Q quits and saves settings.
  * Right click, two finger click, or Control click a thumbnail for its menu.
  * You can drop photos or a folder onto the app's Dock icon.
  * Threads Collage > About Threads Collage opens the version window.

TEXT SIZE
  * Bottom left: "Text size" scales the whole interface from 100% to 250%
    in 10% steps. Keyboard: Ctrl + and Ctrl - ; Ctrl 0 resets to 100%
    (Command instead of Ctrl on Mac).
  * The left panel scrolls when large text makes it taller than the window;
    the bottom row (text size, version, About, updates) always stays visible.
  * Remembered between sessions and updates.
  * The system's own message boxes follow the system display settings.

VERSION INFORMATION
  * The version is shown in the title bar and at the bottom left.
  * Click "About" (or the version number) for full details: app version and
    release date, launcher version, where the app and settings live, and the
    complete version history. "Copy version info" copies it for support.
  * After an update, a "What's new" note appears once.
  * Each batch's collage_index.csv records the app version used.
  * ThreadsCollage.exe carries Windows file version details (right click >
    Properties > Details); ThreadsCollage.app shows its version in Finder
    (Get Info). These describe the launcher, which is built once; the app
    version inside is the one shown in About.

FILE NAMES
  * Under "Save to", type a file name. Files are saved as name_001.jpg,
    name_002.jpg, ... Spaces become "_" as you type; characters Windows
    does not allow (\ / : * ? " < > |) are removed.
  * Single collages continue the numbering already in the folder, so
    nothing is overwritten. Each batch starts at 001 in its own folder.

SINGLE COLLAGE TAB
  * Drop 2 or 3 photos, or click Add. Arrangement "Auto" picks the layout
    that shows the photos largest; or choose one yourself (photo 1 is the
    "one" in the one plus two layouts).

BATCH TAB
  * Add a folder (or many files), or drag them onto the window.
  * Thumbnails of every photo appear on the photo board above the preview:
      - click a thumbnail to include or exclude it (excluded ones are dimmed)
      - drag a thumbnail to reorder; Order switches to "My order" automatically
      - right click for more: preview its collage, move to start or end,
        open the photo, remove it from the list
      - neighbours share a collage: the coloured band under each thumbnail
        shows which collage it belongs to; the white outline marks the
        collage shown in the preview
      - drag the divider between the board and the preview to resize them
  * Order:  Date taken keeps photos from the same moment together;
            File name follows your naming; Shape groups similar shapes for
            the least empty space; My order keeps your dragged arrangement.
  * Group:  Best fit mixes 2s and 3s for the best looking set; or prefer 3
            or prefer 2 per collage. Every collage always has 2 or 3 photos.
  * Click any planned collage to preview it, then "Create all collages".
  * Output goes to a new Batch_date_time folder, with collage_index.csv
    listing which photos went into each collage.

WATERMARK TAB
  * Type the author (for example "© Your Name"). Leave it empty for none.
  * Choose any installed font and style, or "Font file..." for a .ttf/.otf.
  * Size is in pixels for a 1080 px wide image and scales with other sizes;
    very long text shrinks automatically to fit.
  * The text sits in its own strip in the bottom right corner, below the
    photos. The photos are fitted above it, so it never covers them.
  * Your watermark settings are remembered between sessions and updates.

WHAT EACH FILE CONTAINS
  Size        1080 x 1350 px, 4:5 (default). Also 1440 x 1800, 1080 x 1080, or
              "Trim empty space": 1080 wide, height shrinks to remove empty
              bands (never taller than 4:5, never shorter than 1.91:1).
  Format      Baseline JPEG, 8 bit, 4:4:4 chroma.
  File size   As close to 2 MB as possible without going over: the app picks
              the highest quality that fits. Files are never padded, so a
              simple image may come out smaller even at maximum quality.
              Meta's published Threads maximum is 8 MB.
  Colour      Converted from each photo's own profile (iPhone Display P3,
              Adobe RGB, CMYK...) into sRGB, with the sRGB profile embedded.
  DPI         150 ppi tag (phones ignore DPI; pixel size is what matters).
  Privacy     All EXIF removed: no GPS location, serials or capture dates.
  Inputs      JPG, PNG, TIFF, WEBP, BMP, and iPhone HEIC.
