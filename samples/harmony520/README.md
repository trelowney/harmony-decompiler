# Harmony 520 (protocol 9, skin 18)

One config attached by **[@rfritch](https://github.com/rfritch)** to
[issue #45](https://github.com/trelowney/harmony-decompiler/issues/45), after
being asked whether it could be mirrored here. Mirrored unmodified. If they would
rather it were not here, it comes straight back out.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `harmony520-prewrite.EZHex` | #45 | 58,206 B config | 9 | 18 | 2.5.0 |

It is what the page read off their 520 before its first write: the page's own
backup, so the XML header is the page's wrapper (`ezhex_from_blob`), not a
Concordance dump. Firmware 2.6, hardware 0x25, flash 0xFF:0x12. Four devices on
the first Devices page and no activities; every device has more than four soft
keys, so its own screens are paged, up to eleven pages.

It runs the same firmware as the 525 and round trips byte for byte. What
differs: the page counter ("1 OF 2" under a paged screen) is drawn in **font
set 2**, the one set eight rows high, where the 525, 515 and 555 draw it in set
4. Set 4 on the 520 is eleven rows high. The digits 1 to 6 and the O are the
same pixels as on the 525. Before this file the composer put a fifth device's
counter in set 4 and refused its own result.

`tools/verify_500_series.py` reads it and adds a fifth device to it.
