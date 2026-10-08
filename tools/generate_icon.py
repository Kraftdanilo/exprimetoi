"""Génère l'icône Fautexprimer en PNG et ICO multi-résolutions."""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "assets"
ASSET_DIR.mkdir(parents=True, exist_ok=True)
SIZE = 1024


def rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def build_icon() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink, yellow, sky, sand, white = "#2a1c16", "#9b5e43", "#a8d7e8", "#f7efe2", "#fffaf0"

    # Ombre neo-brutaliste et plaque principale.
    rounded(draw, (150, 172, 930, 952), 150, ink)
    rounded(draw, (94, 94, 874, 874), 150, yellow, ink, 34)
    rounded(draw, (160, 160, 808, 808), 104, sand, ink, 24)

    # Microphone.
    rounded(draw, (390, 260, 634, 610), 122, ink)
    rounded(draw, (430, 300, 594, 530), 82, sky)
    draw.arc((280, 380, 744, 760), start=0, end=180, fill=ink, width=58)
    draw.line((512, 700, 512, 790), fill=ink, width=58)
    draw.line((350, 790, 674, 790), fill=ink, width=58)
    draw.line((410, 355, 510, 300), fill=white, width=24)

    # Ondes sonores.
    for offset, width in ((0, 34), (92, 28), (176, 22)):
        draw.arc((208 - offset, 304, 400 + offset, 650 + offset), start=65, end=295, fill=ink, width=width)
        draw.arc((624 - offset, 304, 816 + offset, 650 + offset), start=245, end=115, fill=ink, width=width)

    # Trois barres pour évoquer la voix.
    for x, height in ((250, 90), (300, 150), (350, 210)):
        draw.rounded_rectangle((x, 840 - height, x + 26, 840), radius=13, fill=sky, outline=ink, width=8)
    return image


def main() -> None:
    image = build_icon()
    image.save(ASSET_DIR / "fautexprimer.png", format="PNG", optimize=True)
    static_icon = ROOT / "webapp" / "static" / "fautexprimer.png"
    static_icon.parent.mkdir(parents=True, exist_ok=True)
    image.save(static_icon, format="PNG", optimize=True)
    image.save(
        ASSET_DIR / "fautexprimer.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"OK: {ASSET_DIR / 'fautexprimer.ico'}")


if __name__ == "__main__":
    main()
