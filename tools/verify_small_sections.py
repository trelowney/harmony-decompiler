"""Check the small sections this decompiler used to pass through as raw bytes.

Rooted coverage of the public 525 left 180 bytes that no reader claimed. They
are not one thing: some were already described in FORMAT.md and simply never
read, and some had no description at all. This verifies both kinds against
every public sample in `samples/`, so a claim about them can fail.

Where a check is a real oracle it is marked so in the output. Two fields of the
same record agreeing through an outside rule - a weekday against a calendar, a
record count against a span - can fail. A grammar that merely reads back its own
declared count cannot, and is labelled DESCRIBES rather than PASS.

Usage:
    python tools/verify_small_sections.py
    python tools/verify_small_sections.py --sample samples/harmony525/config.EZHex
"""
from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

import _paths  # noqa: F401
import hconfig

DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
BUILD_HEAD = b"\xdf\xad"
BUILD_TAIL = b"\xbf\xef"


class CheckError(Exception):
    pass


def sections(blob: bytes):
    """Section spans as blob offsets, plus the base the container implies."""
    end_addr = int.from_bytes(blob[4:8], "little")
    base = end_addr - (len(blob) - 4)
    starts = [int.from_bytes(blob[0x0C + 4 * i:0x10 + 4 * i], "little") - base
              for i in range(18)]
    return starts + [len(blob)], base


def check_section_1(blob, span, xml):
    """`<u8 protocol> <u8 protocol> <u8 skin> <u8 0x0D> <u24 0>`.

    The oracle is outside the blob: the .EZHex XML states PROTOCOL and SKIN in
    text, and section 1 has to agree with both.
    """
    a, z = span
    s = blob[a:z]
    if len(s) != 7:
        raise CheckError(f"section 1 is {len(s)} bytes, expected 7")
    proto = int(re.search(rb"<PROTOCOL>(\d+)</PROTOCOL>", xml).group(1))
    skin = int(re.search(rb"<SKIN>(\d+)</SKIN>", xml).group(1))
    if s[0] != proto or s[1] != proto:
        raise CheckError(f"bytes 0,1 = {s[0]},{s[1]}; XML says PROTOCOL {proto}")
    if s[2] != skin:
        raise CheckError(f"byte 2 = {s[2]}; XML says SKIN {skin}")
    if s[3] != 0x0D or s[4:] != b"\0\0\0":
        raise CheckError(f"tail is {s[3:].hex()}, expected 0d000000")
    return {"protocol": proto, "skin": skin}


def check_section_2(blob, span):
    """`<u16 count> <u24 first> <u24 past-the-end>` - the remote's own flash.

    Three fields, one relation: the span the two addresses bound has to be the
    record count times eight. Nothing here reads a stored record size.
    """
    a, z = span
    s = blob[a:z]
    if len(s) != 8:
        raise CheckError(f"section 2 is {len(s)} bytes, expected 8")
    count = int.from_bytes(s[0:2], "little")
    lo = int.from_bytes(s[2:5], "little")
    hi = int.from_bytes(s[5:8], "little")
    if hi <= lo:
        raise CheckError(f"flash range 0x{lo:06X}..0x{hi:06X} does not ascend")
    if (hi - lo) != count * 8:
        raise CheckError(f"{count} records span 0x{hi - lo:X} bytes, "
                         f"which is {(hi - lo) / count:.3f} bytes each, not 8")
    return {"records": count, "flash": f"0x{lo:06X}..0x{hi:06X}", "record_bytes": 8}


def check_section_3(blob, span):
    """The build time, in the eleven-byte framed record @dannybloe published.

    `0xADDF`, second, minute, hour, day, weekday, month from zero, year from
    2000, `0xEFBF`. The oracle is the calendar: the stored weekday has to be
    the weekday the stored date actually falls on, and those are separate
    fields that a wrong field order breaks.
    """
    a, z = span
    s = blob[a:z]
    if len(s) != 14:
        raise CheckError(f"section 3 is {len(s)} bytes, expected 14")
    if s[0:2] != BUILD_HEAD or s[9:11] != BUILD_TAIL:
        raise CheckError(f"frame is {s[0:2].hex()}..{s[9:11].hex()}, "
                         f"expected {BUILD_HEAD.hex()}..{BUILD_TAIL.hex()}")
    if s[11:] != b"\0\0\0":
        raise CheckError(f"trailing bytes are {s[11:].hex()}, expected 000000")
    sec, minute, hour, day, weekday, month, year = s[2:9]
    try:
        when = datetime.datetime(2000 + year, month + 1, day, hour, minute, sec)
    except ValueError as exc:
        raise CheckError(f"not a calendar date: {exc}") from exc
    stored = DOW[(weekday - 1) % 7]
    actual = DOW[(when.weekday() + 1) % 7]
    if stored != actual:
        raise CheckError(f"stored weekday {weekday} is {stored}, but "
                         f"{when:%Y-%m-%d} is a {actual}")
    return {"built": f"{when:%Y-%m-%d %H:%M:%S}", "weekday": actual}


def check_section_4(blob, span):
    """`<u8 first> <u16 0> <u16 count>` then count x `<u8 k> <u8 first+k> <u16 0>`.

    This one DESCRIBES. It reads back its own declared count, so it cannot fail
    the way the others can - and the records carry no information beyond the
    two numbers in the header, because the second column is always the first
    plus the index. What the numbering counts is not known.
    """
    a, z = span
    s = blob[a:z]
    first = s[0]
    pad = int.from_bytes(s[1:3], "little")
    count = int.from_bytes(s[3:5], "little")
    used = 5 + 4 * count
    if pad:
        raise CheckError(f"bytes 1,2 are 0x{pad:04X}, expected 0")
    if used > len(s):
        raise CheckError(f"{count} records need {used} bytes, span is {len(s)}")
    for k in range(count):
        rec = s[5 + 4 * k:9 + 4 * k]
        if rec[0] != k:
            raise CheckError(f"record {k} indexes itself as {rec[0]}")
        if rec[1] != first + k:
            raise CheckError(f"record {k} values {rec[1]}, not {first + k}")
        if rec[2:] != b"\0\0":
            raise CheckError(f"record {k} pads with {rec[2:].hex()}")
    return {"count": count, "values": f"{first}..{first + count - 1}",
            "own_bytes": used, "rest_of_span": len(s) - used}


def check_section_16(blob, span, base):
    """`<u8 count> <u24 address>[count]`, filling the span exactly.

    An empty one is a single zero byte, which is what arch 9 and arch 14 hold.
    """
    a, z = span
    s = blob[a:z]
    count = s[0]
    if len(s) != 1 + 3 * count:
        raise CheckError(f"{count} addresses need {1 + 3 * count} bytes, "
                         f"span is {len(s)}")
    for k in range(count):
        addr = int.from_bytes(s[1 + 3 * k:4 + 3 * k], "little") - base
        if not 0 <= addr < len(blob):
            raise CheckError(f"address {k} lands outside the config")
    return {"count": count, "exact_fit": True}


def check_section_15_tiles_14(blob, spans, base):
    """Section 15's targets, and whether they tile the tail of section 14.

    Section 15 is a pointer table whose targets sit inside section 14's span -
    the plainest case in the file of a span that is not a fence. Each target
    reads as `<u8 count> <u16 value>[count]`, and the check is that consecutive
    targets abut with no gap and no overlap and the last one ends exactly where
    section 15 begins. The boundaries come from the section table and the
    lengths come from the data, so the two can disagree.
    """
    s15 = blob[spans[15]:spans[16]]
    count = s15[0]
    if len(s15) != 1 + 3 * count:
        return None, {"shape": f"{len(s15)} bytes, not 1+3*{count}"}
    targets = [int.from_bytes(s15[1 + 3 * k:4 + 3 * k], "little") - base
               for k in range(count)]
    if not all(spans[14] <= t < spans[15] for t in targets):
        return None, {"targets": "not all inside section 14's span"}
    lists = []
    for i, start in enumerate(targets):
        n = blob[start]
        length = 1 + 2 * n
        values = [int.from_bytes(blob[start + 1 + 2 * j:start + 3 + 2 * j], "little")
                  for j in range(n)]
        nxt = targets[i + 1] if i + 1 < len(targets) else spans[15]
        if start + length != nxt:
            raise CheckError(f"list at 0x{start:05X} is {length} bytes and the "
                             f"next object starts {nxt - start} bytes on")
        lists.append(values)
    return {"lists": len(lists), "values": sum(len(v) for v in lists),
            "tiles_to": f"0x{spans[15]:05X}"}, None


def check_section_17_head(blob, spans):
    """Section 17's span opens with two zero bytes before its first object.

    This DESCRIBES. Every sample here has them, and they read as an empty count
    under either width, but no sample has a non-zero one, so the width is not
    established and neither is the meaning.
    """
    head = blob[spans[17]:spans[17] + 2]
    if head != b"\0\0":
        raise CheckError(f"section 17 opens with {head.hex()}, not 0000")
    return {"head": "00 00"}


def audit(path: Path):
    raw = path.read_bytes()
    xml, _sep, blob = hconfig.split_container(raw)
    spans, base = sections(blob)
    if base & 0xFFFF:
        return None, (f"base 0x{base:X} is not a whole number of 64 kB pages; "
                      f"this container does not lay its sections out the same "
                      f"way - see FORMAT.md 5j")
    out = {}
    out["section 1"] = ("PASS", check_section_1(blob, (spans[1], spans[2]), xml))
    out["section 2"] = ("PASS", check_section_2(blob, (spans[2], spans[3])))
    out["section 3"] = ("PASS", check_section_3(blob, (spans[3], spans[4])))
    out["section 4"] = ("DESCRIBES", check_section_4(blob, (spans[4], spans[5])))
    out["section 16"] = ("PASS", check_section_16(blob, (spans[16], spans[17]), base))
    tiled, why = check_section_15_tiles_14(blob, spans, base)
    out["section 15/14"] = ("PASS", tiled) if tiled else ("N/A", why)
    out["section 17"] = ("DESCRIBES", check_section_17_head(blob, spans))
    return out, None


def negative(path: Path) -> int:
    """Break each checked field in turn and demand the check notices.

    A check that passes on every sample has shown nothing until it has also
    been shown to fail. Each mutation below changes one field the reader claims
    to understand; any that still passes marks a claim the check is not really
    making.
    """
    raw = bytearray(path.read_bytes())
    xml, sep, blob = hconfig.split_container(bytes(raw))
    head = len(raw) - len(blob)
    spans, _base = sections(blob)

    def at(section, offset):
        return head + spans[section] + offset

    mutations = [
        ("section 1 skin", at(1, 2), 1),
        ("section 1 constant 0x0D", at(1, 3), 1),
        ("section 2 record count", at(2, 0), 1),
        ("section 2 flash end", at(2, 7), 1),
        ("section 3 weekday", at(3, 6), 1),
        ("section 3 month", at(3, 7), 1),
        ("section 3 frame", at(3, 0), 1),
        ("section 4 first value", at(4, 0), 1),
        ("section 4 a record value", at(4, 9), 1),
        ("section 4 a record index", at(4, 8), 1),
        ("section 16 count", at(16, 0), 1),
        ("section 17 head", at(17, 0), 1),
    ]
    s15count = blob[spans[15]]
    if len(blob[spans[15]:spans[16]]) == 1 + 3 * s15count and s15count:
        mutations.append(("section 15 a target address", at(15, 1), 1))
        mutations.append(("section 14 a list count", head + spans[14] + 34, 1))

    missed = 0
    for name, offset, delta in mutations:
        if offset >= len(raw):
            continue
        spare = bytearray(raw)
        spare[offset] = (spare[offset] + delta) & 0xFF
        tmp = path.parent / f".negative-{path.name}"
        tmp.write_bytes(bytes(spare))
        try:
            _result, skip = audit(tmp)
            caught = bool(skip)
        except CheckError:
            caught = True
        finally:
            tmp.unlink()
        print(f"  {'caught' if caught else 'MISSED':7} {name} at 0x{offset:05X}")
        missed += not caught
    print(f"\n{len(mutations)} mutations, {missed} not caught")
    return 1 if missed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sample", type=Path, action="append",
                        help="check one container instead of every public sample")
    parser.add_argument("--negative", action="store_true",
                        help="break each checked field and demand a failure")
    args = parser.parse_args()
    if args.negative:
        root = Path(__file__).resolve().parent.parent
        target = (args.sample or [root / "samples" / "harmony525" / "config.EZHex"])[0]
        print(f"negative check against {target.name}")
        return negative(target)
    root = Path(__file__).resolve().parent.parent
    paths = args.sample or sorted((root / "samples").glob("*/*.EZHex"))
    failures = 0
    checked = 0
    for path in paths:
        try:
            result, skip = audit(path)
        except CheckError as exc:
            print(f"FAIL {path.name}: {exc}")
            failures += 1
            continue
        if skip:
            print(f"SKIP {path.name}: {skip}")
            continue
        checked += 1
        print(f"{path.name}")
        for name, (verdict, detail) in result.items():
            body = ", ".join(f"{k}={v}" for k, v in detail.items())
            print(f"  {verdict:9} {name:14} {body}")
    print(f"\n{checked} containers checked, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
