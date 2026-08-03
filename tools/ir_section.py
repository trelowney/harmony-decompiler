"""Analyse section 6 (the record index) and section 8 (suspected bytecode).

Note on the pointer statistic printed at the end: it looks convincing, but it is
partly circular. 0x02 is the single most common byte in the blob precisely
because it is the high byte of 0x02xxxx addresses, so "35% of positions decode to
a valid address" partly restates that. The real evidence for 24-bit pointers is
the monotonic table in section 6 - see records.py and FORMAT.md §4b.

Usage:
    python ir_section.py [config.bin|config.EZHex]
"""
import sys
from collections import Counter

from _paths import CONFIG_BASE as BASE, get_blob, hexdump

data = get_blob(sys.argv[1] if len(sys.argv) > 1 else None)

S6, S6_LEN = 0x00FEAF, 6968
S8, S8_LEN = 0x0119F8, 1086

hexdump(data, S6, 96, "SECTION 6 - start")
hexdump(data, S8, 96, "SECTION 8 - start")

# --- section 8 as 3-byte groups ---
print(f"\n=== SECTION 8 as 3-byte groups ({S8_LEN}/3 = {S8_LEN/3}) ===")
s8 = data[S8:S8 + S8_LEN]
print(f"first byte of section: 0x{s8[0]:02X}")
for i in range(1, 46, 3):
    g = s8[i:i + 3]
    if len(g) < 3:
        break
    v24 = int.from_bytes(g, "little")
    if v24 > BASE:
        print(f"  +{i:04d}  {g.hex(' ').upper()}  u24le=0x{v24:06X}  "
              f"-> offset 0x{v24-BASE:06X}")
    else:
        print(f"  +{i:04d}  {g.hex(' ').upper()}  u24le=0x{v24:06X}")

# --- byte distribution in section 6 ---
print(f"\n=== BYTE DISTRIBUTION IN SECTION 6 ===")
s6 = data[S6:S6 + S6_LEN]
c = Counter(s6)
print(f"distinct values: {len(c)}")
print("most common: " + ", ".join(f"0x{b:02X}({chr(b) if 32<=b<127 else '.'})={n}"
                                  for b, n in c.most_common(12)))
print(f"min=0x{min(c):02X} max=0x{max(c):02X}")

# --- how often three bytes anywhere decode to a plausible address ---
print(f"\n=== 24-BIT POINTER SANITY CHECK (see caveat in the docstring) ===")
valid = 0
total = 0
for i in range(0, len(data) - 3):
    v = int.from_bytes(data[i:i + 3], "little")
    total += 1
    if BASE <= v < BASE + len(data):
        valid += 1
print(f"positions where 3 bytes decode to an address inside the config: "
      f"{valid} of {total} ({valid/total*100:.1f} %)")
print("(random data would give ~0.5%, but see the docstring before drawing")
print(" conclusions from that comparison)")
