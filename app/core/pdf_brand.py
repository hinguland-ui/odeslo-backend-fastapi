"""Odeslo look shared by every PDF: the real logo, a faint logo watermark behind the page, and the brand colours."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Image

LOGO = Path(__file__).resolve().parents[1] / "assets" / "brand" / "logo.png"

# the app's own colours
INK = colors.HexColor("#1c1c22")
INK_SOFT = colors.HexColor("#34343c")
VIOLET = colors.HexColor("#7b6ef6")
VIOLET_DARK = colors.HexColor("#5446d9")
LILAC = colors.HexColor("#ece9fc")
LILAC_LINE = colors.HexColor("#d9d3fb")
PANEL = colors.HexColor("#f6f6f8")
LINE = colors.HexColor("#e7e7ec")
MUTED = colors.HexColor("#6e6e78")
BODY = colors.HexColor("#4a4a54")
GREEN = colors.HexColor("#2f7a34")
RED = colors.HexColor("#c2334d")


def logo(size_mm: float) -> Image:
    return Image(str(LOGO), width=size_mm * mm, height=size_mm * mm)


def watermark(canvas, page_size, size_mm: float = 105, alpha: float = 0.055) -> None:
    """The logo, large and very faint, centred behind everything on the page."""
    w, h = page_size
    s = size_mm * mm
    canvas.saveState()
    canvas.setFillAlpha(alpha)
    canvas.drawImage(str(LOGO), (w - s) / 2, (h - s) / 2 - 8 * mm, width=s, height=s, mask="auto", preserveAspectRatio=True)
    canvas.restoreState()
