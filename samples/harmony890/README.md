# Harmony 890 (protocol 10)

Two configs uploaded by **[@kkong42](https://github.com/kkong42)** on 2026-08-10
in [issue #27](https://github.com/trelowney/harmony-decompiler/issues/27) and
[issue #28](https://github.com/trelowney/harmony-decompiler/issues/28), with the
"happy to publish" box ticked. Mirrored unmodified. If he would rather they were
not here, they come straight back out.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `H890-Bedroom-1.EZHex` | #27 | 396,927 B | 10 | 19 | 0.1.0 |
| `H890-Bedroom-2.EZHex` | #28 | 397,737 B | 10 | 19 | 0.1.0 |

**The decompiler does not read these.** `roundtrip.py` skips this directory and
that is deliberate: nothing here supports protocol 10.

What is known about the container is in
[FORMAT.md §5j](../../docs/FORMAT.md). Briefly: the magic is `TPTP` and the end
marker is `DKDK`, both the same as arch 8, but the first byte of the file sits at
address `0x30000` instead of `0x20000`, and 702 bytes of zero padding follow the
end marker. With those two allowances the header, the marker and the trailer
checksum all agree.

Two other things worth knowing before anybody spends an evening here:

- **`H890-Bedroom-2.EZHex` does not verify.** Its header says the config ends at
  `0x90BBD`, its `DKDK` marker sits at `0x90F1D`, and the `u16` in front of that
  marker does not recompute. Its sibling is exactly self-consistent under the
  same reading, so this is either a damaged dump or a hole in the reading. A
  second read of that remote has been asked for in #28.
- **Concordance will not dump 890 firmware**, which the uploader tried. So the
  route that proved most of the arch 9 findings, finding the routine that reads
  a structure, is not available for this model yet.
