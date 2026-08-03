"""Search for device names in the blob, including alternative encodings.

Motivation: the region 0x0000-0xF35B (61 KiB, 78% of the blob) contains almost no
ASCII. Yet the remote has an LCD and displays device and activity names on it.
So those names are either encoded differently, or pre-rendered as bitmaps.

Usage:
    python find_text.py [config.bin] [name ...]
"""
import re
import sys
from collections import Counter

from _paths import get_blob

argv = sys.argv[1:]
path = argv[0] if argv and not argv[0].isalpha() else None
needles = [a for a in argv if a != path] or [
    "Panasonic", "Genius", "XBOX", "Watch", "TV", "Menu", "Volume"]

data = get_blob(path)
UNEXPLORED_END = 0x0F35B

print(f"blob {len(data)} B, unexplored region 0x000000-0x{UNEXPLORED_END:06X}\n")

# --- 1. look for specific names in several encodings ---
print("=== NAME SEARCH ===")
for n in needles:
    hits = {}
    for label, enc in (("ascii", "ascii"), ("utf16le", "utf-16-le"),
                       ("utf16be", "utf-16-be")):
        try:
            pat = n.encode(enc)
        except Exception:
            continue
        offs = [m.start() for m in re.finditer(re.escape(pat), data)]
        if offs:
            hits[label] = offs
    ci = [m.start() for m in re.finditer(re.escape(n.encode()), data, re.IGNORECASE)]
    if ci:
        hits["ascii/ci"] = ci
    status = "; ".join(f"{k}: {[hex(o) for o in v[:6]]}" for k, v in hits.items()) \
        or "NOT FOUND"
    print(f"  {n:<12} {status}")

# --- 2. every string in the unexplored region, however short ---
print(f"\n=== STRINGS IN THE UNEXPLORED REGION (length >= 3) ===")
low = data[:UNEXPLORED_END]
found = [(m.start(), m.group().decode("ascii"))
         for m in re.finditer(rb"[\x20-\x7e]{3,}", low)]
print(f"found {len(found)} in {UNEXPLORED_END} bytes = "
      f"one string per {UNEXPLORED_END // max(len(found),1)} B")
for off, s in found[:40]:
    print(f"  0x{off:06X}  {s!r}")

# --- 3. byte value distribution: hints at whether this is bitmap data ---
print(f"\n=== STATISTICS FOR THE UNEXPLORED REGION ===")
c = Counter(low)
print(f"distinct byte values : {len(c)} of 256")
print(f"most common          : " +
      ", ".join(f"0x{b:02X}={n}" for b, n in c.most_common(8)))
printable = sum(v for k, v in c.items() if 32 <= k < 127)
print(f"printable            : {printable} ({printable/len(low)*100:.1f} %)")
print(f"0x00                 : {c[0]} ({c[0]/len(low)*100:.1f} %)")
print(f"0xFF                 : {c[255]} ({c[255]/len(low)*100:.1f} %)")
