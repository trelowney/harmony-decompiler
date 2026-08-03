"""Parse the config header: the section pointer table, plus a peek at each section.

Layout, established from hexdumps:
  0x00: 'AHCM' magic
  0x04: u32 absolute flash address of end-of-config
  0x08: u32 (0x1400) - meaning unknown
  0x0C: array of u32 absolute section addresses, terminated by zeros + 'CMAH'

Addresses are absolute flash addresses. config_base = 0x20000, so
  file_offset = address - 0x20000

Usage:
    python sections.py [config.bin|config.EZHex]
"""
import struct
import sys

from _paths import CONFIG_BASE as BASE, get_blob

data = get_blob(sys.argv[1] if len(sys.argv) > 1 else None)

assert data[:4] == b"AHCM" and data[-4:] == b"MCHA", "not an arch 9 blob"

end_addr, field2 = struct.unpack_from("<II", data, 4)
print(f"magic          : AHCM ... MCHA")
print(f"end address    : 0x{end_addr:06X}  -> offset 0x{end_addr-BASE:06X} (length {len(data)})")
print(f"field @0x08    : 0x{field2:X} ({field2})")
print(f"derived BASE   : 0x{end_addr - len(data) + 4:06X}\n")

# read the pointer table up to CMAH
cmah = data.find(b"CMAH")
ptrs = []
off = 0x0C
while off + 4 <= cmah:
    (v,) = struct.unpack_from("<I", data, off)
    if v == 0:
        break
    ptrs.append(v)
    off += 4

print(f"pointer table: {len(ptrs)} entries, 'CMAH' @0x{cmah:X}\n")
print(f"{'#':>3}  {'address':>9}  {'offset':>8}  {'length':>7}  preview")
print("-" * 90)

bounds = ptrs + [end_addr]
for i, p in enumerate(ptrs):
    o = p - BASE
    ln = bounds[i + 1] - p
    chunk = data[o:o + 24]
    txt = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    hexs = " ".join(f"{b:02X}" for b in chunk[:12])
    print(f"{i:3d}  0x{p:07X}  0x{o:06X}  {ln:7d}  {hexs}  |{txt}|")
