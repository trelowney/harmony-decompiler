"""Parse the pointer table in section 6 and inspect the records it points at.

Section 6 turns out to be an index into the region below 0xF35B, which the
section table does not cover: 114 records of ~240-350 B each. See FORMAT.md §4b
and §4d.

Usage:
    python records.py [config.bin|config.EZHex]
"""
import sys
from collections import Counter

from _paths import CONFIG_BASE as BASE, get_blob

data = get_blob(sys.argv[1] if len(sys.argv) > 1 else None)

S6 = 0x00FEAF
S6_LEN = 6968


def u24(off):
    return int.from_bytes(data[off:off + 3], "little")


print(f"section 6 @0x{S6:06X}, {S6_LEN} B")
print(f"first 3 bytes: {data[S6:S6+3].hex(' ').upper()}  "
      f"(u16 count = {int.from_bytes(data[S6:S6+2],'little')})\n")

# --- read u24 pointers from +3 for as long as they increase and stay in range ---
ptrs = []
off = S6 + 3
while off + 3 <= S6 + S6_LEN:
    v = u24(off)
    o = v - BASE
    if not (0 <= o < len(data)):
        break
    if ptrs and o <= ptrs[-1]:
        break
    ptrs.append(o)
    off += 3

consumed = off - S6
print(f"contiguous increasing run: {len(ptrs)} pointers, {consumed} B consumed "
      f"of {S6_LEN} ({S6_LEN - consumed} left)")
print(f"first target 0x{ptrs[0]:06X}, last target 0x{ptrs[-1]:06X}")
print(f"first byte past the run: 0x{data[off]:02X} @0x{off:06X}\n")

# --- record sizes ---
sizes = [ptrs[i + 1] - ptrs[i] for i in range(len(ptrs) - 1)]
print(f"=== RECORD SIZES ===")
print(f"min={min(sizes)}  max={max(sizes)}  mean={sum(sizes)/len(sizes):.1f}")
print("most common: " + ", ".join(f"{s}B x{n}" for s, n in Counter(sizes).most_common(8)))
print(f"total covered: {ptrs[-1]-ptrs[0]} B (0x{ptrs[0]:06X}-0x{ptrs[-1]:06X})\n")

# --- first bytes of each record: is there a common header? ---
print(f"=== FIRST BYTE OF EACH RECORD ===")
firsts = Counter(data[p] for p in ptrs)
print(", ".join(f"0x{b:02X}={n}" for b, n in firsts.most_common(10)))
seconds = Counter(data[p + 1] for p in ptrs)
print("second byte: " + ", ".join(f"0x{b:02X}={n}" for b, n in seconds.most_common(10)))

# --- dump the first few records in full ---
print(f"\n=== FIRST 3 RECORDS, COMPLETE ===")
for i in range(3):
    start, end = ptrs[i], ptrs[i + 1]
    print(f"\n--- record #{i}  @0x{start:06X}  {end-start} B ---")
    for b0 in range(start, end, 16):
        chunk = data[b0:min(b0 + 16, end)]
        hexs = " ".join(f"{x:02X}" for x in chunk).ljust(47)
        txt = "".join(chr(x) if 32 <= x < 127 else "." for x in chunk)
        print(f"{b0:06X}  {hexs}  |{txt}|")
