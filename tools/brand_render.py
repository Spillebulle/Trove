"""Render Trove's brand PNGs from the mark + the app's real Archivo font.

Rasterised with the same Chrome the app runs, loading the bundled Archivo woff2
so the wordmark is pixel-for-pixel the app's typeface rather than a look-alike.
Outputs to docs/brand, docs/images and frontend/public.
"""
import base64
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs/brand"
IMAGES = ROOT / "docs/images"
PUBLIC = ROOT / "frontend/public"
FONT = ROOT / "frontend/src/assets/fonts/archivo-variable.woff2"

ORCHID = "#AA85C5"
INK_DARK = "#17181B"   # wordmark on light backgrounds
INK_LIGHT = "#F3F1EC"  # wordmark on dark backgrounds

# The README banner (style guide 17.4): 1354 x 461 on the theme's backdrop,
# the same size every app in the family uses, with the lockup small in the
# middle of it. The margin is the design - a lockup cropped to its own edges
# reads as a stray image; the ground is what makes it a banner.
BANNER_W, BANNER_H = 1354, 461
BANNER_MARK = 150      # mark side, ~1/3 of the height, matching Umber's
BACKDROP_DARK = "#0D0E10"   # --backdrop, dark theme
BACKDROP_PAPER = "#E4E0D9"  # --backdrop, light theme
INK_ON_DARK = "#E6E7E9"     # --text-strong, dark theme
INK_ON_PAPER = "#3A3836"    # --text-strong, light theme

font_b64 = base64.b64encode(FONT.read_bytes()).decode()

FONT_FACE = f"""
@font-face {{
  font-family: 'Archivo';
  src: url('data:font/woff2;base64,{font_b64}') format('woff2-variations');
  font-weight: 100 900; font-style: normal;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
html,body {{ background: transparent; }}
"""

# The mark as a rounded-square lockup piece (viewBox 64), and the coins alone.
def mark_svg(px):
    return f"""<svg width="{px}" height="{px}" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
  <rect width="64" height="64" rx="14" fill="{ORCHID}"/>
  <g fill="none" stroke="#fff" stroke-width="4.4" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="32" cy="20" rx="16" ry="6.8"/>
    <path d="M16 20v13c0 3.8 7.2 6.8 16 6.8s16-3 16-6.8V20"/>
    <path d="M16 33v13c0 3.8 7.2 6.8 16 6.8s16-3 16-6.8V33"/>
  </g>
</svg>"""

def coins_svg(px, stroke="#fff", sw=4.4):
    return f"""<svg width="{px}" height="{px}" viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
  <g fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">
    <ellipse cx="32" cy="20" rx="16" ry="6.8"/>
    <path d="M16 20v13c0 3.8 7.2 6.8 16 6.8s16-3 16-6.8V20"/>
    <path d="M16 33v13c0 3.8 7.2 6.8 16 6.8s16-3 16-6.8V33"/>
  </g>
</svg>"""


def render(page, html, out, width, height, scale=2, omit_bg=True):
    page.set_viewport_size({"width": width, "height": height})
    page.emulate_media()  # default
    page.set_content(f"<!doctype html><html><head><style>{FONT_FACE}</style></head><body>{html}</body></html>")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(150)
    el = page.query_selector("#stage")
    el.screenshot(path=str(out), omit_background=omit_bg, scale="device")
    print("wrote", out.name, f"({width}x{height} @{scale}x)")


def lockup(ink):
    # Horizontal lockup: mark + "TROVE" (all caps, weight 900, tracking -2px per
    # the style guide). Archivo cap height is ~0.72em, so at 96px the caps are
    # ~69px; the mark is 76px - just slightly taller than the letters.
    F = 96
    mark = 76
    return f"""
    <div id="stage" style="display:inline-flex;align-items:center;gap:22px;padding:10px 14px;">
      {mark_svg(mark)}
      <span style="font-family:'Archivo',system-ui,sans-serif;font-weight:900;font-size:{F}px;
        letter-spacing:-2px;color:{ink};line-height:1;text-transform:uppercase;
        position:relative;top:1px;">Trove</span>
    </div>"""


def banner(ground, ink):
    # Mark + wordmark centred on the backdrop, nothing else in it: no tagline,
    # no version. Proportions are the lockup's, scaled up from mark 76 / font 96.
    scale = BANNER_MARK / 76
    F = round(96 * scale)
    gap = round(22 * scale)
    return f"""
    <div id="stage" style="width:{BANNER_W}px;height:{BANNER_H}px;background:{ground};
      display:flex;align-items:center;justify-content:center;gap:{gap}px;">
      {mark_svg(BANNER_MARK)}
      <span style="font-family:'Archivo',system-ui,sans-serif;font-weight:900;font-size:{F}px;
        letter-spacing:{-2 * scale:.1f}px;color:{ink};line-height:1;text-transform:uppercase;
        position:relative;top:{round(scale)}px;">Trove</span>
    </div>"""


def icon_fullbleed(px):
    # Full-bleed orchid square with white coins, no transparency (app/apple icon).
    pad = int(px * 0.19)
    inner = px - 2 * pad
    return f"""
    <div id="stage" style="width:{px}px;height:{px}px;background:{ORCHID};
      display:grid;place-items:center;">
      <div style="width:{inner}px;height:{inner}px;">{coins_svg(inner)}</div>
    </div>"""


def og_card():
    # 1200x630 social card: dark ground with an orchid glow, mark + word + line.
    return f"""
    <div id="stage" style="width:1200px;height:630px;position:relative;overflow:hidden;
      background:#0D0E10;font-family:'Archivo',system-ui,sans-serif;">
      <div style="position:absolute;inset:0;background:
        radial-gradient(680px 420px at 50% -8%, rgba(170,133,197,0.28), transparent 70%);"></div>
      <div style="position:relative;height:100%;display:flex;flex-direction:column;
        align-items:center;justify-content:center;gap:30px;text-align:center;padding:0 80px;">
        <div style="display:flex;align-items:center;gap:28px;">
          {mark_svg(96)}
          <span style="font-weight:900;font-size:120px;letter-spacing:-2.5px;color:#F3F1EC;line-height:1;
            text-transform:uppercase;position:relative;top:2px;">Trove</span>
        </div>
        <div style="font-weight:500;font-size:36px;color:#B7B2A8;max-width:900px;line-height:1.35;">
          Claims the games that are temporarily free, on your own store accounts.
        </div>
      </div>
    </div>"""


with sync_playwright() as p:
    # --disable-lcd-text: without it Chrome antialiases the wordmark with
    # subpixel (ClearType) coverage, which puts red and blue fringes in the
    # letters - colours that are in no palette and that read as artefacts on
    # any ground but the one they were rendered against.
    b = p.chromium.launch(channel="chrome", headless=True,
                          args=["--disable-lcd-text"])
    page = b.new_page(device_scale_factor=2)

    BRAND.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)

    render(page, lockup(INK_DARK), BRAND / "logo-light.png", 500, 120)
    render(page, lockup(INK_LIGHT), BRAND / "logo-dark.png", 500, 120)

    # OG card at exact 1200x630 (scale 1 so the file is 1200x630, not 2400).
    page1 = b.new_page(device_scale_factor=1)
    render(page1, og_card(), BRAND / "og.png", 1200, 630, scale=1, omit_bg=False)

    # The README banner at exact 1354 x 461, both grounds, no transparency.
    IMAGES.mkdir(parents=True, exist_ok=True)
    render(page1, banner(BACKDROP_DARK, INK_ON_DARK), IMAGES / "banner.png",
           BANNER_W, BANNER_H, scale=1, omit_bg=False)
    render(page1, banner(BACKDROP_PAPER, INK_ON_PAPER), IMAGES / "banner-paper.png",
           BANNER_W, BANNER_H, scale=1, omit_bg=False)

    # App icons at exact pixel sizes (scale 1).
    for px, out in [(180, PUBLIC / "apple-touch-icon.png"),
                    (512, PUBLIC / "icon-512.png"),
                    (192, PUBLIC / "icon-192.png")]:
        render(page1, icon_fullbleed(px), out, px, px, scale=1, omit_bg=False)

    b.close()
print("done")
