"""SFP 'Failed Swing' trade-card generator (box edition) — renders LIVE trade data into the slots.

Adapted from Desktop\\sfp_cards\\sfp_card.py. Identical spec-driven render (positions/fonts/colors read
from slots-spec.json — NOT hardcoded). The ONLY change vs the original is that the ASSETS / FONTS / SPEC
paths are configurable via env so this can run from the box working dir:

    CARD_ASSETS_DIR  -> dir holding failed-swing-win.png, failed-swing-loss.png, slots-spec.json
                        (default: ~/card_assets/assets)
    CARD_FONTS_DIR   -> dir holding Anton-Regular.ttf, BarlowCondensed-*.ttf
                        (default: <CARD_ASSETS_DIR>/../fonts, i.e. ~/card_assets/fonts)

DISPLAY/NOTIFICATION ONLY. Does not import or touch the trading engine.
"""
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _assets_dir() -> Path:
    return Path(os.environ.get("CARD_ASSETS_DIR", str(Path.home() / "card_assets" / "assets"))).expanduser()


def _fonts_dir() -> Path:
    env = os.environ.get("CARD_FONTS_DIR")
    if env:
        return Path(env).expanduser()
    # default: sibling "fonts" dir next to the assets dir (~/card_assets/fonts)
    return _assets_dir().parent / "fonts"


ASSETS = _assets_dir()
FONTS = _fonts_dir()
SPEC = json.loads((ASSETS / "slots-spec.json").read_text(encoding="utf-8"))
BG = {"win": ASSETS / "failed-swing-win.png", "loss": ASSETS / "failed-swing-loss.png"}
BAND_BG = "#081428"

_FONT_FILE = {
    ("anton", None): "Anton-Regular.ttf",
    ("barlow condensed", 500): "BarlowCondensed-Medium.ttf",
    ("barlow condensed", 600): "BarlowCondensed-SemiBold.ttf",
    ("barlow condensed", 700): "BarlowCondensed-Bold.ttf",
}
_fc = {}


def font(spec_str, size_override=None):
    m = re.match(r"(Anton|Barlow Condensed)\s+(?:(\d{3})\s+)?(\d+)px", spec_str)
    fam = m.group(1).lower()
    weight = int(m.group(2)) if m.group(2) else None
    size = size_override or int(m.group(3))
    key = ("anton", None) if fam == "anton" else (fam, weight)
    ck = (key, size)
    if ck not in _fc:
        _fc[ck] = ImageFont.truetype(str(FONTS / _FONT_FILE[key]), size)
    return _fc[ck]


def color(c, variant=None):
    if isinstance(c, dict):
        c = c.get(variant, c.get("win"))
    c = c.strip()
    if c.startswith("#"):
        h = c[1:]
        if len(h) == 6:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        if len(h) == 8:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
    m = re.match(r"rgba?\(([^)]+)\)", c)
    p = [x.strip() for x in m.group(1).split(",")]
    a = int(round(float(p[3]) * 255)) if len(p) > 3 else 255
    return (int(float(p[0])), int(float(p[1])), int(float(p[2])), a)


def render_card(cd, out_path):
    variant = cd["outcome"]
    img = Image.open(BG[variant]).convert("RGBA")
    d = ImageDraw.Draw(img)
    S = SPEC["slots"]
    band = color(BAND_BG)

    def erase(x0, y0, x1, y1):
        d.rectangle([x0, y0, x1, y1], fill=band)

    def val(key, text, col=None):
        sl = S[key]
        f = font(sl["value_font"])
        col = col or color(sl["value_color"], variant)
        x, y = sl["value_xy"]
        bb = d.textbbox((x, y), text, font=f, anchor="la")
        erase(bb[0] - 4, bb[1] - 3, bb[2] + 4, bb[3] + 3)
        d.text((x, y), text, font=f, fill=col, anchor="la")
        return d.textlength(text, font=f)

    def time_line(key, text):
        # FIX C: a small dim time under the value (fill time on ENTRY; close time on the exit that HIT).
        if not text:
            return
        sl = S[key]
        tx, ty = sl.get("time_xy", [sl["value_xy"][0], sl["value_xy"][1] + 44])
        tf = font(sl.get("time_font", sl["label_font"]))
        tc = color(sl.get("time_color", sl["label_color"]))
        d.text((tx, ty), text, font=tf, fill=tc, anchor="la")

    def strike(key, text):
        # FIX C: a thin muted line through the value of the exit that did NOT fill.
        sl = S[key]
        f = font(sl["value_font"])
        x, y = sl["value_xy"]
        bb = d.textbbox((x, y), text, font=f, anchor="la")
        my = (bb[1] + bb[3]) // 2
        d.line([bb[0] - 3, my, bb[2] + 3, my], fill=color(sl.get("strike_color", "#8fa8c6")), width=3)

    # row 1
    val("pair", cd["pair"]); val("side", cd["side"]); val("leverage", cd["leverage"])

    # r_result (Anton) + roi_pct — each in its OWN spec slot (re-spaced result row)
    val("r_result", cd["r_result"])
    if cd.get("roi_pct"):
        val("roi_pct", cd["roi_pct"])

    # outcome PILL — program-drawn (fill + border + centered text); box grows with text
    ob = S["outcome_badge"]
    bx, by, bw, bh = ob["box"]
    of = font(ob["font"])
    txt = cd["outcome_badge"]
    bw = max(bw, int(d.textlength(txt, font=of)) + 44)
    r = bh // 2
    br = color(ob["border_color"], variant)
    fill = (br[0], br[1], br[2], 46)                       # soft tint of the theme colour
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    od.rounded_rectangle([bx, by, bx + bw, by + bh], radius=r, fill=fill, outline=br, width=2)
    img = Image.alpha_composite(img, ov); d = ImageDraw.Draw(img)
    d.text((bx + bw / 2, by + bh / 2), txt, font=of, fill=color(ob["text_color"], variant), anchor="mm")

    # funnel — each chip: state treatment + real value (auto-shrink to fit)
    fn = S["funnel"]
    st = fn["state_treatment"]
    mk_f = font(st["marker"]["font"])
    for chip in fn["chips"]:
        info = cd["funnel"].get(chip["id"], {})
        state = info.get("state", "neutral")
        treat = st[state]
        cx, cy, cw, ch = chip["box"]
        erase(cx, cy, cx + cw, cy + ch)
        ov = Image.new("RGBA", img.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
        od.rounded_rectangle([cx, cy, cx + cw, cy + ch], radius=fn["radius"],
                             fill=color(treat["fill"]), outline=color(treat["border"]), width=2)
        img = Image.alpha_composite(img, ov); d = ImageDraw.Draw(img)
        # label (top)
        lx, ly = chip["label_xy"]
        d.text((lx, ly), chip["label"], font=font(fn["label_font"]),
               fill=color(treat["label_color"]), anchor="la")
        # value (bottom) — shrink to fit value_fit_width
        vx, vy = chip["value_xy"]
        vtxt = str(info.get("value", "") or "")
        if vtxt:
            sz = int(re.search(r"(\d+)px", fn["value_font"]).group(1))
            vf = font(fn["value_font"], sz)
            while sz > 13 and d.textlength(vtxt, font=vf) > fn["value_fit_width"]:
                sz -= 1; vf = font(fn["value_font"], sz)
            d.text((vx, vy), vtxt, font=vf, fill=color(treat["value_color"]), anchor="la")
        # marker top-right
        if treat["marker"]:
            d.text((cx + cw - 26, cy + 12), treat["marker"], font=mk_f,
                   fill=color(treat["marker_color"]), anchor="la")

    # entry / tp / stop — value + (FIX C) fill/close time on the box that HIT, strike-through on the miss
    val("entry", cd["entry"]); time_line("entry", cd.get("entry_time", ""))
    val("take_profit", cd["take_profit"])
    if cd.get("take_profit_struck"):
        strike("take_profit", cd["take_profit"])
    else:
        time_line("take_profit", cd.get("take_profit_time", ""))
    val("stop", cd["stop"])
    if cd.get("stop_struck"):
        strike("stop", cd["stop"])
    else:
        time_line("stop", cd.get("stop_time", ""))

    img.convert("RGB").save(out_path, "PNG")
    return out_path


# real BTC -1.07R close (2026-07-10 20:27) — LONG, placed-then-STOPPED => funnel all passed.
BTC_TEST = {
    "outcome": "loss", "pair": "BTCUSDT", "side": "LONG", "leverage": "30x",
    "r_result": "-1.07R", "roi_pct": "-14.6%", "outcome_badge": "STOPPED",
    "entry": "63,945.50", "take_profit": "64,817.54", "stop": "63,654.95",
    "entry_time": "Jul 10, 4:27 PM ET", "stop_time": "Jul 10, 8:27 PM ET",
    "take_profit_time": "", "stop_struck": False, "take_profit_struck": True,
    "funnel": {
        "pattern":     {"value": "Two-Candle SFP", "state": "passed"},
        "swept_level": {"value": "63,772.8",       "state": "passed"},
        "with_trend":  {"value": "PS-TRAIL30",     "state": "passed"},
        "fresh_inst":  {"value": "YES",            "state": "passed"},
        "bos":         {"value": "3m CONFIRMED",   "state": "passed"},
    },
}

if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "card_btc_box_test.png"
    print("wrote", render_card(BTC_TEST, out))
