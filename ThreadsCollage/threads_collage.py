"""
Threads Collage
===============
A desktop app for Windows and macOS that turns photos into collages of 2 or 3 images,
optimised for Threads on a phone. Every photo is shown in full: nothing
is ever cropped.

Two modes
  * Single: pick 2 or 3 photos, choose or auto pick an arrangement, export.
  * Batch:  drop a whole folder; photos are grouped into collages of 2 or 3
            automatically and all collages are written in one run, together
            with a CSV index of which photos went into which collage.

How "no crop" works
  Each arrangement (side by side, stacked, one beside two, and so on) is
  sized from the photos' own aspect ratios, so every cell has exactly the
  shape of its photo. The app tries every arrangement and every ordering
  and keeps the one that shows the photos largest, with a small bonus for
  keeping the photos similar in size. Any leftover space becomes background.

Output
  * JPEG, 8 bit, standard sRGB IEC61966-2.1 profile embedded
  * 1080 x 1350 px (4:5) by default; 1440 x 1800, 1080 x 1080, or
    "trim empty space" (1080 wide, height shrinks to fit, never taller than 4:5)
  * 150 ppi tag, all EXIF removed, 4:4:4 chroma; quality chosen so each file is
    as close to 2 MB as possible without exceeding it (Meta's maximum is 8 MB)

Run:   python threads_collage.py
Build: build_exe.bat
"""

import csv
import io
import itertools
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile

APP_VERSION = "2.4.1"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
MOD = "Command" if IS_MAC else "Control"            # keyboard modifier for shortcuts
MOD_LABEL = "⌘" if IS_MAC else "Ctrl"
APP_RELEASE_DATE = "2026-10-03"

# Newest first. Shown in About and in the "What's new" notice after an update.
CHANGELOG = [
    ("2.4.1", "2026-10-03", [
        "Installers: the one time build now also creates ThreadsCollage_Setup.exe (Windows) and a .dmg (Mac) "
        "that install directly on any other computer",
        "App icon in the window, taskbar, Dock, Start menu and installers",
    ]),
    ("2.4.0", "2026-10-03", [
        "macOS version: same app, built as ThreadsCollage.app; updates install the same way on both systems",
        "Mac: settings in ~/Library/Application Support/ThreadsCollage; trackpad scrolling; right click and "
        "Control click on thumbnails; ⌘ shortcuts for text size; ⌘Q saves settings; About in the app menu; "
        "photos can be dropped on the Dock icon",
        "Interface text sizes now follow each system's standard size",
    ]),
    ("2.3.0", "2026-10-03", [
        "Custom output file name: files are saved as [your name]_001.jpg, _002.jpg, ...",
        "Spaces typed in the file name become \"_\" automatically; characters Windows "
        "does not allow in file names are removed",
        "Single collages continue the numbering in the folder, so existing files are never overwritten",
        "Exported JPEGs are tagged 150 dpi",
        "File size: highest quality that keeps each file as close to 2 MB as possible "
        "without going over (well inside Meta's published 8 MB Threads maximum)",
    ]),
    ("2.2.0", "2026-10-03", [
        "Text size: scale the whole interface from 100% to 250% in 10% steps "
        "(bottom left, or Ctrl + and Ctrl −; Ctrl 0 resets); remembered between sessions",
        "Left panel scrolls when large text makes it taller than the window",
        "About window with full version details and version history",
        "\"What's new\" notice the first time the app opens after an update",
        "App version recorded in each batch's collage_index.csv",
        "Version details in the ThreadsCollage.exe file properties (after rebuilding)",
    ]),
    ("2.1.0", "2026-10-03", [
        "Photo board in Batch: thumbnails of every photo; click to include or exclude, drag to reorder",
        "Author watermark in the bottom right corner with any font, size and colour; never covers photos",
        "Removed the Corners option",
    ]),
    ("2.0.0", "2026-10-03", [
        "Every photo shown in full: no cropping, layouts sized to each photo's shape",
        "Batch tab: turn a whole folder into collages of 2 or 3 automatically",
        "In app updates: install new versions without rebuilding; settings kept",
    ]),
    ("1.0.0", "2026-10-03", [
        "First release: 2 or 3 photo collages, sRGB JPEG sized for Threads",
    ]),
]

from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageFilter, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIC_OK = True
except Exception:
    HEIC_OK = False

APP_NAME = "Threads Collage"

# --------------------------------------------------------------------------
# Output specification
# --------------------------------------------------------------------------
# label, width, max height, trim empty space
SIZES = [
    ("4:5 portrait, 1080 x 1350 (recommended)", 1080, 1350, False),
    ("4:5 portrait, 1440 x 1800 (extra detail)", 1440, 1800, False),
    ("1:1 square, 1080 x 1080", 1080, 1080, False),
    ("Trim empty space (1080 wide, up to 4:5)", 1080, 1350, True),
]
MIN_TRIM_HEIGHT = 566          # never shorter than 1.91:1 when trimming
OUTPUT_DPI = (150, 150)
TARGET_BYTES = 2 * 1024 * 1024        # aim: as close to 2 MB as possible, never above
MAX_FILE_BYTES = 8 * 1024 * 1024      # Meta's published Threads maximum (8 MB)
MAX_QUALITY = 100
MIN_QUALITY = 20                      # only reached by extreme, noise-like images
CACHE_LONG_SIDE = 2800

IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp"]
if HEIC_OK:
    IMAGE_EXTS += [".heic", ".heif"]

SRGB_PROFILE = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
SRGB_BYTES = SRGB_PROFILE.tobytes()
_BPC = getattr(getattr(ImageCms, "Flags", None), "BLACKPOINTCOMPENSATION", 0x2000)
_INTENT = getattr(getattr(ImageCms, "Intent", None), "RELATIVE_COLORIMETRIC", 1)
RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


# --------------------------------------------------------------------------
# Loading and colour management
# --------------------------------------------------------------------------
def load_as_srgb(path, max_side=CACHE_LONG_SIDE):
    """Open any image, fix orientation, shrink to max_side, convert to sRGB (RGB or RGBA)."""
    im = Image.open(path)
    if im.format == "JPEG" and max(im.size) > max_side * 2:
        im.draft(im.mode, (max_side, max_side))       # fast DCT downscale while decoding
    im.load()
    icc = im.info.get("icc_profile")
    im = ImageOps.exif_transpose(im)

    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        im = im.point(lambda v: v * (1 / 256.0)).convert("L")
    elif im.mode == "F":
        im = im.convert("L")
    if im.mode == "P":
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.mode == "1":
        im = im.convert("L")

    if max(im.size) > max_side:                       # shrink before colour conversion: much faster
        s = max_side / float(max(im.size))
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))),
                       RESAMPLE, reducing_gap=3.0)

    has_alpha = im.mode in ("RGBA", "LA", "PA")
    alpha = im.getchannel("A") if has_alpha else None
    if has_alpha:
        im = im.convert("L" if im.mode == "LA" else "RGB")

    converted = False
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            im = ImageCms.profileToProfile(im, src, SRGB_PROFILE, renderingIntent=_INTENT,
                                           outputMode="RGB", flags=_BPC)
            converted = True
        except Exception:
            converted = False
    if not converted and im.mode != "RGB":
        im = im.convert("RGB")          # untagged: assume already sRGB

    if alpha is not None:
        if alpha.size != im.size:
            alpha = alpha.resize(im.size)
        im.putalpha(alpha)
    return im


THUMB_SIZE = 112


def make_thumb(path, size=THUMB_SIZE):
    """Small colour managed thumbnail (RGB) for the photo board."""
    im = load_as_srgb(path, size)
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (235, 235, 235))
        bg.paste(im, mask=im.getchannel("A"))
        im = bg
    return im


def read_meta(path):
    """Cheap header read: (aspect ratio after EXIF rotation, sortable date string)."""
    with Image.open(path) as im:
        w, h = im.size
        try:
            ex = im.getexif()
        except Exception:
            ex = {}
    date = None
    try:
        if ex.get(0x0112, 1) in (5, 6, 7, 8):
            w, h = h, w
        date = ex.get_ifd(0x8769).get(36867) or ex.get(306)
    except Exception:
        pass
    if not date:
        date = time.strftime("%Y:%m:%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
    return w / float(h), str(date)


# --------------------------------------------------------------------------
# No crop layout engine
#   A layout is a tree: ("L", i) is photo i, ("H", kids) places kids side by
#   side with equal heights, ("V", kids) stacks kids with equal widths.
#   For any node, width = alpha * height + beta (beta accounts for gutters),
#   which lets us size the whole tree exactly from the photos' aspect ratios.
# --------------------------------------------------------------------------
def L(i): return ("L", i)
def H(*k): return ("H", k)
def V(*k): return ("V", k)


ARRANGEMENTS = {
    2: [("Side by side", H(L(0), L(1))),
        ("Stacked", V(L(0), L(1)))],
    3: [("Three in a row", H(L(0), L(1), L(2))),
        ("Three stacked", V(L(0), L(1), L(2))),
        ("One left, two stacked right", H(L(0), V(L(1), L(2)))),
        ("Two stacked left, one right", H(V(L(1), L(2)), L(0))),
        ("One on top, two below", V(L(0), H(L(1), L(2)))),
        ("Two on top, one below", V(H(L(1), L(2)), L(0)))],
}
AUTO = "Auto (shows photos largest)"


def _affine(node, asp, g):
    if node[0] == "L":
        return asp[node[1]], 0.0
    parts = [_affine(k, asp, g) for k in node[1]]
    n = len(parts)
    if node[0] == "H":
        return sum(a for a, _ in parts), sum(b for _, b in parts) + g * (n - 1)
    s = sum(1.0 / a for a, _ in parts)
    return 1.0 / s, (sum(b / a for a, b in parts) - g * (n - 1)) / s


def _place(node, x, y, h, asp, g, out):
    if node[0] == "L":
        out[node[1]] = (x, y, asp[node[1]] * h, h)
    elif node[0] == "H":
        for k in node[1]:
            a, b = _affine(k, asp, g)
            _place(k, x, y, h, asp, g, out)
            x += a * h + b + g
    else:
        a, b = _affine(node, asp, g)
        w = a * h + b
        for k in node[1]:
            ka, kb = _affine(k, asp, g)
            kh = (w - kb) / ka
            _place(k, x, y, kh, asp, g, out)
            y += kh + g


def _remap(node, perm):
    if node[0] == "L":
        return ("L", perm[node[1]])
    return (node[0], tuple(_remap(k, perm) for k in node[1]))


def fit_tree(tree, asp, cw, ch, g):
    """Largest size of the tree inside cw x ch. Returns (rects, block_w, block_h) or None."""
    a, b = _affine(tree, asp, g)
    h = min(ch, (cw - b) / a)
    if h <= 0:
        return None
    w = a * h + b
    out = {}
    _place(tree, 0.0, 0.0, h, asp, g, out)
    if any(r[2] < 24 or r[3] < 24 for r in out.values()):
        return None
    return out, w, h


def score_rects(rects, cw, ch):
    areas = [r[2] * r[3] for r in rects.values()]
    coverage = sum(areas) / float(cw * ch)
    balance = min(areas) / max(areas)
    return coverage * (0.7 + 0.3 * math.sqrt(balance))


_LAYOUT_CACHE = {}


def best_layout(asp, cw, ch, g, choice=AUTO):
    """Return dict(score, rects, w, h, name) for the chosen or best arrangement (memoised)."""
    key = (tuple(round(a, 4) for a in asp), round(cw), round(ch), round(g), choice)
    if key in _LAYOUT_CACHE:
        return _LAYOUT_CACHE[key]
    n = len(asp)
    options = ARRANGEMENTS[n]
    if choice != AUTO:
        options = [o for o in options if o[0] == choice] or options
        perms = [tuple(range(n))]
    else:
        perms = list(itertools.permutations(range(n))) if n == 3 else [tuple(range(n))]
    best = None
    for name, tree in options:
        for p in perms:
            r = fit_tree(_remap(tree, p), asp, cw, ch, g)
            if not r:
                continue
            s = score_rects(r[0], cw, ch)
            if best is None or s > best["score"] + 1e-9:
                best = {"score": s, "rects": r[0], "w": r[1], "h": r[2], "name": name}
    if len(_LAYOUT_CACHE) > 20000:
        _LAYOUT_CACHE.clear()
    _LAYOUT_CACHE[key] = best
    return best


# --------------------------------------------------------------------------
# Composition and saving
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Watermark: author text in the bottom right corner, drawn in a strip that
# is reserved below the photos, so it can never cover any part of a photo.
# Font sizes are given for a 1080 px wide image and scale with other sizes.
# --------------------------------------------------------------------------
FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),
             "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             os.path.expanduser("~/Library/Fonts"), "/Library/Fonts", "/System/Library/Fonts"]
FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
PREFERRED_FONTS = ["Segoe UI", "Helvetica Neue", "Arial", "Helvetica", "DejaVu Sans", "Liberation Sans"]
_FONT_OBJ_CACHE = {}


def scan_fonts():
    """Return {family: {style: "path|index"}} for every installed font."""
    from PIL import ImageFont
    found = {}
    seen = set()
    for d in FONT_DIRS:
        if not d or not os.path.isdir(d):
            continue
        for dp, _, fs in os.walk(d):
            for f in fs:
                if not f.lower().endswith(FONT_EXTS):
                    continue
                path = os.path.join(dp, f)
                if path.lower() in seen:
                    continue
                seen.add(path.lower())
                for idx in range(4 if f.lower().endswith((".ttc", ".otc")) else 1):
                    try:
                        fam, style = ImageFont.truetype(path, 12, index=idx).getname()
                    except Exception:
                        break
                    if fam:
                        found.setdefault(fam, {}).setdefault(style or "Regular", "%s|%d" % (path, idx))
    return found


def get_font(spec, px):
    """spec is "path|index" (or a plain path). Falls back to a built in font."""
    from PIL import ImageFont
    key = (spec, px)
    if key not in _FONT_OBJ_CACHE:
        font = None
        if spec:
            path, _, idx = spec.partition("|")
            try:
                font = ImageFont.truetype(path, px, index=int(idx or 0))
            except Exception:
                font = None
        if font is None:
            try:
                font = ImageFont.load_default(px)
            except Exception:
                font = ImageFont.load_default()
        _FONT_OBJ_CACHE[key] = font
    return _FONT_OBJ_CACHE[key]


def watermark_metrics(s):
    """(font, footer height reserved below the photos, bottom/right padding) or (None, 0, 0)."""
    text = (s.get("wm_text") or "").strip()
    if not text:
        return None, 0, 0
    px = max(6, int(round(s.get("wm_size", 28) * s["W"] / 1080.0)))
    m, g = s["margin"], s["gutter"]
    pad = max(m, round(10 * s["W"] / 1080.0))          # keep text off the very edge
    font = get_font(s.get("wm_font"), px)
    while px > 6:                                       # shrink long text to fit the width
        b = font.getbbox(text)
        if b[2] - b[0] <= s["W"] - 2 * pad:
            break
        px = max(6, int(px * 0.92))
        font = get_font(s.get("wm_font"), px)
    try:
        asc, desc = font.getmetrics()
        th = asc + desc
    except Exception:
        b = font.getbbox("Ag")
        th = b[3] - b[1]
    footer = th + max(g, round(8 * s["W"] / 1080.0)) + (pad - m)
    return font, int(footer), int(pad)


def draw_watermark(canvas, s, font, pad):
    text = s["wm_text"].strip()
    d = ImageDraw.Draw(canvas)
    d.text((canvas.width - pad, canvas.height - pad), text, font=font,
           fill=tuple(s.get("wm_color", (136, 136, 136))), anchor="rd")


def compose(images, s):
    """images: list of 2 or 3 PIL images. s: settings dict. Returns (img, meta)."""
    W, Hmax, trim = s["W"], s["H"], s["trim"]
    m, g = s["margin"], s["gutter"]
    font, footer, pad = watermark_metrics(s)
    cw, ch = W - 2 * m, Hmax - 2 * m - footer
    asp = [im.width / float(im.height) for im in images]
    lay = best_layout(asp, cw, ch, g, s.get("layout", AUTO)) if ch > 50 else None
    if lay is None:
        raise ValueError("These photos cannot fit with the current gap, border and watermark size.")

    Hc = Hmax
    if trim:
        Hc = int(min(Hmax, max(MIN_TRIM_HEIGHT, round(lay["h"]) + 2 * m + footer)))
    area_h = Hc - 2 * m - footer                        # photo area sits above the text strip
    ox = m + (cw - lay["w"]) / 2.0
    oy = m + (area_h - lay["h"]) / 2.0

    canvas = Image.new("RGB", (W, Hc), s["bg"])
    warnings, area = [], 0
    for i, im in enumerate(images):
        x, y, w, h = lay["rects"][i]
        x0, y0 = int(round(ox + x)), int(round(oy + y))
        x1, y1 = int(round(ox + x + w)), int(round(oy + y + h))
        tw, th = max(1, x1 - x0), max(1, y1 - y0)
        scale = tw / float(im.width)
        if scale > 1.05:
            warnings.append("A photo is enlarged %d%% and may look soft." % round(scale * 100))
        tile = im.resize((tw, th), RESAMPLE, reducing_gap=3.0 if scale < 0.5 else None)
        if s.get("sharpen", True) and scale < 0.9:
            usm = ImageFilter.UnsharpMask(0.6, 40, 2)
            if tile.mode == "RGBA":
                a = tile.getchannel("A")
                tile = tile.convert("RGB").filter(usm)
                tile.putalpha(a)
            else:
                tile = tile.filter(usm)
        mask = tile.getchannel("A") if tile.mode == "RGBA" else None
        canvas.paste(tile.convert("RGB"), (x0, y0), mask)
        area += tw * th
    photo_bottom = int(round(oy + lay["h"]))
    if font is not None:
        draw_watermark(canvas, s, font, pad)
    meta = {"layout": lay["name"], "coverage": area / float(W * Hc), "warnings": warnings,
            "photo_bottom": photo_bottom}
    return canvas, meta


DEFAULT_BASENAME = "Threads_Collage"
_ILLEGAL_CHARS = '\\/:*?"<>|'
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


def clean_basename_live(text):
    """What the File name box shows while typing: spaces become "_", characters
    Windows forbids in file names are dropped. Returns (cleaned, removed_any)."""
    out, removed = [], False
    for ch in text:
        if ch.isspace():                       # includes full width and other spaces
            out.append("_")
        elif ch in _ILLEGAL_CHARS or ord(ch) < 32:
            removed = True
        else:
            out.append(ch)
    return "".join(out)[:100], removed


def final_basename(text):
    """Name actually used for files: live cleaning plus Windows edge cases."""
    name = clean_basename_live(text)[0].rstrip(". ")
    if not name.strip("_"):                     # empty, or only spaces typed
        return DEFAULT_BASENAME
    if name.split(".")[0].upper() in _RESERVED:
        name += "_"
    return name


def next_number(folder, base):
    """First free number for base_###.jpg in folder, so nothing is overwritten."""
    pat = re.compile(r"^%s_(\d+)\.jpe?g$" % re.escape(base), re.I)
    top = 0
    try:
        for f in os.listdir(folder):
            m = pat.match(f)
            if m:
                top = max(top, int(m.group(1)))
    except Exception:
        pass
    return top + 1


def numbered_name(base, n):
    return "%s_%03d.jpg" % (base, n)


def _encode(img, q):
    """JPEG bytes at quality q. libjpeg's Huffman optimiser needs the whole output to
    fit a fixed buffer (about 1 to 2 bytes per pixel); very detailed photos at high
    quality overflow it ("Suspension not allowed here"). Those sizes are above the
    2 MB target anyway, so we simply measure them without the optimiser."""
    for optimize in (True, False):
        buf = io.BytesIO()
        try:
            img.save(buf, "JPEG", quality=q, subsampling=0, optimize=optimize, progressive=False,
                     dpi=OUTPUT_DPI, icc_profile=SRGB_BYTES)
            return buf.getvalue()
        except OSError:
            if not optimize:
                raise
    return b""


def save_for_threads(img, path, target=TARGET_BYTES):
    """Baseline sRGB JPEG, no EXIF, 150 ppi. Picks the highest quality whose file
    is as close to target (2 MB) as possible without exceeding it. Files are never
    padded: if even maximum quality is smaller than 2 MB, that is the best possible.
    Never exceeds Meta's 8 MB maximum."""
    img = img.convert("RGB")
    best = _encode(img, MAX_QUALITY)
    q_best = MAX_QUALITY
    if len(best) > target:
        lo, hi, best, q_best = MIN_QUALITY, MAX_QUALITY - 1, None, None
        while lo <= hi:                                   # binary search on quality
            mid = (lo + hi) // 2
            data = _encode(img, mid)
            if len(data) <= target:
                best, q_best, lo = data, mid, mid + 1
            else:
                hi = mid - 1
        if best is None:                                  # extremely detailed image
            q_best = MIN_QUALITY
            best = _encode(img, q_best)
            while len(best) > MAX_FILE_BYTES and q_best > 10:
                q_best -= 10
                best = _encode(img, q_best)
    with open(path, "wb") as f:
        f.write(best)
    return q_best, len(best)


# --------------------------------------------------------------------------
# Batch grouping
# --------------------------------------------------------------------------
ORDER_MODES = ["Date taken (photos from the same moment together)",
               "File name",
               "Shape (best fit; ignores order)",
               "My order (drag thumbnails to arrange)"]
CUSTOM_ORDER = 3
GROUP_MODES = ["Best fit (mix of 2 and 3)",
               "Prefer 3 per collage",
               "Prefer 2 per collage"]


def group_sizes(n, mode):
    if n < 2:
        return []
    if mode == 1:                       # prefer 3
        q, r = divmod(n, 3)
        if r == 0:
            return [3] * q
        if r == 1:
            return [3] * (q - 1) + [2, 2]
        return [3] * q + [2]
    if mode == 2:                       # prefer 2
        return [2] * (n // 2) if n % 2 == 0 else [2] * ((n - 3) // 2) + [3]
    return None                         # best fit, decided by DP


def plan_groups(aspects, s, group_mode):
    """Split an ordered list of aspect ratios into consecutive groups of 2 or 3.
    Returns a list of index lists. One leftover photo (n == 1) is impossible."""
    n = len(aspects)
    if n < 2:
        return []
    _, footer, _ = watermark_metrics(s)
    cw, ch, g = s["W"] - 2 * s["margin"], s["H"] - 2 * s["margin"] - footer, s["gutter"]
    cache = {}

    def gscore(i, k):
        key = (i, k)
        if key not in cache:
            b = best_layout(aspects[i:i + k], cw, ch, g)
            cache[key] = (b["score"] * k) if b else -1e9
        return cache[key]

    sizes = group_sizes(n, group_mode)
    if sizes is None:
        NEG = float("-inf")
        dp = [NEG] * (n + 1)
        back = [0] * (n + 1)
        dp[0] = 0.0
        for i in range(2, n + 1):
            for k in (2, 3):
                if i - k >= 0 and dp[i - k] > NEG:
                    v = dp[i - k] + gscore(i - k, k)
                    if v > dp[i]:
                        dp[i], back[i] = v, k
        sizes, i = [], n
        while i > 0:
            sizes.append(back[i])
            i -= back[i]
        sizes.reverse()
    groups, i = [], 0
    for k in sizes:
        groups.append(list(range(i, i + k)))
        i += k
    return groups


# --------------------------------------------------------------------------
# Settings that survive updates, and in app updating
#   The launcher (ThreadsCollage.exe) runs this file from the user's data
#   folder. "Install update" replaces it with a newer threads_collage.py
#   (or a .zip containing one), keeps a backup, and restarts. No rebuild,
#   no reinstall, and settings are kept.
# --------------------------------------------------------------------------
def data_dir():
    """Windows: %LOCALAPPDATA%\\ThreadsCollage   macOS: ~/Library/Application Support/ThreadsCollage"""
    if IS_MAC:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "ThreadsCollage")
    os.makedirs(d, exist_ok=True)
    return d


def wheel_steps(e):
    """Scroll units for a wheel event on Windows (delta 120), macOS (small deltas) and Linux."""
    num = getattr(e, "num", None)
    if num == 4:
        return -2
    if num == 5:
        return 2
    d = getattr(e, "delta", 0) or 0
    if IS_MAC:
        return -d if abs(d) < 120 else -int(d / 120)
    return int(-d / 120) * 2


def open_path(path):
    """Open a folder or file with the system's default app (Explorer, Finder, ...)."""
    try:
        if IS_WIN:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def load_settings():
    try:
        with open(os.path.join(data_dir(), "settings.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        with open(os.path.join(data_dir(), "settings.json"), "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
    except Exception:
        pass


_VER_RE = re.compile(r"""^APP_VERSION\s*=\s*["']([0-9][0-9.]*)["']""", re.M)


def version_of(text):
    m = _VER_RE.search(text)
    return m.group(1) if m else None


def vtuple(v):
    return tuple(int(x) for x in (v or "0").split(".") if x.isdigit())


def inspect_update(path):
    """Read an update (.py, or .zip containing threads_collage.py). Returns (text, version).
    Raises ValueError if it is not a valid Threads Collage app file."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".py")]
            names.sort(key=lambda n: 0 if os.path.basename(n).lower() == "threads_collage.py" else 1)
            text = None
            for n in names:
                t = z.read(n).decode("utf-8")
                if version_of(t) and "def run_gui" in t:
                    text = t
                    break
            if text is None:
                raise ValueError("No Threads Collage app file found in this zip.")
    else:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    ver = version_of(text)
    if not ver or "def run_gui" not in text:
        raise ValueError("This is not a Threads Collage update file.")
    compile(text, "threads_collage.py", "exec")          # refuse files with syntax errors
    return text, ver


def apply_update(text, target=None):
    """Replace the running app file with text, keeping threads_collage.bak.py."""
    target = target or os.path.abspath(__file__)
    folder = os.path.dirname(target)
    if os.path.exists(target):
        shutil.copy2(target, os.path.join(folder, "threads_collage.bak.py"))
    tmp = target + ".new"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, target)
    return target


def restart_app():
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        cmd = [sys.executable, os.path.abspath(sys.argv[0])]
    subprocess.Popen(cmd, close_fds=True)


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, colorchooser, messagebox
    from PIL import ImageTk

    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root = TkinterDnD.Tk()
        DND_OK = True
    except Exception:
        root = tk.Tk()
        DND_OK = False
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root.title("%s %s" % (APP_NAME, APP_VERSION))

    def set_window_icon():
        """Window and taskbar icon from the icon files bundled by the installer builds."""
        dirs = [getattr(sys, "_MEIPASS", ""), os.path.dirname(os.path.abspath(sys.executable)),
                os.path.dirname(os.path.abspath(sys.argv[0] or ".")), os.path.dirname(os.path.abspath(__file__))]
        for d in dirs:
            if not d:
                continue
            ico, png = os.path.join(d, "icon.ico"), os.path.join(d, "icon.png")
            try:
                if IS_WIN and os.path.exists(ico):
                    root.iconbitmap(default=ico)
                    return
                if os.path.exists(png):
                    ui["icon"] = ImageTk.PhotoImage(Image.open(png).resize((256, 256), RESAMPLE))
                    root.iconphoto(True, ui["icon"])
                    return
            except Exception:
                pass
    root.minsize(1100, 720)

    saved = load_settings()

    single = {"paths": [], "images": []}
    batch = {"paths": [], "meta": {}, "order": [], "groups": [], "running": False,
             "excluded": set(), "group_of": {}, "sel": 0}
    cache = {}                         # path -> loaded image (small LRU for previews)
    ui = {"preview": None, "bg": tuple(saved.get("bg", (255, 255, 255)))}
    set_window_icon()

    def pick(key, default, allowed=None):
        v = saved.get(key, default)
        return v if (allowed is None or v in allowed) else default

    out_dir = tk.StringVar(value=pick("out_dir", os.path.join(os.path.expanduser("~"), "Pictures", "Threads Collages")))
    size_var = tk.StringVar(value=pick("size", SIZES[0][0], [s[0] for s in SIZES]))
    layout_var = tk.StringVar(value=AUTO)
    gutter_var = tk.IntVar(value=int(pick("gutter", 16)))
    margin_var = tk.IntVar(value=int(pick("margin", 16)))
    sharpen_var = tk.BooleanVar(value=bool(pick("sharpen", True)))
    order_var = tk.StringVar(value=pick("order", ORDER_MODES[0], ORDER_MODES))
    group_var = tk.StringVar(value=pick("group", GROUP_MODES[0], GROUP_MODES))
    sub_var = tk.BooleanVar(value=bool(pick("subfolders", False)))
    status_var = tk.StringVar()

    def persist():
        save_settings({"out_dir": out_dir.get(), "size": size_var.get(), "gutter": safe_int(gutter_var),
                       "margin": safe_int(margin_var),
                       "sharpen": sharpen_var.get(), "bg": list(ui["bg"]), "order": order_var.get(),
                       "group": group_var.get(), "subfolders": sub_var.get(),
                       "wm_text": wm_text_var.get(), "wm_family": wm_family_var.get(),
                       "wm_style": wm_style_var.get(), "wm_size": safe_int(wm_size_var) or 28,
                       "wm_color": list(ui["wm_color"]), "wm_file": ui["wm_file"],
                       "last_version": APP_VERSION, "ui_scale": int(scale_var.get().rstrip("%")),
                       "file_name": name_var.get()})

    def load_cached(p):
        if p not in cache:
            if len(cache) > 12:
                cache.pop(next(iter(cache)))
            cache[p] = load_as_srgb(p)
        return cache[p]

    def safe_int(var):
        try:
            return max(0, int(var.get()))
        except Exception:
            return 0

    # ---------- watermark state ----------
    wm_text_var = tk.StringVar(value=saved.get("wm_text", ""))
    wm_family_var = tk.StringVar(value=saved.get("wm_family", ""))
    wm_style_var = tk.StringVar(value=saved.get("wm_style", ""))
    wm_size_var = tk.IntVar(value=int(saved.get("wm_size", 28)))
    ui["wm_color"] = tuple(saved.get("wm_color", (128, 128, 128)))
    ui["wm_file"] = saved.get("wm_file") or None
    fonts = {"map": {}}

    def font_spec():
        if ui["wm_file"]:
            return ui["wm_file"]
        styles = fonts["map"].get(wm_family_var.get(), {})
        if not styles:
            return None
        st = wm_style_var.get()
        if st not in styles:
            st = "Regular" if "Regular" in styles else sorted(styles)[0]
        return styles[st]

    def settings(layout=AUTO):
        lab = size_var.get()
        W, Hh, trim = next(((w, h, t) for (l, w, h, t) in SIZES if l == lab), SIZES[0][1:])
        return {"W": W, "H": Hh, "trim": trim, "margin": safe_int(margin_var),
                "gutter": safe_int(gutter_var),
                "bg": ui["bg"], "sharpen": sharpen_var.get(), "layout": layout,
                "wm_text": wm_text_var.get(), "wm_font": font_spec(),
                "wm_size": max(6, safe_int(wm_size_var)), "wm_color": ui["wm_color"]}

    # ---------- text size (UI scale, 100% to 250% in 10% steps) ----------
    import tkinter.font as tkfont
    UI_SCALES = ["%d%%" % p for p in range(100, 251, 10)]
    fams = set(tkfont.families())
    _deff = tkfont.nametofont("TkDefaultFont")
    base_family = "Segoe UI" if "Segoe UI" in fams else _deff.actual("family")
    D = abs(_deff.cget("size")) or (13 if IS_MAC else 9)   # system text size: 9 pt Windows, 13 pt macOS
    mono_family = next((f for f in ("Consolas", "Menlo", "DejaVu Sans Mono") if f in fams), "Courier")
    F = {
        "bold": tkfont.Font(root, family=base_family, size=D, weight="bold"),
        "title": tkfont.Font(root, family=base_family, size=D + 5, weight="bold"),
        "mono": tkfont.Font(root, family=mono_family, size=D),
        "msg": tkfont.Font(root, family=base_family, size=D + 3),
        "small": tkfont.Font(root, family=base_family, size=max(7, D - 1)),
        "smallb": tkfont.Font(root, family=base_family, size=max(7, D - 1), weight="bold"),
        "badge": tkfont.Font(root, family=base_family, size=D, weight="bold"),
        "dots": tkfont.Font(root, family=base_family, size=D + 5),
    }
    std_fonts = [tkfont.nametofont(n) for n in
                 ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
                  "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont")
                 if n in tkfont.names()]
    base_sizes = [(f, f.cget("size") or 9) for f in std_fonts + list(F.values())]
    root.option_add("*TCombobox*Listbox.font", "TkDefaultFont")     # dropdown lists scale too
    ui["scale"] = 1.0
    ui["on_scale"] = []                                              # widgets that relayout on change

    def apply_scale(pct):
        s = max(1.0, min(2.5, pct / 100.0))
        ui["scale"] = s
        for f, b in base_sizes:
            new = int(round(abs(b) * s))
            f.configure(size=new if b > 0 else -new)                # negative = pixel sizes; keep the sign
        for cb in ui["on_scale"]:
            try:
                cb()
            except Exception:
                pass

    try:
        start_pct = int(str(saved.get("ui_scale", 100)).rstrip("%"))
    except Exception:
        start_pct = 100
    start_pct = min(250, max(100, int(round(start_pct / 10.0)) * 10))
    scale_var = tk.StringVar(value="%d%%" % start_pct)
    apply_scale(start_pct)

    # ---------- window structure ----------
    # Left column scrolls when large text makes it taller than the window;
    # the footer (text size, version, About, updates) always stays visible.
    left_outer = ttk.Frame(root)
    left_outer.pack(side="left", fill="y")
    footer_left = ttk.Frame(left_outer, padding=(10, 4, 10, 10))
    footer_left.pack(side="bottom", fill="x")
    lbg = ttk.Style().lookup("TFrame", "background") or root.cget("bg")
    lcanvas = tk.Canvas(left_outer, highlightthickness=0, bg=lbg, bd=0)
    lsb = ttk.Scrollbar(left_outer, orient="vertical", command=lcanvas.yview)
    lcanvas.configure(yscrollcommand=lsb.set)
    lcanvas.pack(side="left", fill="y", expand=True)
    left = ttk.Frame(lcanvas, padding=10)
    lcanvas.create_window(0, 0, window=left, anchor="nw")

    def left_resized(_e=None):
        lcanvas.configure(scrollregion=(0, 0, left.winfo_reqwidth(), left.winfo_reqheight()),
                          width=left.winfo_reqwidth())
        need = left.winfo_reqheight() > lcanvas.winfo_height() > 1
        shown = lsb.winfo_ismapped()
        if need and not shown:
            lsb.pack(side="right", fill="y", before=lcanvas)
        elif not need and shown:
            lsb.pack_forget()
            lcanvas.yview_moveto(0)
    left.bind("<Configure>", left_resized)
    lcanvas.bind("<Configure>", left_resized)

    def left_wheel(e):
        if not lsb.winfo_ismapped():
            return
        lcanvas.yview_scroll(wheel_steps(e), "units")

    def left_enter(_e):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.bind_all(seq, left_wheel)

    def left_leave(_e):
        try:
            w = root.winfo_containing(*root.winfo_pointerxy())
            if w is not None and str(w).startswith(str(left_outer)):
                return                                   # moved onto a child control: still inside
        except Exception:
            pass
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.unbind_all(seq)
    left_outer.bind("<Enter>", left_enter)
    left_outer.bind("<Leave>", left_leave)
    right = ttk.Frame(root, padding=10)
    right.pack(side="right", fill="both", expand=True)

    nb = ttk.Notebook(left)
    nb.pack(fill="x")
    tab_single = ttk.Frame(nb, padding=8)
    tab_batch = ttk.Frame(nb, padding=8)
    nb.add(tab_single, text="  Single collage  ")
    nb.add(tab_batch, text="  Batch  ")

    # ---------- shared settings: Look and Watermark tabs ----------
    nb2 = ttk.Notebook(left)
    nb2.pack(fill="x", pady=(10, 0))
    opt = ttk.Frame(nb2, padding=8)
    wmf = ttk.Frame(nb2, padding=8)
    nb2.add(opt, text="  Look  ")
    nb2.add(wmf, text="  Watermark  ")

    ttk.Label(opt, text="Size").grid(row=0, column=0, sticky="w")
    ttk.Combobox(opt, textvariable=size_var, values=[s[0] for s in SIZES], state="readonly",
                 width=38).grid(row=0, column=1, sticky="we", pady=2)

    sprow = ttk.Frame(opt)
    sprow.grid(row=1, column=0, columnspan=2, sticky="w", pady=2)

    def spin(parent, label, var, lo, hi):
        ttk.Label(parent, text=label).pack(side="left")
        sp = ttk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=4, command=lambda: settings_changed())
        sp.pack(side="left", padx=(3, 10))
        sp.bind("<KeyRelease>", lambda e: settings_changed())

    spin(sprow, "Gap (px)", gutter_var, 0, 120)
    spin(sprow, "Border (px)", margin_var, 0, 200)

    bgrow = ttk.Frame(opt)
    bgrow.grid(row=2, column=0, columnspan=2, sticky="w", pady=2)
    ttk.Label(bgrow, text="Background").pack(side="left")
    swatch = tk.Label(bgrow, text="     ", bg="#%02x%02x%02x" % ui["bg"], relief="solid", bd=1)
    swatch.pack(side="left", padx=(6, 4))

    def pick_bg():
        c = colorchooser.askcolor(color="#%02x%02x%02x" % ui["bg"], title="Background colour")
        if c and c[0]:
            ui["bg"] = tuple(int(v) for v in c[0])
            swatch.configure(bg=c[1])
            settings_changed()
    ttk.Button(bgrow, text="Choose...", command=pick_bg).pack(side="left")
    ttk.Checkbutton(bgrow, text="Sharpen", variable=sharpen_var,
                    command=lambda: settings_changed()).pack(side="left", padx=(12, 0))

    # ---------- watermark controls ----------
    r0 = ttk.Frame(wmf)
    r0.pack(fill="x", pady=2)
    ttk.Label(r0, text="Author", width=7).pack(side="left")
    ttk.Entry(r0, textvariable=wm_text_var, width=34).pack(side="left", fill="x", expand=True)

    r1 = ttk.Frame(wmf)
    r1.pack(fill="x", pady=2)
    ttk.Label(r1, text="Font", width=7).pack(side="left")
    cb_family = ttk.Combobox(r1, textvariable=wm_family_var, state="readonly", width=22)
    cb_family.pack(side="left")
    cb_style = ttk.Combobox(r1, textvariable=wm_style_var, state="readonly", width=11)
    cb_style.pack(side="left", padx=(4, 0))

    r2 = ttk.Frame(wmf)
    r2.pack(fill="x", pady=2)
    ttk.Label(r2, text="Size", width=7).pack(side="left")
    sp_wm = ttk.Frame(r2)
    sp_wm.pack(side="left")
    spin(sp_wm, "", wm_size_var, 6, 200)
    ttk.Label(r2, text="Colour").pack(side="left")
    wm_swatch = tk.Label(r2, text="     ", bg="#%02x%02x%02x" % ui["wm_color"], relief="solid", bd=1)
    wm_swatch.pack(side="left", padx=(6, 4))

    def pick_wm_colour():
        c = colorchooser.askcolor(color="#%02x%02x%02x" % ui["wm_color"], title="Watermark colour")
        if c and c[0]:
            ui["wm_color"] = tuple(int(v) for v in c[0])
            wm_swatch.configure(bg=c[1])
            settings_changed()
    ttk.Button(r2, text="Choose...", command=pick_wm_colour).pack(side="left")

    r3 = ttk.Frame(wmf)
    r3.pack(fill="x", pady=(4, 0))
    font_note = tk.StringVar(value="Loading fonts...")
    ttk.Label(r3, textvariable=font_note, foreground="#666").pack(side="left")

    def pick_font_file():
        p = filedialog.askopenfilename(title="Choose a font file",
                                       filetypes=[("Fonts", "*.ttf *.otf *.ttc *.otc"), ("All files", "*.*")])
        if not p:
            return
        try:
            from PIL import ImageFont
            fam, style = ImageFont.truetype(p, 12).getname()
        except Exception as e:
            messagebox.showerror(APP_NAME, "Cannot use this font:\n%s" % e)
            return
        ui["wm_file"] = p + "|0"
        font_note.set("Using file: %s %s" % (fam, style))
        settings_changed()

    def use_installed(*_):
        ui["wm_file"] = None
        styles = sorted(fonts["map"].get(wm_family_var.get(), {}))
        cb_style["values"] = styles
        if styles and wm_style_var.get() not in styles:
            wm_style_var.set("Regular" if "Regular" in styles else styles[0])
        font_note.set("%d font families installed" % len(fonts["map"]))
        settings_changed()

    ttk.Button(r3, text="Font file...", command=pick_font_file).pack(side="right")
    wm_note = ttk.Label(wmf, text="Sits in its own strip below the photos; never covers them. Leave empty for none.",
                        foreground="#666", wraplength=int(330 * ui["scale"]), justify="left")
    wm_note.pack(anchor="w", pady=(4, 0))
    ui["on_scale"].append(lambda: wm_note.configure(wraplength=int(330 * ui["scale"])))

    cb_family.bind("<<ComboboxSelected>>", use_installed)
    cb_style.bind("<<ComboboxSelected>>", lambda e: (ui.__setitem__("wm_file", None), settings_changed()))
    wm_text_var.trace_add("write", lambda *a: settings_changed())

    def fonts_signature():
        sig = []
        for d in FONT_DIRS:
            if d and os.path.isdir(d):
                try:
                    sig.append([d, len(os.listdir(d)), int(os.path.getmtime(d))])
                except Exception:
                    pass
        return sig

    def fonts_ready(mapping):
        fonts["map"] = mapping
        fams = sorted(mapping, key=str.lower)
        cb_family["values"] = fams
        if wm_family_var.get() not in mapping:
            wm_family_var.set(next((f for f in PREFERRED_FONTS if f in mapping), fams[0] if fams else ""))
        styles = sorted(mapping.get(wm_family_var.get(), {}))
        cb_style["values"] = styles
        if styles and wm_style_var.get() not in styles:
            wm_style_var.set("Regular" if "Regular" in styles else styles[0])
        if ui["wm_file"]:
            font_note.set("Using a font file")
        else:
            font_note.set("%d font families installed" % len(mapping) if mapping else "Built in font")
        settings_changed()

    def load_fonts():
        cache_path = os.path.join(data_dir(), "fonts.json")
        sig = fonts_signature()
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                c = json.load(f)
            if c.get("sig") == sig and c.get("map"):
                fonts_ready(c["map"])
                return
        except Exception:
            pass
        result = {}

        def work():
            result["map"] = scan_fonts()
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"sig": sig, "map": result["map"]}, f)
            except Exception:
                pass

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def wait():
            if t.is_alive():
                root.after(150, wait)
            else:
                fonts_ready(result.get("map", {}))
        root.after(150, wait)

    outf = ttk.LabelFrame(left, text="Save to", padding=8)
    outf.pack(fill="x", pady=(10, 0))
    of_row = ttk.Frame(outf)
    of_row.pack(fill="x")
    out_lbl = ttk.Label(of_row, textvariable=out_dir, wraplength=int(220 * ui["scale"]))
    out_lbl.pack(side="left", anchor="w")
    ui["on_scale"].append(lambda: out_lbl.configure(wraplength=int(220 * ui["scale"])))

    def pick_out():
        d = filedialog.askdirectory(initialdir=out_dir.get() if os.path.isdir(out_dir.get()) else None)
        if d:
            out_dir.set(os.path.normpath(d))
            persist()
    ttk.Button(of_row, text="Change...", command=pick_out).pack(side="right")

    # ---------- output file name: [name]_###.jpg ----------
    fn_row = ttk.Frame(outf)
    fn_row.pack(fill="x", pady=(8, 0))
    ttk.Label(fn_row, text="File name").pack(side="left")
    name_var = tk.StringVar(value=clean_basename_live(saved.get("file_name", DEFAULT_BASENAME))[0])
    name_entry = ttk.Entry(fn_row, textvariable=name_var, width=24)
    name_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
    name_hint = tk.StringVar()
    hint_lbl = ttk.Label(outf, textvariable=name_hint, foreground="#666", wraplength=int(300 * ui["scale"]),
                         justify="left")
    hint_lbl.pack(anchor="w", pady=(4, 0))
    ui["on_scale"].append(lambda: hint_lbl.configure(wraplength=int(300 * ui["scale"])))
    name_guard = {"busy": False}

    def name_changed(*_):
        if name_guard["busy"]:
            return
        raw = name_var.get()
        cleaned, removed = clean_basename_live(raw)
        if cleaned != raw:
            pos = name_entry.index("insert")
            name_guard["busy"] = True
            name_var.set(cleaned)                      # spaces -> "_", forbidden characters dropped
            name_guard["busy"] = False
            name_entry.icursor(max(0, min(len(cleaned), pos - (len(raw) - len(cleaned)))))
        base = final_basename(cleaned)
        hint = "Saved as %s, %s, ..." % (numbered_name(base, 1), numbered_name(base, 2))
        if removed:
            hint = "Removed characters Windows does not allow.  " + hint
        name_hint.set(hint)

    name_var.trace_add("write", name_changed)
    name_entry.bind("<FocusOut>", lambda e: persist())
    name_changed()

    # ---------- preview area (Batch adds the photo board above it) ----------
    paned = ttk.PanedWindow(right, orient="vertical")
    paned.pack(fill="both", expand=True)
    board_frame = ttk.Frame(paned)
    prev_frame = ttk.Frame(paned)
    paned.add(prev_frame, weight=3)
    canvas = tk.Canvas(prev_frame, bg="#2b2b2b", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    bottom = ttk.Frame(right)
    bottom.pack(fill="x", pady=(6, 0))
    ttk.Label(bottom, textvariable=status_var, foreground="#444").pack(side="left")
    progress = ttk.Progressbar(right, mode="determinate")
    progress.pack(fill="x", pady=(6, 0))

    def show_board(on):
        present = str(board_frame) in [str(p) for p in paned.panes()]
        if on and not present:
            paned.insert(0, board_frame, weight=2)
        elif not on and present:
            paned.forget(board_frame)

    def show_message(text):
        canvas.delete("all")
        cw, ch = max(canvas.winfo_width(), 50), max(canvas.winfo_height(), 50)
        canvas.create_text(cw // 2, ch // 2, fill="#bbb", font=F["msg"], text=text, justify="center")

    def show_image(img, meta):
        canvas.delete("all")
        cw, ch = max(canvas.winfo_width(), 50), max(canvas.winfo_height(), 50)
        k = min((cw - 20) / float(img.width), (ch - 20) / float(img.height))
        pv = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), RESAMPLE)
        ui["preview"] = ImageTk.PhotoImage(pv)
        canvas.create_image((cw - pv.width) // 2, (ch - pv.height) // 2, anchor="nw", image=ui["preview"])
        msg = "%d x %d px  |  %s  |  photos fill %d%% of the image" % (
            img.width, img.height, meta["layout"], round(meta["coverage"] * 100))
        if meta["warnings"]:
            msg += "  |  " + meta["warnings"][0]
        status_var.set(msg)

    # =====================================================================
    # Single tab
    # =====================================================================
    ttk.Label(tab_single, text="Photos (2 or 3); every photo shown in full",
              font=F["bold"]).pack(anchor="w")
    lb_single = tk.Listbox(tab_single, height=4, width=42, activestyle="none")
    lb_single.pack(fill="x", pady=4)
    row = ttk.Frame(tab_single)
    row.pack(fill="x")
    lay_row = ttk.Frame(tab_single)
    lay_row.pack(fill="x", pady=(8, 0))
    ttk.Label(lay_row, text="Arrangement").pack(side="left")
    cb_layout = ttk.Combobox(lay_row, textvariable=layout_var, state="readonly", width=30)
    cb_layout.pack(side="left", padx=(6, 0))
    btn_export = ttk.Button(tab_single, text="Export this collage", command=lambda: export_single())
    btn_export.pack(fill="x", pady=(10, 0), ipady=4)

    def sync_single():
        lb_single.delete(0, "end")
        for i, p in enumerate(single["paths"]):
            lb_single.insert("end", "%d.  %s" % (i + 1, os.path.basename(p)))
        n = len(single["paths"])
        opts = [AUTO] + [a[0] for a in ARRANGEMENTS.get(n, [])]
        cb_layout["values"] = opts
        if layout_var.get() not in opts:
            layout_var.set(AUTO)
        btn_export.state(["!disabled"] if n in (2, 3) else ["disabled"])
        refresh()

    def add_single(paths):
        bad = []
        for p in paths:
            if len(single["paths"]) >= 3:
                bad.append(os.path.basename(p) + " (maximum is 3; use the Batch tab for more)")
                continue
            if os.path.splitext(p)[1].lower() not in IMAGE_EXTS:
                bad.append(os.path.basename(p) + " (unsupported type)")
                continue
            try:
                single["images"].append(load_as_srgb(p))
                single["paths"].append(p)
            except Exception as e:
                bad.append("%s (%s)" % (os.path.basename(p), e))
        if bad:
            messagebox.showwarning(APP_NAME, "Skipped:\n" + "\n".join(bad))
        sync_single()

    def single_move(d):
        sel = lb_single.curselection()
        if not sel:
            return
        i, j = sel[0], sel[0] + d
        if 0 <= j < len(single["paths"]):
            for k in ("paths", "images"):
                single[k][i], single[k][j] = single[k][j], single[k][i]
            sync_single()
            lb_single.selection_set(j)

    def single_remove():
        sel = lb_single.curselection()
        if sel:
            for k in ("paths", "images"):
                del single[k][sel[0]]
            sync_single()

    def single_clear():
        single["paths"].clear()
        single["images"].clear()
        sync_single()

    def single_add_dialog():
        pat = " ".join("*" + e for e in IMAGE_EXTS)
        ps = filedialog.askopenfilenames(title="Choose 2 or 3 photos",
                                         filetypes=[("Images", pat), ("All files", "*.*")])
        if ps:
            add_single(list(ps))

    for t, c in (("Add...", single_add_dialog), ("Up", lambda: single_move(-1)),
                 ("Down", lambda: single_move(1)), ("Remove", single_remove), ("Clear", single_clear)):
        ttk.Button(row, text=t, command=c, width=7).pack(side="left", padx=(0, 3))

    def export_single():
        if len(single["images"]) not in (2, 3):
            return
        try:
            os.makedirs(out_dir.get(), exist_ok=True)
            img, meta = compose(single["images"], settings(layout_var.get()))
            base = final_basename(name_var.get())
            name = numbered_name(base, next_number(out_dir.get(), base))   # next free number
            path = os.path.join(out_dir.get(), name)
            q, nb_ = save_for_threads(img, path)
        except Exception as e:
            messagebox.showerror(APP_NAME, "Could not export:\n%s" % e)
            return
        status_var.set("Saved %s  (%d x %d, %.2f MB)" % (name, img.width, img.height, nb_ / 1048576.0))
        persist()
        if messagebox.askyesno(APP_NAME, "Saved:\n%s\n\nOpen the folder?" % path):
            open_folder(out_dir.get())

    # =====================================================================
    # Batch tab
    # =====================================================================
    ttk.Label(tab_batch, text="Photos to turn into collages", font=F["bold"]).pack(anchor="w")
    brow = ttk.Frame(tab_batch)
    brow.pack(fill="x", pady=4)
    count_var = tk.StringVar(value="No photos yet")
    ttk.Label(tab_batch, textvariable=count_var).pack(anchor="w")
    ttk.Checkbutton(tab_batch, text="Include subfolders", variable=sub_var).pack(anchor="w")

    bopt = ttk.Frame(tab_batch)
    bopt.pack(fill="x", pady=(6, 0))
    ttk.Label(bopt, text="Order").grid(row=0, column=0, sticky="w")
    ttk.Combobox(bopt, textvariable=order_var, values=ORDER_MODES, state="readonly",
                 width=36).grid(row=0, column=1, sticky="we", pady=2)
    ttk.Label(bopt, text="Group").grid(row=1, column=0, sticky="w")
    ttk.Combobox(bopt, textvariable=group_var, values=GROUP_MODES, state="readonly",
                 width=36).grid(row=1, column=1, sticky="we", pady=2)

    ttk.Label(tab_batch, text="Planned collages (click to preview)").pack(anchor="w", pady=(8, 0))
    pf = ttk.Frame(tab_batch)
    pf.pack(fill="x")
    lb_plan = tk.Listbox(pf, height=5, width=42, activestyle="none", exportselection=False)
    sb = ttk.Scrollbar(pf, orient="vertical", command=lb_plan.yview)
    lb_plan.configure(yscrollcommand=sb.set)
    lb_plan.pack(side="left", fill="x", expand=True)
    sb.pack(side="right", fill="y")

    bbtns = ttk.Frame(tab_batch)
    bbtns.pack(fill="x", pady=(10, 0))
    btn_run = ttk.Button(bbtns, text="Create all collages", command=lambda: start_batch())
    btn_run.pack(side="left", fill="x", expand=True, ipady=4)
    btn_cancel = ttk.Button(bbtns, text="Cancel", command=lambda: cancel_flag.set())
    btn_cancel.pack(side="left", padx=(6, 0), ipady=4)
    btn_cancel.state(["disabled"])
    cancel_flag = threading.Event()

    def scan_folder(folder):
        found = []
        if sub_var.get():
            for dp, _, fs in os.walk(folder):
                found += [os.path.join(dp, f) for f in fs]
        else:
            found = [os.path.join(folder, f) for f in os.listdir(folder)]
        return [p for p in found if os.path.isfile(p) and os.path.splitext(p)[1].lower() in IMAGE_EXTS]

    def add_batch(paths):
        new, bad = [], []
        for p in paths:
            if os.path.isdir(p):
                new += scan_folder(p)
            elif os.path.splitext(p)[1].lower() in IMAGE_EXTS:
                new.append(p)
        for p in new:
            p = os.path.normpath(p)
            if p in batch["meta"]:
                continue
            try:
                batch["meta"][p] = read_meta(p)
                batch["paths"].append(p)
            except Exception as e:
                bad.append("%s (%s)" % (os.path.basename(p), e))
        if bad:
            messagebox.showwarning(APP_NAME, "Could not read %d file(s):\n%s" % (len(bad), "\n".join(bad[:15])))
        replan()

    def batch_add_files():
        pat = " ".join("*" + e for e in IMAGE_EXTS)
        ps = filedialog.askopenfilenames(title="Choose photos", filetypes=[("Images", pat), ("All files", "*.*")])
        if ps:
            add_batch(list(ps))

    def batch_add_folder():
        d = filedialog.askdirectory(title="Choose a folder of photos")
        if d:
            add_batch([d])

    def batch_clear():
        batch["paths"].clear()
        batch["meta"].clear()
        batch["excluded"].clear()
        replan()

    for t, c in (("Add folder...", batch_add_folder), ("Add files...", batch_add_files), ("Clear", batch_clear)):
        ttk.Button(brow, text=t, command=c).pack(side="left", padx=(0, 3))

    def replan(*_):
        mode = ORDER_MODES.index(order_var.get())
        meta = batch["meta"]
        if mode == 0:
            batch["paths"].sort(key=lambda p: (meta[p][1], os.path.basename(p).lower()))
        elif mode == 1:
            batch["paths"].sort(key=lambda p: os.path.basename(p).lower())
        elif mode == 2:
            batch["paths"].sort(key=lambda p: (meta[p][0], os.path.basename(p).lower()))
        # mode 3 (my order): keep the arrangement exactly as dragged
        paths = [p for p in batch["paths"] if p not in batch["excluded"]]
        batch["order"] = paths
        s = settings()
        groups = plan_groups([meta[p][0] for p in paths], s, GROUP_MODES.index(group_var.get()))
        batch["groups"] = groups
        batch["group_of"] = {paths[i]: gi for gi, grp in enumerate(groups) for i in grp}
        lb_plan.delete(0, "end")
        for gi, grp in enumerate(groups):
            names = " + ".join(os.path.basename(paths[i]) for i in grp)
            lb_plan.insert("end", "%03d  (%d)  %s" % (gi + 1, len(grp), names))
        total, n = len(batch["paths"]), len(paths)
        excl = "" if total == n else "  (%d excluded)" % (total - n)
        if total == 0:
            count_var.set("No photos yet")
        elif n < 2:
            count_var.set("%d photo selected: include at least 2%s" % (n, excl))
        else:
            c2 = sum(1 for g_ in groups if len(g_) == 2)
            count_var.set("%d photos -> %d collages (%d of 3, %d of 2)%s"
                          % (n, len(groups), len(groups) - c2, c2, excl))
        if not batch["running"]:
            btn_run.state(["!disabled"] if groups else ["disabled"])
        batch["sel"] = min(batch["sel"], max(0, len(groups) - 1))
        if groups:
            lb_plan.selection_clear(0, "end")
            lb_plan.selection_set(batch["sel"])
            lb_plan.see(batch["sel"])
        request_thumbs()
        board_redraw()
        if nb.index("current") == 1:
            refresh()

    def select_group(gi):
        if not batch["groups"]:
            return
        batch["sel"] = max(0, min(gi, len(batch["groups"]) - 1))
        lb_plan.selection_clear(0, "end")
        lb_plan.selection_set(batch["sel"])
        lb_plan.see(batch["sel"])
        board_redraw()
        refresh()

    order_var.trace_add("write", replan)
    group_var.trace_add("write", replan)
    lb_plan.bind("<<ListboxSelect>>",
                 lambda e: select_group(lb_plan.curselection()[0]) if lb_plan.curselection() else None)

    # =====================================================================
    # Photo board: thumbnails of every imported photo.
    #   click = include / exclude, drag = reorder (switches Order to "My order"),
    #   right click = more. Coloured bands show which photos share a collage.
    # =====================================================================
    PAD, TB = 10, THUMB_SIZE + 8
    M = {"cw": 132, "ch": 166}          # cell size, recomputed from the text size
    BAND_COLOURS = ["#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#db2777"]
    thumbs = {"pil": {}, "tk": {}, "dim": {}, "pending": set()}
    tq_in, tq_out = queue.Queue(), queue.Queue()
    drag = {"src": None, "x": 0, "y": 0, "moving": False, "ghost": None}

    bar = ttk.Frame(board_frame)
    bar.pack(fill="x", pady=(0, 4))
    ttk.Label(bar, text="Photos: click to include or exclude, drag to reorder. Neighbours share a collage.",
              foreground="#444").pack(side="left")

    def set_all(include):
        if include:
            batch["excluded"].clear()
        else:
            batch["excluded"] = set(batch["paths"])
        replan()
    ttk.Button(bar, text="Exclude all", command=lambda: set_all(False)).pack(side="right")
    ttk.Button(bar, text="Include all", command=lambda: set_all(True)).pack(side="right", padx=(0, 4))

    bwrap = ttk.Frame(board_frame)
    bwrap.pack(fill="both", expand=True)
    board = tk.Canvas(bwrap, bg="#1f1f1f", highlightthickness=0)
    bsb = ttk.Scrollbar(bwrap, orient="vertical", command=board.yview)
    board.configure(yscrollcommand=bsb.set)
    board.pack(side="left", fill="both", expand=True)
    bsb.pack(side="right", fill="y")

    def thumb_worker():
        while True:
            p = tq_in.get()
            try:
                tq_out.put((p, make_thumb(p)))
            except Exception:
                tq_out.put((p, None))

    threading.Thread(target=thumb_worker, daemon=True).start()

    def request_thumbs():
        for p in batch["paths"]:
            if p not in thumbs["pil"] and p not in thumbs["pending"]:
                thumbs["pending"].add(p)
                tq_in.put(p)
        if thumbs["pending"]:
            root.after(150, collect_thumbs)

    def collect_thumbs():
        got = False
        try:
            while True:
                p, im = tq_out.get_nowait()
                thumbs["pending"].discard(p)
                if im is None:
                    im = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), (90, 30, 30))
                thumbs["pil"][p] = im
                thumbs["tk"][p] = ImageTk.PhotoImage(im)
                thumbs["dim"][p] = ImageTk.PhotoImage(
                    Image.blend(im, Image.new("RGB", im.size, (31, 31, 31)), 0.72))
                got = True
        except queue.Empty:
            pass
        if got:
            board_redraw()
        if thumbs["pending"]:
            root.after(150, collect_thumbs)

    def cols():
        return max(1, (max(board.winfo_width(), M["cw"]) - PAD) // M["cw"])

    def cell_xy(i):
        c = cols()
        return PAD + (i % c) * M["cw"], PAD + (i // c) * M["ch"]

    def index_at(cx, cy):
        c = cols()
        col, row = int((cx - PAD) // M["cw"]), int((cy - PAD) // M["ch"])
        if col < 0 or col >= c or row < 0:
            return None
        i = row * c + col
        return i if i < len(batch["paths"]) else None

    def drop_index(cx, cy):
        c, n = cols(), len(batch["paths"])
        col = min(max(int((cx - PAD) // M["cw"]), 0), c - 1)
        row = max(int((cy - PAD) // M["ch"]), 0)
        frac = ((cx - PAD) - col * M["cw"]) / float(M["cw"])
        return min(max(row * c + col + (1 if frac > 0.5 else 0), 0), n)

    def short(name, maxw):
        f = F["small"]
        if f.measure(name) <= maxw:
            return name
        while len(name) > 2 and f.measure(name + "…") > maxw:
            name = name[:-1]
        return name + "…"

    def board_metrics():
        lh = F["small"].metrics("linespace")
        lhb = F["smallb"].metrics("linespace")
        M["band"] = lhb + 6
        M["line"] = lh
        M["badge"] = max(9, F["bold"].metrics("linespace") // 2 + 1)
        M["cw"] = max(132, F["smallb"].measure("Collage 888") + 28, F["small"].measure("needs a partner") + 24)
        M["ch"] = 4 + TB + 4 + lh + 6 + M["band"] + 4 + 8

    def board_redraw():
        board.delete("all")
        paths = batch["paths"]
        if not paths:
            board.create_text(max(board.winfo_width(), 200) // 2, 60, fill="#999", font=F["msg"],
                              text="Thumbnails of your photos will appear here")
            board.configure(scrollregion=(0, 0, 1, 1))
            return
        sel_paths = set()
        if batch["groups"] and batch["sel"] < len(batch["groups"]):
            sel_paths = {batch["order"][i] for i in batch["groups"][batch["sel"]]}
        for i, p in enumerate(paths):
            x, y = cell_xy(i)
            excluded = p in batch["excluded"]
            gi = batch["group_of"].get(p)
            w, h = M["cw"] - 8, M["ch"] - 8
            outline = "#ffffff" if p in sel_paths else "#3a3a3a"
            board.create_rectangle(x, y, x + w, y + h, fill="#2b2b2b", outline=outline,
                                   width=3 if p in sel_paths else 1)
            cx, cy = x + w // 2, y + 4 + TB // 2
            img = (thumbs["dim"] if excluded else thumbs["tk"]).get(p)
            if img:
                board.create_image(cx, cy, image=img)
            else:
                board.create_text(cx, cy, text="…", fill="#888", font=F["dots"])
            board.create_text(x + 6, y + 5, text=str(i + 1), anchor="nw", fill="#ddd", font=F["smallb"])
            r = M["badge"]                                   # include / exclude badge
            bx, by = x + w - r - 5, y + r + 5
            board.create_oval(bx - r, by - r, bx + r, by + r,
                              fill="#b91c1c" if excluded else "#16a34a", outline="")
            board.create_text(bx, by, text="✕" if excluded else "✓", fill="white", font=F["bold"])
            ny = y + 4 + TB + 4 + M["line"] // 2
            board.create_text(cx, ny, text=short(os.path.basename(p), w - 10), fill="#bbb", font=F["small"])
            by0, by1 = y + h - 4 - M["band"], y + h - 4
            bmid = (by0 + by1) // 2
            if excluded:
                board.create_rectangle(x + 4, by0, x + w - 4, by1, fill="#444", outline="")
                board.create_text(cx, bmid, text="Excluded", fill="#ccc", font=F["small"])
            elif gi is not None:
                board.create_rectangle(x + 4, by0, x + w - 4, by1,
                                       fill=BAND_COLOURS[gi % len(BAND_COLOURS)], outline="")
                board.create_text(cx, bmid, text="Collage %d" % (gi + 1), fill="white", font=F["smallb"])
            else:
                board.create_text(cx, bmid, text="needs a partner", fill="#e5a50a", font=F["small"])
        rows = (len(paths) + cols() - 1) // cols()
        board.configure(scrollregion=(0, 0, cols() * M["cw"] + PAD, rows * M["ch"] + PAD))

    def focus_on_path(p):
        """Preview the collage containing p, or the nearest included neighbour."""
        if p in batch["group_of"]:
            select_group(batch["group_of"][p])
            return
        paths = batch["paths"]
        i = paths.index(p) if p in paths else 0
        for j in list(range(i, len(paths))) + list(range(i - 1, -1, -1)):
            if paths[j] in batch["group_of"]:
                select_group(batch["group_of"][paths[j]])
                return

    def toggle(p):
        if p in batch["excluded"]:
            batch["excluded"].discard(p)
        else:
            batch["excluded"].add(p)
        replan()
        focus_on_path(p)

    def move_item(src, dst):
        paths = batch["paths"]
        p = paths.pop(src)
        if dst > src:
            dst -= 1
        paths.insert(dst, p)
        if ORDER_MODES.index(order_var.get()) != CUSTOM_ORDER:
            order_var.set(ORDER_MODES[CUSTOM_ORDER])     # trace runs replan, keeping this order
        else:
            replan()
        focus_on_path(p)

    def on_press(e):
        cx, cy = board.canvasx(e.x), board.canvasy(e.y)
        drag.update(src=index_at(cx, cy), x=cx, y=cy, moving=False)

    def on_motion(e):
        if drag["src"] is None:
            return
        cx, cy = board.canvasx(e.x), board.canvasy(e.y)
        if not drag["moving"] and abs(cx - drag["x"]) + abs(cy - drag["y"]) < 8:
            return
        drag["moving"] = True
        if e.y < 24:
            board.yview_scroll(-1, "units")
        elif e.y > board.winfo_height() - 24:
            board.yview_scroll(1, "units")
        board.delete("dragmark")
        di = drop_index(cx, cy)
        n = len(batch["paths"])
        if di < n:
            x, y = cell_xy(di)
            mx = x - 4
        else:
            x, y = cell_xy(n - 1)
            mx = x + M["cw"] - 4
        board.create_rectangle(mx - 2, y, mx + 2, y + M["ch"] - 8, fill="#38bdf8", outline="", tags="dragmark")
        p = batch["paths"][drag["src"]]
        img = thumbs["tk"].get(p)
        if img:
            board.create_image(cx, cy, image=img, tags="dragmark")
            iw, ih = thumbs["pil"][p].size
            board.create_rectangle(cx - iw // 2, cy - ih // 2, cx + iw // 2, cy + ih // 2,
                                   outline="#38bdf8", width=2, tags="dragmark")

    def on_release(e):
        src = drag["src"]
        drag["src"] = None
        board.delete("dragmark")
        if src is None:
            return
        cx, cy = board.canvasx(e.x), board.canvasy(e.y)
        if drag["moving"]:
            dst = drop_index(cx, cy)
            if dst not in (src, src + 1):
                move_item(src, dst)
            else:
                board_redraw()
        else:
            toggle(batch["paths"][src])

    menu = tk.Menu(root, tearoff=0)

    def on_right(e):
        i = index_at(board.canvasx(e.x), board.canvasy(e.y))
        if i is None:
            return
        p = batch["paths"][i]
        menu.delete(0, "end")
        menu.add_command(label="Include" if p in batch["excluded"] else "Exclude", command=lambda: toggle(p))
        menu.add_command(label="Preview its collage", command=lambda: focus_on_path(p))
        menu.add_separator()
        menu.add_command(label="Move to start", command=lambda: move_item(i, 0))
        menu.add_command(label="Move to end", command=lambda: move_item(i, len(batch["paths"])))
        menu.add_separator()
        menu.add_command(label="Open photo", command=lambda: open_folder(p))
        menu.add_command(label="Remove from list", command=lambda: remove_photo(p))
        menu.tk_popup(e.x_root, e.y_root)

    def remove_photo(p):
        if p in batch["paths"]:
            batch["paths"].remove(p)
        batch["meta"].pop(p, None)
        batch["excluded"].discard(p)
        replan()

    def on_wheel(e):
        board.yview_scroll(wheel_steps(e), "units")

    board.bind("<ButtonPress-1>", on_press)
    board.bind("<B1-Motion>", on_motion)
    board.bind("<ButtonRelease-1>", on_release)
    if IS_MAC:
        board.bind("<Button-2>", on_right)
        board.bind("<Control-Button-1>", on_right)
    else:
        board.bind("<Button-3>", on_right)
    for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        board.bind(seq, on_wheel)
    board_pending = {"id": None}

    def board_resized(_e=None):
        if board_pending["id"]:
            root.after_cancel(board_pending["id"])
        board_pending["id"] = root.after(80, board_redraw)
    board.bind("<Configure>", board_resized)
    board_metrics()
    ui["on_scale"].append(lambda: (board_metrics(), board_redraw()))

    def open_folder(path):
        open_path(path)

    msgq = queue.Queue()

    def start_batch():
        if not batch["groups"] or batch["running"]:
            return
        s = settings()
        s["basename"] = final_basename(name_var.get())
        persist()
        order = list(batch["order"])
        groups = [list(g_) for g_ in batch["groups"]]
        folder = os.path.join(out_dir.get(), "Batch_%s" % time.strftime("%Y%m%d_%H%M%S"))
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            messagebox.showerror(APP_NAME, "Cannot create output folder:\n%s" % e)
            return
        batch["running"] = True
        cancel_flag.clear()
        btn_run.state(["disabled"])
        btn_cancel.state(["!disabled"])
        progress.configure(maximum=len(groups), value=0)
        threading.Thread(target=batch_worker, args=(s, order, groups, folder), daemon=True).start()
        root.after(100, poll)

    def batch_worker(s, order, groups, folder):
        rows, errors, done = [], [], 0
        for gi, grp in enumerate(groups):
            if cancel_flag.is_set():
                break
            srcs = [order[i] for i in grp]
            name = numbered_name(s.get("basename", DEFAULT_BASENAME), gi + 1)
            try:
                imgs = [load_as_srgb(p) for p in srcs]
                img, meta = compose(imgs, s)
                q, nbytes = save_for_threads(img, os.path.join(folder, name))
                rows.append([name, len(srcs), meta["layout"], "%d%%" % round(meta["coverage"] * 100),
                             "%dx%d" % img.size, "%.2f" % (nbytes / 1048576.0), APP_VERSION] + srcs)
                done += 1
            except Exception as e:
                errors.append("%s: %s" % (name, e))
            msgq.put(("progress", gi + 1, len(groups), name))
        try:
            with open(os.path.join(folder, "collage_index.csv"), "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["collage", "photos", "arrangement", "photo coverage", "pixels", "MB",
                            "app version", "photo 1", "photo 2", "photo 3"])
                w.writerows(rows)
        except Exception as e:
            errors.append("index: %s" % e)
        msgq.put(("done", done, len(groups), folder, errors, cancel_flag.is_set()))

    def poll():
        try:
            while True:
                m = msgq.get_nowait()
                if m[0] == "progress":
                    progress.configure(value=m[1])
                    status_var.set("Creating %s  (%d of %d)" % (m[3], m[1], m[2]))
                else:
                    _, done, total, folder, errors, cancelled = m
                    batch["running"] = False
                    btn_cancel.state(["disabled"])
                    btn_run.state(["!disabled"] if batch["groups"] else ["disabled"])
                    msg = "%s %d of %d collages in:\n%s" % (
                        "Cancelled after" if cancelled else "Created", done, total, folder)
                    if errors:
                        msg += "\n\nProblems:\n" + "\n".join(errors[:10])
                    status_var.set(msg.split("\n")[0])
                    if messagebox.askyesno(APP_NAME, msg + "\n\nOpen the folder?"):
                        open_folder(folder)
                    return
        except queue.Empty:
            pass
        root.after(100, poll)

    # =====================================================================
    # Preview refresh
    # =====================================================================
    pending = {"id": None}

    def refresh(*_):
        if pending["id"]:
            root.after_cancel(pending["id"])
        pending["id"] = root.after(120, draw)

    def settings_changed(*_):
        if nb.index("current") == 1:
            replan()
        else:
            refresh()

    def draw():
        pending["id"] = None
        try:
            if nb.index("current") == 0:
                if len(single["images"]) not in (2, 3):
                    show_message("Add 2 or 3 photos\n(drag them here, or click Add)")
                    status_var.set("")
                    return
                img, meta = compose(single["images"], settings(layout_var.get()))
                show_image(img, meta)
            else:
                if batch["running"]:
                    return
                if not batch["groups"]:
                    show_message("Add a folder of photos\n(drag it here, or click Add folder)"
                                 if not batch["paths"] else "Include at least 2 photos")
                    status_var.set("")
                    return
                gi = min(batch["sel"], len(batch["groups"]) - 1)
                srcs = [batch["order"][i] for i in batch["groups"][gi]]
                img, meta = compose([load_cached(p) for p in srcs], settings())
                show_image(img, meta)
                status_var.set("Collage %d of %d  |  " % (gi + 1, len(batch["groups"])) + status_var.get())
        except Exception as e:
            status_var.set("Preview error: %s" % e)

    def tab_changed(_e=None):
        if nb.index("current") == 1:
            show_board(True)
            replan()
        else:
            show_board(False)
            refresh()

    nb.bind("<<NotebookTabChanged>>", tab_changed)
    canvas.bind("<Configure>", refresh)
    size_var.trace_add("write", settings_changed)
    layout_var.trace_add("write", refresh)

    if DND_OK:
        def on_drop(e):
            items = list(root.tk.splitlist(e.data))
            if len(items) == 1 and os.path.splitext(items[0])[1].lower() in (".py", ".zip"):
                install_update_from(items[0])
                return
            if nb.index("current") == 0 and not any(os.path.isdir(p) for p in items) and len(items) <= 3:
                add_single(items)
            else:
                nb.select(1)
                add_batch(items)
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", on_drop)

    # ---------- updating ----------
    def install_update_from(path):
        if batch["running"]:
            messagebox.showinfo(APP_NAME, "Please wait for the batch to finish first.")
            return
        try:
            text, newv = inspect_update(path)
        except Exception as e:
            messagebox.showerror(APP_NAME, "Cannot use this update:\n%s" % e)
            return
        if vtuple(newv) <= vtuple(APP_VERSION):
            if not messagebox.askyesno(APP_NAME, "This file is version %s; you have %s.\n\nInstall it anyway?"
                                       % (newv, APP_VERSION)):
                return
        try:
            apply_update(text)
        except Exception as e:
            messagebox.showerror(APP_NAME, "Could not install the update:\n%s" % e)
            return
        persist()
        if messagebox.askyesno(APP_NAME, "Updated to version %s. Your settings are kept.\n\nRestart now?" % newv):
            restart_app()
            root.destroy()

    def update_dialog():
        p = filedialog.askopenfilename(title="Choose the update file (threads_collage.py or .zip)",
                                       filetypes=[("Threads Collage update", "*.py *.zip"), ("All files", "*.*")])
        if p:
            install_update_from(p)

    # ---------- version information ----------
    def version_report():
        import platform
        import PIL
        launcher = os.environ.get("THREADS_COLLAGE_LAUNCHER") or (
            "1.0.0" if getattr(sys, "frozen", False) else "not used (running the script directly)")
        lines = [
            "%s %s  (released %s)" % (APP_NAME, APP_VERSION, APP_RELEASE_DATE),
            "Launcher (%s): %s" % ("ThreadsCollage.app" if IS_MAC else "ThreadsCollage.exe", launcher),
            "",
            "App file:        %s" % os.path.abspath(__file__),
            "Settings folder: %s" % data_dir(),
            "",
            "Python %s  |  Pillow %s  |  %s" % (platform.python_version(), PIL.__version__,
                                               ("macOS " + platform.mac_ver()[0]) if IS_MAC
                                               else "%s %s" % (platform.system(), platform.release())),
            "iPhone HEIC support: %s  |  Drag and drop: %s" % ("yes" if HEIC_OK else "no",
                                                               "yes" if DND_OK else "no"),
        ]
        return "\n".join(lines)

    def history_text(only=None):
        out = []
        for ver, date, items in CHANGELOG:
            if only and ver != only:
                continue
            out.append("Version %s  (%s)" % (ver, date))
            out += ["  • " + it for it in items]
            out.append("")
        return "\n".join(out).rstrip()

    def show_about():
        win = tk.Toplevel(root)
        win.title("About %s" % APP_NAME)
        win.transient(root)
        s = ui["scale"]
        win.geometry("%dx%d" % (min(int(620 * s), root.winfo_screenwidth() - 60),
                                min(int(520 * s), root.winfo_screenheight() - 80)))
        ttk.Label(win, text="%s  %s" % (APP_NAME, APP_VERSION), font=F["title"]).pack(
            anchor="w", padx=14, pady=(12, 2))
        ttk.Label(win, text="Released %s" % APP_RELEASE_DATE, foreground="#555").pack(anchor="w", padx=14)
        row = ttk.Frame(win)
        row.pack(side="bottom", fill="x", padx=14, pady=(0, 12))      # buttons always visible
        tf = ttk.Frame(win)
        tf.pack(fill="both", expand=True, padx=14, pady=10)
        txt = tk.Text(tf, wrap="word", font=F["mono"], relief="flat", padx=10, pady=8, bg="#f6f6f6")
        tsb = ttk.Scrollbar(tf, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        report = version_report()
        txt.insert("end", report + "\n\nVERSION HISTORY\n\n" + history_text())
        txt.configure(state="disabled")

        def copy():
            root.clipboard_clear()
            root.clipboard_append(report)
            status_var.set("Version information copied to the clipboard.")
        ttk.Button(row, text="Copy version info", command=copy).pack(side="left")
        ttk.Button(row, text="Open app folder",
                   command=lambda: open_folder(os.path.dirname(os.path.abspath(__file__)))).pack(side="left", padx=6)
        ttk.Button(row, text="Close", command=win.destroy).pack(side="right")

    # footer: always visible below the scrolling left column
    tsrow = ttk.Frame(footer_left)
    tsrow.pack(fill="x", pady=(0, 6))
    ttk.Label(tsrow, text="Text size").pack(side="left")
    cb_scale = ttk.Combobox(tsrow, textvariable=scale_var, values=UI_SCALES, state="readonly", width=6)
    cb_scale.pack(side="left", padx=(6, 0))

    def scale_selected(_e=None):
        apply_scale(int(scale_var.get().rstrip("%")))
        persist()
        root.after(60, left_resized)
        refresh()
    cb_scale.bind("<<ComboboxSelected>>", scale_selected)
    for key, d in (("<%s-plus>" % MOD, 10), ("<%s-equal>" % MOD, 10), ("<%s-minus>" % MOD, -10),
                   ("<%s-0>" % MOD, 0)):
        def step(_e, d=d):
            cur = int(scale_var.get().rstrip("%"))
            new = 100 if d == 0 else min(250, max(100, cur + d))
            scale_var.set("%d%%" % new)
            scale_selected()
        root.bind_all(key, step)
    ttk.Label(tsrow, text="%s + / %s \u2212" % (MOD_LABEL, MOD_LABEL), foreground="#777").pack(side="left", padx=(8, 0))

    upd = ttk.Frame(footer_left)
    upd.pack(fill="x")
    ver_lbl = ttk.Label(upd, text="Version %s" % APP_VERSION, foreground="#1d4ed8", cursor="hand2")
    ver_lbl.pack(side="left")
    ver_lbl.bind("<Button-1>", lambda e: show_about())
    ttk.Button(upd, text="Install update...", command=update_dialog).pack(side="right")
    ttk.Button(upd, text="About", command=show_about).pack(side="right", padx=(0, 4))

    # "What's new" once, the first time a new version opens (not on a first install)
    last = saved.get("last_version")
    if not last and saved:                      # settings from before versions were recorded
        last = "2.1.0" if "wm_text" in saved else "2.0.0"
    if last and vtuple(last) < vtuple(APP_VERSION):
        news = [history_text(v) for v, _, _ in CHANGELOG if vtuple(last) < vtuple(v) <= vtuple(APP_VERSION)]
        root.after(700, lambda: messagebox.showinfo(
            "What's new", "Updated from %s to %s.\n\n%s" % (last, APP_VERSION, "\n\n".join(news))))
    if last != APP_VERSION:
        root.after(800, persist)

    def on_close():
        persist()
        cancel_flag.set()
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)

    def route_files(paths):
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        if len(paths) == 1 and os.path.splitext(paths[0])[1].lower() in (".py", ".zip"):
            install_update_from(paths[0])
        elif len(paths) <= 3 and not any(os.path.isdir(p) for p in paths):
            nb.select(0)
            add_single(paths)
        else:
            nb.select(1)
            add_batch(paths)

    if IS_MAC:
        # macOS menu bar and Finder integration
        try:
            root.createcommand("tk::mac::Quit", on_close)                    # Cmd+Q saves settings
            root.createcommand("tkAboutDialog", show_about)                  # Threads Collage > About
            root.createcommand("::tk::mac::OpenDocument",                    # files dropped on the Dock icon
                               lambda *paths: root.after(100, lambda: route_files(list(paths))))
        except Exception:
            pass

    tip = "Drag in 2 or 3 photos, or a whole folder to batch." if DND_OK else ""
    if tip:
        ttk.Label(footer_left, text=tip, foreground="#555").pack(side="bottom", anchor="w", pady=(6, 0))

    # files passed on the command line (e.g. dropped onto the .exe icon)
    root.after(50, load_fonts)
    initial = [a for a in sys.argv[1:] if os.path.exists(a)]
    sync_single()
    if initial:
        if len(initial) <= 3 and not any(os.path.isdir(p) for p in initial):
            root.after(200, lambda: add_single(initial))
        else:
            root.after(200, lambda: (nb.select(1), add_batch(initial)))
    root.mainloop()


if __name__ == "__main__":
    run_gui()
