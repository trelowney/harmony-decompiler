# Harmony 650 (protocol 14)

One config uploaded by **[@psolyca](https://github.com/psolyca)** on 2026-08-03
in [issue #8](https://github.com/trelowney/harmony-decompiler/issues/8), with the
"happy to publish" box ticked. Mirrored unmodified. If he would rather it were
not here, it comes straight back out.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `Harmony_650.EZHex` | #8 | 845,133 B | 14 | 72 | 1.2.0 |

He notes the remote was configured with concordance a long time ago, and offered
a firmware dump as well. Firmware is not mirrored in this repository, by policy.

**The decompiler does not read this.** `roundtrip.py` skips this directory.
Protocol 14 is a third architecture and nothing here supports it.

The container is described in [FORMAT.md §5j](../../docs/FORMAT.md). Briefly:
the magic is `GSPM`, which is neither arch 8's `TPTP` nor arch 9's `AHCM`, the
end marker is `PTYY`, and the first byte of the file sits at address `0x30000`
rather than `0x20000`. With that base the header's end address lands exactly on
the marker, and the trailer checksum from
[FORMAT.md §4m](../../docs/FORMAT.md) reproduces the stored `0x4045`. So the
envelope is the same design as the other two, and only the labels differ.

If you want this file read properly today rather than eventually,
[harmony-explorations](https://github.com/dannybloe/harmony-explorations) already
handles protocol 14.
