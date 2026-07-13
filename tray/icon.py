"""
tray/icon.py
-------------
Generates the system-tray icon image using Pillow.

Two states
----------
* PROTECTED (green shield)  — service is active and blocking is enabled
* WARNING   (amber shield)  — service is reachable but blocking is disabled
* ERROR     (red circle)    — service is unreachable / not running
"""

from __future__ import annotations

from enum import Enum, auto
from PIL import Image, ImageDraw


class IconState(Enum):
    PROTECTED = auto()   # green shield
    WARNING   = auto()   # amber shield — blocking disabled
    ERROR     = auto()   # red  — service unreachable


# Colour palette
_COLOURS = {
    IconState.PROTECTED: {
        "bg":     (30,  200, 100),   # vivid green
        "border": (10,  150,  70),
        "symbol": (255, 255, 255),
    },
    IconState.WARNING: {
        "bg":     (255, 180,  30),   # amber
        "border": (200, 130,  10),
        "symbol": (255, 255, 255),
    },
    IconState.ERROR: {
        "bg":     (220,  50,  50),   # red
        "border": (160,  20,  20),
        "symbol": (255, 255, 255),
    },
}


def create_icon(state: IconState = IconState.PROTECTED, size: int = 64) -> Image.Image:
    """
    Create and return a PIL Image for the given icon state.

    Parameters
    ----------
    state : One of IconState.PROTECTED / WARNING / ERROR.
    size  : Pixel dimensions of the square image (default 64).
    """
    colours = _COLOURS[state]
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw shield shape (pentagon-ish polygon)
    margin = size // 8
    top    = margin
    bottom = size - margin
    mid_x  = size // 2

    shield_pts = [
        (margin,      top + size // 6),    # top-left
        (mid_x,       top),                # top-center
        (size-margin, top + size // 6),    # top-right
        (size-margin, bottom - size // 4), # right
        (mid_x,       bottom),             # bottom-tip
        (margin,      bottom - size // 4), # left
    ]

    # Shield fill + border
    draw.polygon(shield_pts, fill=colours["bg"])
    draw.polygon(shield_pts, outline=colours["border"])

    # Symbol inside shield
    s = colours["symbol"]
    cx, cy = mid_x, size // 2

    if state == IconState.PROTECTED:
        # Draw a tick / check-mark
        tick_pts = [
            (cx - size//5, cy),
            (cx - size//10, cy + size//6),
            (cx + size//5, cy - size//7),
        ]
        lw = max(2, size // 14)
        draw.line(tick_pts, fill=s, width=lw)

    elif state == IconState.WARNING:
        # Draw exclamation mark
        lw = max(2, size // 12)
        draw.rectangle(
            [cx - lw//2, cy - size//5, cx + lw//2, cy + size//10],
            fill=s,
        )
        draw.ellipse(
            [cx - lw//2, cy + size//8, cx + lw//2, cy + size//6],
            fill=s,
        )

    elif state == IconState.ERROR:
        # Draw X
        lw = max(2, size // 12)
        r = size // 5
        draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill=s, width=lw)
        draw.line([(cx + r, cy - r), (cx - r, cy + r)], fill=s, width=lw)

    return img
