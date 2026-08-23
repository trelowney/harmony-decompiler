"""Count the devices in a config, without being told which architecture it is.

Two structures carry the answer, and they are in different halves of the file:

    section 5    its first byte is the device count, followed by one u24 per
                 device. Confirmed against ground truth on arch 9 (the 525
                 sample, 4) and on arch 14 (a 650 and a 600 at 4, and eight
                 Harmony 700 configs from the same account's history that run
                 4, 5 and 6). Reported for arch 8 as well, where nothing here
                 has checked it against a known setup.

    the state    on arch 12 and 14 the `0xFEED ... 0xBEEF` tree of section 5c
    tree         holds four variables per device whose names end in that
                 device's id, so the distinct ids are the devices. Arch 8 and
                 arch 9 configs do not carry those variables, and there the
                 tree says nothing; that is not a failure, it is a difference
                 between architectures.

Where both speak, they are an independent check on each other: one is a name
table, the other a pointer array.

Why anyone wants this: how a config says "five devices rather than four" is the
open question behind docs/FORMAT.md section 5o, where a generated fifth device
was written to a real remote and never appeared on its screen. Counting devices
in someone else's upload is the cheapest way to find a sample that settles it.

Usage:
    python tools/count_devices.py                       # the bundled samples
    python tools/count_devices.py path/to/config.EZHex ...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import _paths
from _paths import get_blob

DEVICE_VARIABLES = (
    "PowerOnDelay",
    "InterDeviceDelay",
    "DefaultPowerOnDelay",
    "DefaultInterDeviceDelay",
)
PER_DEVICE = re.compile(r"^(?:%s)_(\d+)_" % "|".join(DEVICE_VARIABLES))
TYPED = re.compile(r"^([A-Za-z]+)_(?:Power|Input)_\d+$")

TREE_START = b"\xed\xfe"
TREE_END = b"\xef\xbe"
NODE = 0xA7


def u16(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 2], "little")


def u32(blob: bytes, offset: int) -> int:
    return int.from_bytes(blob[offset:offset + 4], "little")


def base_address(blob: bytes) -> int:
    """Where the first byte of the blob lives, per section 5j.

    The header states the address of its own end, so the base is that minus the
    length. Gives 0x20000 on arch 8/9/12 and 0x30000 on arch 14 without being
    told which is which - and gives something else entirely on a damaged dump,
    which is why the caller checks it.
    """
    return u32(blob, 4) - (len(blob) - 4)


def state_tree_names(blob: bytes) -> list[str]:
    """Every node name in the state tree, in file order.

    The node layout is the same on every architecture seen here: tag 0xA7, a
    u16 length, a u16 parent id, a u16 own id, then the name.
    """
    start = blob.find(TREE_START)
    if start < 0:
        return []
    end = blob.find(TREE_END, start)
    if end < 0:
        end = len(blob)
    names, at = [], start + 5
    while at < end and blob[at] == NODE:
        length = u16(blob, at + 1)
        names.append(blob[at + 7:at + 3 + length].decode("latin-1"))
        at += 3 + length
    return names


def devices_from_tree(blob: bytes) -> tuple[set[str], list[str]]:
    """(device ids, device type names) as far as the state tree gives them."""
    ids, types = set(), set()
    for name in state_tree_names(blob):
        found = PER_DEVICE.match(name)
        if found:
            ids.add(found.group(1))
        typed = TYPED.match(name)
        if typed:
            types.add(typed.group(1))
    return ids, sorted(types)


def devices_from_section_5(blob: bytes) -> int | None:
    """The count byte at the head of section 5, or None if it is unreachable."""
    section = u32(blob, 12 + 5 * 4) - base_address(blob)
    if not 0 <= section < len(blob):
        return None
    return blob[section]


def report(path: Path) -> str:
    """Print one config's counts. Returns 'ok', 'suspect' or 'disagree'."""
    blob = get_blob(path)
    base = base_address(blob)
    ids, types = devices_from_tree(blob)
    print(f"{path.name}")
    print(f"    cookie {blob[:4].decode('latin-1')}   base 0x{base:06X}"
          f"   {len(blob)} bytes")

    if base <= 0 or base % 0x10000:
        print("    base is not a whole number of 64 KiB pages, so the header does")
        print("    not agree with the file length. Nothing below can be trusted;")
        print("    if this is a Harmony 890 read, run repair_890_dump.py first.")
        return "suspect"

    section = devices_from_section_5(blob)
    tree = f"{len(ids)}" if ids else "no per-device variables"
    print(f"    devices: section 5 says {'-' if section is None else section}"
          f"; state tree says {tree}")
    if types:
        print(f"    types named in the state tree: {', '.join(types)}")
    if ids and section is not None and section != len(ids):
        print("    THE TWO DISAGREE.")
        return "disagree"
    return "ok"


def bundled() -> list[Path]:
    paths = [_paths.SAMPLE_EZHEX]
    for directory in _paths.UNREAD_DIRS:
        paths += sorted(directory.glob("*.EZHex"))
    paths += _paths.arch8_samples()
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("configs", nargs="*", type=Path)
    args = parser.parse_args()

    bad = 0
    for path in args.configs or bundled():
        if report(path) == "disagree":
            bad += 1
    if bad:
        print(f"\n{bad} config(s) where the two counts disagree.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
