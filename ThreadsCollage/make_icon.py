"""
Draws the Threads Collage app icon and saves:
    icon.ico   (Windows: exe, installer, shortcuts)
    icon.icns  (macOS: app bundle, disk image)
    icon.png   (1024 px master)
An original design: three photo tiles in the app's "one on top, two below"
layout on a deep slate rounded square. Run: python make_icon.py
"""
import sys
from PIL import Image, ImageDraw, ImageFilter

S = 1024


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def tile(draw, box, top, bottom, r):
    """A photo tile: vertical gradient with a soft 'sun' and horizon."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    grad = Image.new("RGB", (w, h))
    gd = ImageDraw.Draw(grad)
    for y in range(h):
        gd.line([(0, y), (w, y)], fill=lerp(top, bottom, y / max(1, h - 1)))
    # simple landscape: a sun and a hill, so the tiles read as photos
    gd.ellipse([w * 0.62, h * 0.16, w * 0.62 + h * 0.28, h * 0.44], fill=(255, 244, 214))
    gd.polygon([(0, h * 0.78), (w * 0.35, h * 0.55), (w * 0.7, h * 0.74), (w, h * 0.6), (w, h), (0, h)],
               fill=lerp(bottom, (20, 30, 45), 0.45))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return grad, mask


def draw_icon():
    base = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([70, 90, S - 70, S - 50], radius=200, fill=(0, 0, 0, 110))
    base = Image.alpha_composite(base, sh.filter(ImageFilter.GaussianBlur(28)))
    # background: rounded square with a slate gradient
    bg = Image.new("RGB", (S, S))
    bd = ImageDraw.Draw(bg)
    for y in range(S):
        bd.line([(0, y), (S, y)], fill=lerp((44, 56, 82), (17, 24, 39), y / (S - 1)))
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle([64, 64, S - 64, S - 64], radius=200, fill=255)
    base.paste(bg, (0, 0), m)

    # three tiles on a white "print" card
    card = [196, 196, S - 196, S - 196]
    ImageDraw.Draw(base).rounded_rectangle(card, radius=44, fill=(250, 248, 244))
    pad, gap, r = 30, 22, 26
    cx0, cy0, cx1, cy1 = card[0] + pad, card[1] + pad, card[2] - pad, card[3] - pad
    split = cy0 + int((cy1 - cy0 - gap) * 0.56)
    mid = (cx0 + cx1) // 2
    boxes = [
        ((cx0, cy0, cx1, split), (255, 159, 67), (238, 82, 83)),            # warm sunset, wide
        ((cx0, split + gap, mid - gap // 2, cy1), (72, 219, 251), (10, 132, 180)),   # sky blue
        ((mid + gap // 2, split + gap, cx1, cy1), (29, 209, 161), (16, 120, 100)),   # teal green
    ]
    for (b, top, bottom) in boxes:
        img, mask = tile(None, b, top, bottom, r)
        base.paste(img, (b[0], b[1]), mask)
    return base


def main():
    icon = draw_icon()
    icon.save("icon.png")
    icon.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    written = ["icon.png", "icon.ico"]
    try:
        icon.save("icon.icns")
        written.append("icon.icns")
    except Exception as e:                 # older Pillow without ICNS writing
        print("Note: could not write icon.icns (%s); the Mac app will use the default icon." % e)
    print("Icon written: " + ", ".join(written))


if __name__ == "__main__":
    main()
