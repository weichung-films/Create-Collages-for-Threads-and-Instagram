"""
Threads Collage launcher
========================
This tiny file is what gets built into ThreadsCollage.exe (Windows) or
ThreadsCollage.app (macOS), ONCE.

It does not contain the app itself. On every start it runs the app file
    Windows: %LOCALAPPDATA%\\ThreadsCollage\\app\\threads_collage.py
    macOS:   ~/Library/Application Support/ThreadsCollage/app/threads_collage.py
so updating the app is just replacing that one file (the app's own
"Install update..." button does this for you). No rebuild, no reinstall,
and settings in %LOCALAPPDATA%\\ThreadsCollage\\settings.json are kept.

First run, or when this exe carries a NEWER app than the installed one,
the bundled copy is installed automatically. If an installed update fails
to start, the previous version (threads_collage.bak.py) is used instead.
"""

import importlib.util
import os
import re
import shutil
import sys
import traceback

# ---------------------------------------------------------------------------
# Import everything the app (and reasonable future versions) may use, so the
# packager bundles it into the exe. Updates can then use these freely.
# ---------------------------------------------------------------------------
import csv, io, itertools, json, math, queue, random, subprocess, threading     # noqa: E401,F401
import time, zipfile, hashlib, datetime, tempfile, glob, collections            # noqa: E401,F401
import concurrent.futures, urllib.request, ctypes                               # noqa: E401,F401
import tkinter, tkinter.ttk, tkinter.filedialog, tkinter.colorchooser           # noqa: E401,F401
import tkinter.messagebox, tkinter.simpledialog, tkinter.font                   # noqa: E401,F401
from PIL import (Image, ImageCms, ImageChops, ImageDraw, ImageEnhance,          # noqa: F401
                 ImageFilter, ImageFont, ImageOps, ImageStat, ImageTk)
try:
    import pillow_heif                                                           # noqa: F401
except Exception:
    pass
try:
    import tkinterdnd2                                                           # noqa: F401
except Exception:
    pass

LAUNCHER_VERSION = "1.2.0"      # keep in step with version_info.txt and build_mac.command
os.environ["THREADS_COLLAGE_LAUNCHER"] = LAUNCHER_VERSION

APP_FILE = "threads_collage.py"
BACKUP_FILE = "threads_collage.bak.py"
_VER = re.compile(r"""^APP_VERSION\s*=\s*["']([0-9][0-9.]*)["']""", re.M)


def _version(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            m = _VER.search(f.read())
        return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    except Exception:
        return (0,)


def _bundle_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def _app_dir():
    if sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "ThreadsCollage", "app")
    os.makedirs(d, exist_ok=True)
    return d


def _install_bundled_if_newer(app_dir):
    bundled = os.path.join(_bundle_dir(), APP_FILE)
    installed = os.path.join(app_dir, APP_FILE)
    if os.path.exists(bundled) and _version(bundled) > _version(installed):
        if os.path.exists(installed):
            shutil.copy2(installed, os.path.join(app_dir, BACKUP_FILE))
        shutil.copy2(bundled, installed)
    return installed


def _run(path):
    spec = importlib.util.spec_from_file_location("threads_collage", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["threads_collage"] = mod
    spec.loader.exec_module(mod)
    mod.run_gui()


def _error_box(msg):
    try:
        root = tkinter.Tk()
        root.withdraw()
        tkinter.messagebox.showerror("Threads Collage", msg)
        root.destroy()
    except Exception:
        print(msg, file=sys.stderr)


def main():
    app_dir = _app_dir()
    installed = _install_bundled_if_newer(app_dir)
    try:
        _run(installed)
        return
    except SystemExit:
        return
    except Exception:
        err = traceback.format_exc()
    backup = os.path.join(app_dir, BACKUP_FILE)
    if os.path.exists(backup):
        _error_box("The installed update could not start, so the previous version will open.\n\n"
                   + err[-1500:])
        # make the working backup the active version again
        shutil.copy2(installed, os.path.join(app_dir, "threads_collage.failed.py"))
        shutil.copy2(backup, installed)
        try:
            _run(installed)
            return
        except Exception:
            err = traceback.format_exc()
    _error_box("Threads Collage could not start.\n\n" + err[-1500:])


if __name__ == "__main__":
    main()
