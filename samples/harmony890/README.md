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

  **It is a damaged dump, and the damage is now measured.** The header and the
  section table are unchanged from its sibling, but the end marker has moved,
  so a base recovered from the marker puts every section **864 bytes late**:
  slot 0 then reads `a2 5d 03 b9 5e 03 72` instead of the architecture record,
  and not one of the thirteen content-checkable slots survives. The file's own
  declared address and table are the intact half.

  **And the cause has a name.** [@dannybloe](https://github.com/dannybloe)'s
  `harmony-explorations`, findings section 122, records read corruption as a
  known behaviour of architecture 10 rather than a fault in one remote, and the
  handling is to read the same remote several times and take the consensus.
  [@kkong42](https://github.com/kkong42) did exactly that for his Harmony 895
  in [issue #34](https://github.com/trelowney/harmony-decompiler/issues/34):
  five reads, of which 2, 4 and 5 are byte-identical and 1 and 3 are not. So
  the request in #28 is better put as **three to five reads of the 890, not
  one**, and nothing needs fixing on that remote.
- **Concordance will not dump 890 firmware**, which the uploader tried. So the
  route that proved most of the arch 9 findings, finding the routine that reads
  a structure, is not available for this model yet.
