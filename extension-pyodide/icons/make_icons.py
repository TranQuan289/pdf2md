"""Generate simple PNG icons (16, 48, 128) for the extension."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent

GRADIENT_TOP = (14, 165, 233)     # indigo-500
GRADIENT_BOTTOM = (139, 92, 246)  # purple-500
WHITE = (255, 255, 255)


def make_icon(size: int):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded gradient background
    for y in range(size):
        ratio = y / max(size - 1, 1)
        r = int(GRADIENT_TOP[0] + (GRADIENT_BOTTOM[0] - GRADIENT_TOP[0]) * ratio)
        g = int(GRADIENT_TOP[1] + (GRADIENT_BOTTOM[1] - GRADIENT_TOP[1]) * ratio)
        b = int(GRADIENT_TOP[2] + (GRADIENT_BOTTOM[2] - GRADIENT_TOP[2]) * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))

    # Mask for rounded corners
    radius = max(size // 6, 2)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    img.putalpha(mask)

    # Text "MD"
    try:
        font_size = int(size * 0.55)
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()
    text = "MD"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1] - size * 0.05),
        text, font=font, fill=WHITE,
    )

    img.save(OUT / f"icon{size}.png")


if __name__ == "__main__":
    for s in (16, 48, 128):
        make_icon(s)
    print("Wrote icon16.png, icon48.png, icon128.png")
