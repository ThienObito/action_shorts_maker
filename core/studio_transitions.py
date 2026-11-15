"""Bounded-memory chapter effects on a constant timeline.

The Studio derivative uses real short xfade clips for cross-dissolve. Direct
pipeline fallback uses temporal blending so it can render a long video in one pass.
"""
from __future__ import annotations

STYLES = ("none", "cross_dissolve", "flash_cut", "zoom_punch", "dip_black", "whip_pan")


def transition_graph(style: str, chapters: list[float], duration: float,
                     width: int = 1920, height: int = 1080,
                     input_label: str = "0:v") -> tuple[str, str]:
    if style not in STYLES:
        raise ValueError("Invalid transition style")
    events = sorted(set(round(float(t), 3) for t in chapters if .5 < float(t) < duration - .5))
    if style == "none" or not events:
        return f"[{input_label}]null[transitions]", "transitions"
    windows = "+".join(f"between(t,{t-.15:.3f},{t+.15:.3f})" for t in events)
    if style == "cross_dissolve":
        # Bounded temporal blend for direct one-pass pipeline. Studio derivatives
        # render exact xfade clips on independent short seek windows instead.
        effect = f"tmix=frames=8:enable='{windows}',setpts=N/(60*TB)"
    elif style in ("flash_cut", "dip_black"):
        val = .45 if style == "flash_cut" else -.75
        effect = f"eq=brightness='if({windows},{val},0)':eval=frame"
    elif style == "zoom_punch":
        frames = "+".join(f"between(on,{round((t-.15)*60)},{round((t+.15)*60)})" for t in events)
        zoom = f"1+0.12*({frames})"
        effect = (f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"d=1:s={width}x{height}:fps=60")
    else:
        effect = (f"crop=w={round(width*.90)}:h={height}:x='(in_w-out_w)*({windows})':y=0,"
                  f"scale={width}:{height},gblur=sigma=1.5:enable='{windows}'")
    return f"[{input_label}]{effect}[transitions]", "transitions"
