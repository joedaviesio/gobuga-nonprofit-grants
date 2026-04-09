"""Generate billboard-light.png at 1920x960 using Pillow."""

from PIL import Image, ImageDraw, ImageFont
import math, random

W, H = 1920, 960

# Brand colors
BLUE = (93, 120, 197)
YELLOW = (255, 206, 49)
RED = (226, 80, 99)
GREEN = (110, 231, 183)

def radial_gradient(img, cx, cy, rx, ry, color, opacity):
    """Paint a soft radial ellipse splash onto img."""
    px = img.load()
    w, h = img.size
    # Only iterate over the bounding box of the ellipse
    x0 = max(0, int(cx - rx * 1.5))
    x1 = min(w, int(cx + rx * 1.5))
    y0 = max(0, int(cy - ry * 1.5))
    y1 = min(h, int(cy + ry * 1.5))
    for y in range(y0, y1):
        for x in range(x0, x1):
            dx = (x - cx) / rx
            dy = (y - cy) / ry
            d = math.sqrt(dx * dx + dy * dy)
            if d < 1.0:
                # Smooth falloff
                alpha = opacity * (1.0 - d) ** 1.5
                r0, g0, b0 = px[x, y]
                r = int(r0 + (color[0] - r0) * alpha)
                g = int(g0 + (color[1] - g0) * alpha)
                b = int(b0 + (color[2] - b0) * alpha)
                px[x, y] = (r, g, b)

# --- Create base white image ---
img = Image.new("RGB", (W, H), (255, 255, 255))

# --- Splash gradients ---
splashes = [
    (0.10 * W, 0.20 * H, 0.45 * W, 0.34 * H, BLUE, 0.50),
    (0.90 * W, 0.80 * H, 0.40 * W, 0.29 * H, RED, 0.45),
    (0.50 * W, 0.50 * H, 0.35 * W, 0.29 * H, YELLOW, 0.40),
    (0.80 * W, 0.15 * H, 0.30 * W, 0.24 * H, YELLOW, 0.30),
    (0.25 * W, 0.85 * H, 0.30 * W, 0.24 * H, BLUE, 0.28),
    (0.60 * W, 0.70 * H, 0.25 * W, 0.19 * H, RED, 0.20),
    (0.55 * W, 0.90 * H, 0.35 * W, 0.26 * H, GREEN, 0.45),
    (0.05 * W, 0.60 * H, 0.28 * W, 0.22 * H, GREEN, 0.30),
]

print("Painting splash gradients...")
for cx, cy, rx, ry, color, opacity in splashes:
    radial_gradient(img, cx, cy, rx, ry, color, opacity)

draw = ImageDraw.Draw(img)

# --- Load fonts ---
# Try to load Geist, fall back to system fonts
def load_font(names, size):
    """Try font names in order."""
    import subprocess
    # Check common macOS font paths
    paths = []
    for name in names:
        candidates = [
            f"/System/Library/Fonts/{name}.ttf",
            f"/System/Library/Fonts/{name}.otf",
            f"/Library/Fonts/{name}.ttf",
            f"/Library/Fonts/{name}.otf",
            f"/System/Library/Fonts/Supplemental/{name}.ttf",
            f"/System/Library/Fonts/Supplemental/{name}.otf",
        ]
        paths.extend(candidates)
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (OSError, IOError):
            continue
    # Fallback: try generic
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()

# Try to find fonts - check what's available from the Next.js app cache
import glob
next_font_files = glob.glob("/Users/joedaviesio/Desktop/gobuga-nonprofit-grants/frontend/node_modules/next/dist/**/*.woff2", recursive=True)
geist_files = glob.glob("/Users/joedaviesio/Desktop/gobuga-nonprofit-grants/frontend/node_modules/next/dist/**/geist*", recursive=True)

# Use system fonts as primary approach
header_font = load_font(["Geist-Black", "Geist-Bold", "HelveticaNeue-Bold", "Helvetica-Bold", "Arial Bold"], 180)
sub_font = load_font(["DM Sans", "DMSans-SemiBold", "DM-Sans-SemiBold", "HelveticaNeue", "Helvetica"], 44)
url_font = load_font(["DM Sans", "DMSans-Medium", "DM-Sans-Medium", "HelveticaNeue-Light", "Helvetica"], 36)
stamp_font = load_font(["DM Sans", "DMSans-Bold", "DM-Sans-Bold", "HelveticaNeue-Bold", "Helvetica-Bold"], 22)

# --- Header: "gobuga" centered ---
header_text = "gobuga"
header_color = (58, 58, 58)  # #3a3a3a

bbox = draw.textbbox((0, 0), header_text, font=header_font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
hx = (W - tw) // 2
hy = (H - th) // 2 - 60

# White stroke effect (draw text offset in all directions)
stroke_width = 3
for dx in range(-stroke_width, stroke_width + 1):
    for dy in range(-stroke_width, stroke_width + 1):
        if dx * dx + dy * dy <= stroke_width * stroke_width:
            draw.text((hx + dx, hy + dy), header_text, fill=(255, 255, 255), font=header_font)
# Main text on top
draw.text((hx, hy), header_text, fill=header_color, font=header_font)

# --- Subheader: "Helps with grants" centered ---
sub_text = "Helps with grants"
sub_color = (93, 120, 197)  # brand blue

bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
sx = (W - tw) // 2
sy = hy + 180 + 20

# White stroke
for dx in range(-2, 3):
    for dy in range(-2, 3):
        if dx * dx + dy * dy <= 4:
            draw.text((sx + dx, sy + dy), sub_text, fill=(255, 255, 255), font=sub_font)
draw.text((sx, sy), sub_text, fill=sub_color, font=sub_font)

# --- URL: "gobuga.org" centered ---
url_text = "gobuga.org"
url_color = (0, 0, 0, 153)  # rgba(0,0,0,0.6) on white ≈ (153,153,153)
url_color_rgb = (128, 128, 128)

bbox = draw.textbbox((0, 0), url_text, font=url_font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
ux = (W - tw) // 2
uy = sy + 44 + 24

draw.text((ux, uy), url_text, fill=url_color_rgb, font=url_font)

# --- Kiwi Tech stamp (bottom right, rotated) ---
stamp_text = "KIWI TECH"
stamp_green = (22, 163, 74)       # #16a34a
stamp_border = (34, 197, 94)      # #22c55e
stamp_bg = (34, 197, 94, 20)

# Create stamp on a separate image so we can rotate it
stamp_padding_x, stamp_padding_y = 28, 14
kiwi_size = 30

bbox = draw.textbbox((0, 0), stamp_text, font=stamp_font)
stw, sth = bbox[2] - bbox[0], bbox[3] - bbox[1]

total_w = kiwi_size + 12 + stw + stamp_padding_x * 2
total_h = sth + stamp_padding_y * 2

# Make stamp image larger for rotation padding
pad = 30
stamp_img = Image.new("RGBA", (total_w + pad * 2, total_h + pad * 2), (0, 0, 0, 0))
sd = ImageDraw.Draw(stamp_img)

# Background
sd.rounded_rectangle(
    [pad, pad, pad + total_w, pad + total_h],
    radius=8,
    fill=(234, 255, 240, 50),
    outline=stamp_border,
    width=3,
)

# Kiwi bird (simple)
kx, ky = pad + stamp_padding_x, pad + stamp_padding_y + sth // 2

# Body circle
sd.ellipse([kx, ky - 10, kx + 22, ky + 10], fill=stamp_green)
# Beak
sd.polygon([(kx + 20, ky - 2), (kx + 30, ky - 5), (kx + 20, ky + 2)], fill=stamp_green)
# Eye
sd.ellipse([kx + 16, ky - 5, kx + 19, ky - 2], fill=(255, 255, 255))
# Legs
sd.line([(kx + 8, ky + 9), (kx + 6, ky + 16)], fill=stamp_green, width=2)
sd.line([(kx + 14, ky + 9), (kx + 12, ky + 16)], fill=stamp_green, width=2)

# Text
text_x = pad + stamp_padding_x + kiwi_size + 12
text_y = pad + stamp_padding_y
sd.text((text_x, text_y), stamp_text, fill=stamp_green, font=stamp_font)

# Rotate
stamp_img = stamp_img.rotate(4, resample=Image.BICUBIC, expand=True)

# Paste onto main image
paste_x = W - stamp_img.width - 50
paste_y = H - stamp_img.height - 40
img.paste(Image.new("RGB", stamp_img.size, (255, 255, 255)), (paste_x, paste_y), stamp_img.split()[3])
img.paste(stamp_img, (paste_x, paste_y), stamp_img)

# --- Save ---
out = "/Users/joedaviesio/Desktop/gobuga-nonprofit-grants/billboard-light.png"
img.save(out, "PNG")
print(f"Saved {out} ({img.size[0]}x{img.size[1]})")
