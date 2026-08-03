"""Extract printable ASCII strings from the binary config, plus a structure overview.

The distribution map at the end is the interesting part: it shows that essentially
all readable text sits above 0xF35B, and that the 61 KiB below it - 78% of the
blob - contains almost none.

Usage:
    python strings_blob.py [config.bin] [min-length]
"""
import re
import sys
from collections import Counter
from pathlib import Path

from _paths import get_blob

data = get_blob(sys.argv[1] if len(sys.argv) > 1 else None)
MINLEN = int(sys.argv[2]) if len(sys.argv) > 2 else 4

print(f"blob: {len(data)} B\n")

# --- byte distribution: where is text and where is binary data ---
printable = sum(1 for b in data if 32 <= b < 127)
zeros = data.count(0)
print(f"printable bytes : {printable} ({printable/len(data)*100:.1f} %)")
print(f"zero bytes      : {zeros} ({zeros/len(data)*100:.1f} %)")
print()

# --- strings, with offsets ---
pat = re.compile(rb"[\x20-\x7e]{%d,}" % MINLEN)
hits = [(m.start(), m.group().decode("ascii")) for m in pat.finditer(data)]
print(f"found {len(hits)} strings of length >= {MINLEN}\n")

out = Path("blob_strings.txt")
with out.open("w", encoding="utf-8") as f:
    for off, s in hits:
        f.write(f"0x{off:06X}  {s}\n")

# --- where in the file the strings sit ---
print("string distribution across the file (each block = 4 KiB):")
buckets = Counter(off // 4096 for off, _ in hits)
for blk in range(0, (len(data) // 4096) + 1):
    n = buckets.get(blk, 0)
    bar = "#" * min(n // 2, 60)
    print(f"  0x{blk*4096:06X}  {n:4d}  {bar}")

print(f"\nfirst 60 strings:")
for off, s in hits[:60]:
    print(f"  0x{off:06X}  {s}")

print(f"\nfull list written to {out}")
