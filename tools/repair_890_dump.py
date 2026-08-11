#!/usr/bin/env python3
"""Remove the duplicated 54-byte blocks a Harmony 890 read can leave behind.

Two dumps of one 890 in issue #28 both failed their own header contract, and
comparing them showed why: at every point where they disagree, they resynchronise
after an exact multiple of 54 bytes, and the extra block is always a copy of the
54 bytes in front of it. The reader delivers the same chunk twice. See
`docs/FORMAT.md` section 5k.

The repair is to drop those copies, and the point of this tool is that it will
not guess. A dump is only repaired if the result satisfies two things it was not
fitted to:

* `DKDK` lands at the address the file's own header states, and
* the trailer checksum recomputes to the value already stored in the file.

Both are 16 bits or better of agreement, so a wrong removal fails them. If no
removal satisfies both, the tool says so and writes nothing: a dump can be broken
in some other way, and a plausible-looking blob is worse than an error.

Usage:
    repair_890_dump.py <dump.EZHex> [-o repaired.EZHex]
    repair_890_dump.py <dump.EZHex> --reference <good-dump.EZHex>

Without `--reference` the duplicates are searched for directly, which works when
there are only a few. With a second dump of the same remote it is much stronger:
the two are aligned against each other and only the blocks one has and the other
does not are candidates, so sixteen duplicates are as easy as two.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import hconfig

BLOCK = 54
END_MARKER = b"DKDK"


def blob_of(path: Path) -> tuple[bytes, bytes, bytes]:
    xml, separator, blob = hconfig.split_container(path.read_bytes())
    return xml, separator, blob


def stated_end(blob: bytes, base: int) -> int:
    """Where the header says the end marker is, as an offset into the blob."""
    return hconfig._u32(blob, 4) - base


def verified(blob: bytes, base: int) -> bytes | None:
    """The blob trimmed to its marker, if it agrees with its own header and sum."""
    marker = blob.find(END_MARKER)
    if marker < 0 or marker != stated_end(blob, base):
        return None
    trimmed = blob[:marker + 4]
    stored = trimmed[marker - 2] | (trimmed[marker - 1] << 8)
    if stored != hconfig.trailer_checksum(trimmed):
        return None
    return trimmed


def duplicate_positions(blob: bytes, limit: int) -> list[int]:
    """Every place a 54-byte block repeats the block in front of it.

    Self-similar data produces hundreds of these, which is why the caller has to
    choose between them rather than remove them all.
    """
    out, at = [], BLOCK
    while at + BLOCK <= limit:
        if blob[at - BLOCK:at] == blob[at:at + BLOCK]:
            out.append(at)
            at += BLOCK
        else:
            at += 1
    return out


def repair_against(blob: bytes, reference: bytes) -> tuple[bytes, list[int]]:
    """Drop the blocks `blob` has and `reference` does not, walking both at once."""
    out, drops = bytearray(), []
    i = j = 0
    while j < len(reference) and i < len(blob):
        if blob[i] == reference[j]:
            out.append(blob[i])
            i += 1
            j += 1
        elif blob[i:i + BLOCK] == blob[i - BLOCK:i]:
            drops.append(i)
            i += BLOCK
        else:
            raise ValueError(f"the two dumps stop agreeing at 0x{i:06X}, and not "
                             f"over a duplicated block")
    return bytes(out), drops


def repair_alone(blob: bytes, base: int) -> tuple[bytes, list[int]]:
    """Search for the duplicates directly. Only tractable while there are few."""
    extra = blob.find(END_MARKER) - stated_end(blob, base)
    if extra <= 0 or extra % BLOCK:
        raise ValueError(f"the marker is {extra} bytes out of place, which is not "
                         f"a whole number of {BLOCK}-byte blocks")
    wanted = extra // BLOCK
    candidates = duplicate_positions(blob, stated_end(blob, base))
    if len(candidates) < wanted:
        raise ValueError("fewer duplicated blocks than the file has bytes too many")

    # Try the smallest combinations first. Anything past a handful of duplicates
    # needs a second dump; that is what --reference is for.
    from itertools import combinations
    if len(candidates) > 400 and wanted > 2:
        raise ValueError(f"{wanted} blocks to find among {len(candidates)} candidates; "
                         f"pass a second dump of the same remote with --reference")
    for chosen in combinations(candidates, wanted):
        out = bytearray()
        previous = 0
        for position in chosen:
            out += blob[previous:position]
            previous = position + BLOCK
        out += blob[previous:]
        if verified(bytes(out), base) is not None:
            return bytes(out), list(chosen)
    raise ValueError("no combination of duplicated blocks repairs this dump")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--reference", type=Path,
                        help="another dump of the same remote, healthy or not")
    parser.add_argument("--base", type=lambda v: int(v, 0), default=0x30000,
                        help="config base address, 0x30000 on protocol 10")
    args = parser.parse_args()

    xml, separator, blob = blob_of(args.dump)
    already = verified(blob, args.base)
    if already is not None:
        print(f"{args.dump.name}: already agrees with its header and checksum, "
              f"nothing to repair")
        return 0

    marker = blob.find(END_MARKER)
    print(f"{args.dump.name}: marker at 0x{marker:06X}, header says "
          f"0x{stated_end(blob, args.base):06X}, "
          f"{marker - stated_end(blob, args.base)} bytes too many")

    if args.reference:
        _, _, other = blob_of(args.reference)
        reference = verified(other, args.base)
        if reference is None:
            # The reference is broken too. Repair it first, on its own, then use it.
            reference, _ = repair_alone(other, args.base)
            reference = verified(reference, args.base)
            if reference is None:
                print("the reference dump could not be repaired either", file=sys.stderr)
                return 1
        repaired, drops = repair_against(blob, reference)
    else:
        repaired, drops = repair_alone(blob, args.base)

    trimmed = verified(repaired, args.base)
    if trimmed is None:
        print("the removal did not reproduce the stated end address and checksum",
              file=sys.stderr)
        return 1

    print(f"removed {len(drops)} duplicated {BLOCK}-byte blocks at "
          + ", ".join(f"0x{d:06X}" for d in drops))
    print(f"marker now at 0x{trimmed.find(END_MARKER):06X}, trailer checksum "
          f"0x{hconfig.trailer_checksum(trimmed):04X}, as stored")
    print(f"{len(trimmed)} bytes, sha256 {hashlib.sha256(trimmed).hexdigest()}")

    if args.output:
        header = hconfig._set_tag(xml, "BINARYDATASIZE", len(trimmed))
        checksum = 0x69
        for byte in trimmed:
            checksum ^= byte
        header = hconfig._set_tag(header, "CHECKSUM", checksum)
        args.output.write_bytes(header + separator + trimmed)
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
