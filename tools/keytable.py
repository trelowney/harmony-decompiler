"""Parse the key table inside record #0.

Record layout (derived from the first three records):
  00 <u24 ptr to previous record +9>
  01 00 <u24 ptr = start-6>
  <u8 count> then entries: <u8 keycode> <u16 target> <u8 0x7F>

The count byte at REC0+9 reads 0x33 = 51, which is exactly how many entries
follow - the table is self-describing, which is why it is treated as solved
rather than guessed. See FORMAT.md §4e.

Usage:
    python keytable.py [config.bin|config.EZHex]
"""
import sys
from collections import Counter

from _paths import CONFIG_BASE as BASE, get_blob

data = get_blob(sys.argv[1] if len(sys.argv) > 1 else None)

REC0 = 0x0000F1

print("=== RECORD #0 HEADER ===")
print(f"  {data[REC0:REC0+11].hex(' ').upper()}")
a = int.from_bytes(data[REC0 + 1:REC0 + 4], "little") - BASE
b = int.from_bytes(data[REC0 + 6:REC0 + 9], "little") - BASE
print(f"  ptr A = 0x{a:06X}   ptr B = 0x{b:06X}  (= start-6: {b == REC0-6})")
print(f"  byte @+9 = 0x{data[REC0+9]:02X} = {data[REC0+9]}\n")

# --- 4-byte entries from offset +10 ---
off = REC0 + 10
entries = []
while off + 4 <= len(data):
    kc, target, term = data[off], int.from_bytes(data[off+1:off+3], "little"), data[off+3]
    if term != 0x7F:
        break
    entries.append((off, kc, target))
    off += 4

print(f"=== TABLE: {len(entries)} entries of <u8 code> <u16 target> <0x7F> ===")
print(f"ends at 0x{off:06X}, next bytes: {data[off:off+8].hex(' ').upper()}\n")

print(f"{'#':>3} {'off':>8} {'code':>5} {'target':>6}   "
      f"{'#':>3} {'off':>8} {'code':>5} {'target':>6}")
half = (len(entries) + 1) // 2
for i in range(half):
    left = entries[i]
    row = f"{i:3d} 0x{left[0]:06X}  0x{left[1]:02X}  {left[2]:6d}"
    if i + half < len(entries):
        r = entries[i + half]
        row += f"   {i+half:3d} 0x{r[0]:06X}  0x{r[1]:02X}  {r[2]:6d}"
    print(row)

codes = [e[1] for e in entries]
targets = [e[2] for e in entries]
print(f"\ncode range    : 0x{min(codes):02X} - 0x{max(codes):02X}, "
      f"{len(set(codes))} unique")
dup = [c for c, n in Counter(codes).items() if n > 1]
print(f"duplicate codes: {[hex(c) for c in dup] if dup else 'none'}")
print(f"target range  : {min(targets)} - {max(targets)}, {len(set(targets))} unique")
missing = sorted(set(range(max(targets) + 1)) - set(targets))
print(f"missing targets: {missing[:20]}{' ...' if len(missing) > 20 else ''}")

# --- the repeating block described in FORMAT.md §4f ---
print(f"\n=== REPEATING BLOCK '16 <i> 03 00 <i*8> 00 <i*8> 60 08 8B 2F 03' ===")
hits = 0
for i in range(len(data) - 13):
    if (data[i] == 0x16 and data[i+2] == 0x03 and data[i+3] == 0x00
            and data[i+4] == data[i+1] * 8 and data[i+6] == data[i+1] * 8
            and data[i+7:i+12] == bytes([0x60, 0x08, 0x8B, 0x2F, 0x03])):
        hits += 1
        if hits <= 8:
            print(f"  0x{i:06X}  idx={data[i+1]}  {data[i:i+13].hex(' ').upper()}")
print(f"  ... {hits} occurrences in total")
