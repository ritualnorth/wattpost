#!/usr/bin/env python3
"""Compare two audit screenshot folders pixel by pixel.

    python3 scripts/ux/compare.py /tmp/ux-before/shots /tmp/ux-after/shots [--tolerance 0.002]

Prints, per image, the share of pixels that differ (any channel by more than 8/255), and
writes a red-highlight diff PNG next to the "after" folder for anything over the tolerance.
Exit status 1 if any image is over the tolerance, so it can gate a "no visual change" refactor.
Live data (clocks, animations) can cause small differences: look at the diff image before
deciding that a change is real.
"""
import sys
from pathlib import Path
from PIL import Image, ImageChops

a, b = Path(sys.argv[1]), Path(sys.argv[2])
tol = float(sys.argv[sys.argv.index("--tolerance") + 1]) if "--tolerance" in sys.argv else 0.002
bad = 0
for pa in sorted(a.glob("*.png")):
    pb = b / pa.name
    if not pb.exists():
        print(f"MISSING  {pa.name}"); bad += 1; continue
    ia, ib = Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB")
    if ia.size != ib.size:
        print(f"SIZE     {pa.name}: {ia.size} -> {ib.size}"); bad += 1; continue
    diff = ImageChops.difference(ia, ib).convert("L").point(lambda v: 255 if v > 8 else 0)
    n = sum(1 for v in diff.getdata() if v)
    share = n / (ia.size[0] * ia.size[1])
    flag = "ok      " if share <= tol else "CHANGED "
    if share > tol:
        bad += 1
        out = Image.merge("RGB", (diff, Image.new("L", ia.size, 0), Image.new("L", ia.size, 0)))
        out.save(b.parent / f"diff_{pa.name}")
    print(f"{flag} {pa.name:46} {share*100:6.3f}%")
print(f"\n{bad} image(s) over tolerance")
sys.exit(1 if bad else 0)
