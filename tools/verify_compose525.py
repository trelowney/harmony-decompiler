"""Check compose525 on the public 525 sample, offline.

Adds devices one after another and requires: the first goes top left on a new
Devices page 2 shared by every Devices screen, the next ones fill the other
corners in reading order, the fifth opens page 3, every Devices page carries
"n OF m", and each step passes add_device's own checks (records decode as
asked, keys and soft keys send them, nothing else changed, the pool reader
accounts for every page, the file round-trips).

    python verify_compose525.py [--out DIR]   DIR receives the composed files
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _paths
import compose525 as c
import hconfig
import render_525_screens as renderer
import verify_525_semantics as sem
from clone_525_device import mode_fields


def device(n: int):
    address = 0x40 + n
    names = ["Up", "Down", "Left", "Right", "OK", "Exit", "Power"]
    commands = [c.nec_command(k, address, 0x10 + i, repeat=k in ("Up", "Down", "Left", "Right"))
                for i, k in enumerate(names)]
    keys = {k: k for k in names if k in c.KEYS}
    lcd = [(("Power", "Power"), ("Ok", "OK"), None, ("Exit", "Exit"))]
    return commands, keys, lcd


def rekey_x96(raw: bytes) -> bytes:
    """The X96 Box re-keyed the way the owner's remote has it since 2026-10-18
    (FORMAT 5af): its own records where they are right, three new ones on the
    address the box's handset sends, one soft-key page."""
    x96 = [d for d in c.devices(raw) if d.name == "X96 Box"]
    if len(x96) != 1:
        raise SystemExit("FAIL no X96 Box on the Devices screen")
    records = {"0": 4, "1": 21, "2": 58, "3": 31, "4": 25, "5": 42, "6": 18, "7": 26,
               "8": 20, "9": 59, "BACK": 30, "DOWN": 43, "LEFT": 62, "RIGHT": 15, "UP": 46,
               "FORWARD": 14, "HOME": 44, "OK": 6, "PLAY/PAUSE": 0, "POWER": 50,
               "REWIND": 27, "SETTINGS": 13, "VOLUME +": 33, "VOLUME -": 38}
    commands = dict(records)
    commands.update({n: c.nec_command(n, 0x01, code) for n, code in
                     (("MENU", 0x4C), ("AT", 0x47), ("BACKSPACE", 0x42))})
    keys = {"Up": "UP", "Down": "DOWN", "Left": "LEFT", "Right": "RIGHT", "OK": "OK",
            "Exit": "BACK", "Menu": "MENU", "VolumeUp": "VOLUME +", "VolumeDown": "VOLUME -",
            "Play": "PLAY/PAUSE", "Pause": "PLAY/PAUSE", "FastForward": "FORWARD",
            "Rewind": "REWIND", "Guide": "HOME", "Info": "SETTINGS", "Star": "BACKSPACE",
            "Hash": "AT", **{str(n): str(n) for n in range(10)},
            **{k: None for k in ("ChannelUp", "ChannelDown", "Stop", "Record", "Prev",
                                 "Red", "Green", "Yellow", "Blue", "Mute",
                                 "SkipForward", "SkipBack")}}
    lcd = [(("Power", "POWER"), ("Home", "HOME"), ("Settings", "SETTINGS"), ("Bksp", "BACKSPACE"))]
    return c.rekey_device(raw, x96[0].mode, commands, keys, lcd)


def counter_text(blob, root) -> str:
    out = []
    for item in sem.screen_program_path(blob, root):
        if item["opcode"] in (4, 5) and item["operands"][1] == 56:
            out.append(item["operands"][2] if item["opcode"] == 5 else None)
    return " ".join("?" if x is None else {v: str(k) for k, v in c.COUNTER_DIGIT.items()}.get(x, "OF")
                    for x in out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    raw = Path(_paths.SAMPLE_CONTAINER if hasattr(_paths, "SAMPLE_CONTAINER")
               else Path(__file__).resolve().parents[1] / "samples" / "harmony525" / "config.EZHex").read_bytes()
    expected = [(2, 0), (2, 1), (2, 2), (2, 3), (3, 0)]
    for n, (page, corner) in enumerate(expected):
        commands, keys, lcd = device(n)
        raw, placed = c.add_device(raw, f"Box {'ABCDE'[n]}", commands, keys, lcd)
        got = (placed.devices_page + 1, placed.corner)
        print(f"  Box {'ABCDE'[n]}: group {placed.group}, mode {placed.mode}, "
              f"Devices page {got[0]}, corner {got[1]}")
        if got != (page, corner):
            print(f"FAIL expected page {page} corner {corner}")
            return 1
        if args.out:
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / f"compose-{n + 1}.EZHex").write_bytes(raw)
    blob = hconfig.split_container(raw)[2]
    S = sem.section_offsets(blob)
    d = c._Doc(raw)
    for m in d.devices_modes():
        texts = [counter_text(blob, p["program"]) for p in mode_fields(blob, S, m)["pages"]]
        if texts != ["1 OF 3", "2 OF 3", "3 OF 3"]:
            print(f"FAIL Devices mode {m} counters {texts}")
            return 1
    print("PASS five devices composed onto the public sample, pages and counters as expected")

    # delete from the middle, so groups, modes and action lists renumber
    names = {x.name: x for x in c.devices(raw)}
    raw = c.delete_device(raw, names["Box B"].mode)
    left = sorted((x.name, x.page, x.corner) for x in c.devices(raw))
    want = sorted((n, x.page, x.corner) for n, x in names.items() if n != "Box B")
    if left != want:
        print(f"FAIL after deleting Box B: {left}")
        return 1
    print("PASS Box B deleted from the middle: everything after it renumbered, its corner empty")
    # the last page's only device: the page goes and the counters say OF 2
    raw = c.delete_device(raw, {x.name: x for x in c.devices(raw)}["Box E"].mode)
    blob = hconfig.split_container(raw)[2]
    S = sem.section_offsets(blob)
    for m in c._Doc(raw).devices_modes():
        texts = [counter_text(blob, p["program"]) for p in mode_fields(blob, S, m)["pages"]]
        if texts != ["1 OF 2", "2 OF 2"]:
            print(f"FAIL Devices mode {m} counters {texts} after deleting Box E")
            return 1
    if args.out:
        (args.out / "deleted-b-and-e.EZHex").write_bytes(raw)
    print("PASS Box E deleted with its page, every Devices screen reads OF 2")

    sample = Path(Path(__file__).resolve().parents[1] / "samples" / "harmony525" / "config.EZHex").read_bytes()
    rekeyed = rekey_x96(sample)
    x96 = [d for d in c.devices(rekeyed) if d.name == "X96 Box"][0]
    blob = hconfig.split_container(rekeyed)[2]
    pages = mode_fields(blob, sem.section_offsets(blob), x96.mode)["pages"]
    if len(pages) != 1:
        print(f"FAIL X96 has {len(pages)} pages")
        return 1
    if args.out:
        (args.out / "rekey-x96.EZHex").write_bytes(rekeyed)
    print("PASS the X96 re-keyed as on the owner's remote, one page, every key checked")

    # activities: the first takes the menu's free corner, the second opens page 2
    power = c.power_variables(sample)
    if sorted(power.values()) != [16, 17, 19]:
        print(f"FAIL power variables {power}")
        return 1
    tv = next(n for n, v in power.items() if v == 17)
    amp = next(n for n, v in power.items() if v == 16)
    xbox = next(n for n, v in power.items() if v == 19)
    watch_tv = next(a.name for a in c.activities(sample) if a.value == 0)
    xbox_activity = next(a.name for a in c.activities(sample) if a.value == 2)
    raw = c.add_activity(sample, "Test", like=watch_tv, on=[tv, amp], writes={20: 5})
    raw = c.add_activity(raw, "Games", like=xbox_activity, on=[tv, xbox], writes={20: 6})
    got = {a.name: (a.value, a.key_set, a.page, a.corner) for a in c.activities(raw)}
    if got.get("Test") != (4, 8, 0, 3) or got.get("Games") != (5, 9, 1, 0):
        print(f"FAIL activities {got}")
        return 1
    blob = hconfig.split_container(raw)[2]
    S = sem.section_offsets(blob)
    menu = c._menu_mode(c._Doc(raw))
    texts = [counter_text(blob, p["program"]) for p in mode_fields(blob, S, menu)["pages"]]
    if texts != ["1 OF 2", "2 OF 2"]:
        print(f"FAIL menu counters {texts}")
        return 1
    if args.out:
        (args.out / "two-activities.EZHex").write_bytes(raw)
    print("PASS two activities: Test in the menu's free corner, Games on a new page 2")

    # delete them again, the first from before the second, so values and key
    # sets renumber; the result must read as the original sample did
    raw = c.delete_activity(raw, "Test")
    got = {a.name: (a.value, a.key_set) for a in c.activities(raw)}
    if got.get("Games") != (4, 8) or "Test" in got:
        print(f"FAIL after deleting Test: {got}")
        return 1
    raw = c.delete_activity(raw, "Games")
    if args.out:
        (args.out / "activities-deleted.EZHex").write_bytes(raw)
    blob = hconfig.split_container(raw)[2]
    sblob = hconfig.split_container(sample)[2]
    if c.activities(raw) != c.activities(sample) or c.devices(raw) != c.devices(sample):
        print("FAIL the menu or the device list differs from the original sample")
        return 1
    from clone_525_device import render_map
    r0, r1 = render_map(sblob), render_map(blob)
    if {k: v[0] for k, v in r0.items()} != {k: v[0] for k, v in r1.items()}:
        print("FAIL a screen differs from the original sample")
        return 1
    d0, d1 = c._Doc(sample), c._Doc(raw)
    if [d0.ins(i) for i in range(d0.n_actions)] != [d1.ins(i) for i in range(d1.n_actions)]:
        print("FAIL the action lists differ from the original sample")
        return 1
    print("PASS both deleted again: every action list, screen, activity and device as in the sample")
    return 0


if __name__ == "__main__":
    sys.exit(main())
