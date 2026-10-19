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

    # on the config the owner's remote runs: a device added and deleted again is
    # the same file, byte for byte (a record header's pointer to itself once
    # kept its records alive after the device was gone)
    composed = (Path(__file__).resolve().parents[1] / "samples" / "harmony525" /
                "composed-2026-10-18.EZHex").read_bytes()
    commands, keys, lcd = device(9)
    added, placed = c.add_device(composed, "Box", commands, keys, lcd)
    if c.delete_device(added, placed.mode) != composed:
        print("FAIL adding and deleting a device does not give the same file back")
        return 1
    print("PASS a device added to composed-2026-10-18 and deleted is the same file byte for byte")

    # every one of the sample's own devices can go, the ones activities use too
    before = c.devices(sample)
    acts = [a.name for a in c.activities(sample)]
    for victim in before:
        out = c.delete_device(sample, victim.mode)
        left = sorted((x.name, x.page, x.corner) for x in c.devices(out))
        want = sorted((x.name, x.page, x.corner) for x in before if x.name != victim.name)
        if left != want or [a.name for a in c.activities(out)] != acts:
            print(f"FAIL deleting {victim.name}: devices {left}, activities {c.activities(out)}")
            return 1
        power = c.power_variables(out)
        if victim.name in power:
            print(f"FAIL {victim.name} still has a power variable in use")
            return 1
    print(f"PASS each of the sample's {len(before)} devices deleted, activities kept, "
          "the device gone from every Devices screen and the all-off list")

    # moving: a swap across pages, a move into an empty corner, a new menu page
    pos = lambda r: {x.name: (x.page, x.corner) for x in c.devices(r)}
    pc = next(x for x in c.devices(composed) if x.name == "PC")
    moved = c.place_device(composed, pc.mode, 0, 3)
    p = pos(moved)
    if p["PC"] != (0, 3) or p["XBOX 360"] != (1, 1):
        print(f"FAIL swapping PC onto page 1: {p}")
        return 1
    amp = next(x for x in c.devices(moved) if x.name == "Amplifier Genius")
    moved = c.place_device(moved, amp.mode, 1, 2)
    if pos(moved)["Amplifier Genius"] != (1, 2):
        print(f"FAIL moving into an empty corner: {pos(moved)}")
        return 1
    moved = c.place_activity(moved, "Play on XBOX", 1, 0)
    if {a.name: (a.page, a.corner) for a in c.activities(moved)}["Play on XBOX"] != (1, 0):
        print("FAIL moving an activity to a new menu page")
        return 1
    print("PASS devices swapped across pages and moved, an activity moved to a new menu page")

    # an activity read back as steps, one ending on a device, one with its
    # volume elsewhere; each deleted again gives the same file back
    steps = c.describe_activity(composed, "Watch TV +")
    sent = [(s["device"], s.get("label")) for s in steps if s["kind"] == "send"]
    if sent != [("TV Panason..", "Input Hdmi1")]:
        print(f"FAIL Watch TV reads as {steps}")
        return 1
    modes = {x.name: x.mode for x in c.devices(composed)}
    hdmi2 = next(k for k, v in c.device_commands(composed).items() if v == ("TV Panason..", "Input Hdmi2"))
    for show, volume in ((modes["X96 Box"], None), (modes["X96 Box"], modes["Amplifier Genius"]),
                         (None, modes["Amplifier Genius"])):
        out = c.add_activity(composed, "Film", "Watch TV +", ["TV Panason.."], writes={},
                             steps=[hdmi2], show=show, volume=volume)
        film = next(a for a in c.activities(out) if a.name == "Film")
        if show is not None and volume is None and film.work_mode != show:
            print(f"FAIL Film ends on mode {film.work_mode}, not the X96's {show}")
            return 1
        if volume is not None:
            vol = c._Doc(out).mode(film.work_mode)["start"]
            amp = c._Doc(out).mode(volume)["start"]
            pick = lambda at: {t: (o, v) for t, o, v in c.cmp.tagged(c._Doc(out).blob, at)
                               if t in (c.KEYS["VolumeUp"], c.KEYS["Mute"])}
            if pick(vol) != pick(amp) or not pick(vol):
                print(f"FAIL Film's volume keys {pick(vol)} are not the amplifier's {pick(amp)}")
                return 1
        sent = [s.get("label") for s in c.describe_activity(out, "Film") if s["kind"] == "send"]
        if sent != ["Input Hdmi2"] or c.delete_activity(out, "Film") != composed:
            print(f"FAIL Film (show {show}, volume {volume}): sends {sent}, or deleting it differs")
            return 1
    print("PASS activities read as steps; one ends on the X96, one sends volume to the amplifier; "
          "each deleted gives the file back")

    # an activity edited: read as settings, built again, in the same corner
    hdmi1 = next(k for k, v in c.device_commands(composed).items() if v == ("TV Panason..", "Input Hdmi1"))
    out = c.add_activity(composed, "Film", "Watch TV +", ["TV Panason.."], writes={}, steps=[hdmi2],
                         show=modes["X96 Box"], volume=modes["Amplifier Genius"])
    s = c.activity_settings(out, "Film")
    if (s["on"], s["steps"], s["show"], s["volume"], s["unread"]) != \
            (["TV Panason.."], [hdmi2], modes["X96 Box"], modes["Amplifier Genius"], 0):
        print(f"FAIL Film reads back as {s}")
        return 1
    edited = c.edit_activity(out, "Film", "Movie", "Film", ["Amplifier Genius", "TV Panason.."],
                             writes={}, steps=[hdmi1], show=modes["X96 Box"])
    s = c.activity_settings(edited, "Movie")
    where = {a.name: (a.page, a.corner) for a in c.activities(edited)}
    if (s["on"], s["steps"], s["show"], s["volume"]) != \
            (["Amplifier Genius", "TV Panason.."], [hdmi1], modes["X96 Box"], None) or \
            where.get("Movie") != (0, 3) or "Film" in where:
        print(f"FAIL Film edited into Movie reads {s} at {where}")
        return 1
    w = c.activity_settings(composed, "Watch TV +")
    again = c.edit_activity(composed, "Watch TV +", "Watch TV +", "Watch TV +", w["on"], None,
                            w["steps"], w["show"], w["volume"])
    same = lambda raw: sorted(map(repr, c.describe_activity(raw, "Watch TV +")))   # order of the switching aside
    if w["unread"] or same(again) != same(composed) \
            or {a.name: (a.page, a.corner) for a in c.activities(again)} != \
            {a.name: (a.page, a.corner) for a in c.activities(composed)}:
        print("FAIL Watch TV + edited with its own settings does something else")
        return 1
    print("PASS an activity reads back as its settings, is edited and renamed in its own corner, "
          "and Watch TV + rebuilt from its own settings does the same")

    # a wait in an activity: tenths of a second for one device, 100 at most
    # per instruction (@dannybloe, sections 70 and 235)
    hdmi2 = next(k for k, v in c.device_commands(composed).items() if v == ("TV Panason..", "Input Hdmi2"))
    out = c.add_activity(composed, "Film", "Watch TV +", ["TV Panason.."], writes={},
                         steps=[hdmi1, ("wait", hdmi1[0], 150), hdmi2])
    s = c.activity_settings(out, "Film")
    film = next(a for a in c.activities(out) if a.name == "Film")
    d = c._Doc(out)
    waits = [d.ins(x) for o, x in d.ins(film.start) if o == 0x7F and all(p == 0x7C for p, _ in d.ins(x))]
    told = [s_ for s_ in c.describe_activity(out, "Film") if s_["kind"] == "wait"]
    if s["steps"] != [hdmi1, ("wait", hdmi1[0], 150), hdmi2] or \
            waits != [[(0x7C, hdmi1[0] << 8 | 100), (0x7C, hdmi1[0] << 8 | 50)]] or \
            [t["seconds"] for t in told] != [15] or c.delete_activity(out, "Film") != composed:
        print(f"FAIL a wait of 15 s: {s['steps']}, {waits}, {told}")
        return 1
    print("PASS a 15 s wait for the TV between two inputs: 100 and 50 tenths, read back, deleted again")

    # a device renamed: its corner on every Devices screen and its pages' title
    renamed = c.rename_device(composed, modes["X96 Box"], "Android box")
    names = {x.mode: x.name for x in c.devices(renamed)}
    if names[modes["X96 Box"]] != "Android box" or             {m: n for m, n in names.items() if m != modes["X96 Box"]} !=             {x.mode: x.name for x in c.devices(composed) if x.name != "X96 Box"}:
        print(f"FAIL renaming the X96: {names}")
        return 1
    if "CurrentActivityState_0_4" not in [r["name"] for r in c._Doc(composed).region(
            c._Doc(composed).S[0], "name_table")["records"]] or             "CurrentActivityState_0_5" not in [r["name"] for r in c._Doc(out).region(
                c._Doc(out).S[0], "name_table")["records"]]:
        print("FAIL CurrentActivityState's name does not count its values")
        return 1
    print("PASS the X96 renamed on every Devices screen and its own pages; the activity state's "
          "name counts its values")

    # a power state for the X96 and the Samsung: Off and every activity switch
    # them off, a new activity can switch one on, deleting one takes it out
    label = lambda n: {v[1]: k[1] for k, v in c.device_commands(composed).items() if v[0] == n}
    out = c.add_power(composed, modes["X96 Box"], label("X96 Box")["Power"])
    out = c.add_power(out, modes["Samsung T200HD"], label("Samsung T200HD")["Power"])
    power = c.power_variables(out)
    if power.get("X96 Box") != 23 or power.get("Samsung T200HD") != 24:
        print(f"FAIL power variables {power}")
        return 1
    for a in c.activities(out):
        offs = {s["device"] for s in c.describe_activity(out, a.name) if s["kind"] == "power" and not s["on"]}
        if not {"X96 Box", "Samsung T200HD"} <= offs:
            print(f"FAIL {a.name} leaves a new power state alone: {offs}")
            return 1
    film = c.add_activity(out, "Film", "Watch TV +", ["TV Panason..", "X96 Box"])
    on = {s["device"] for s in c.describe_activity(film, "Film") if s["kind"] == "power" and s["on"]}
    gone = c.delete_device(out, modes["X96 Box"])
    if on != {"TV Panason..", "X96 Box"} or "X96 Box" in c.power_variables(gone):
        print(f"FAIL Film switches on {on}; after deleting the X96 {c.power_variables(gone)}")
        return 1
    print("PASS the X96 and the Samsung get power states: in the all-off list and every activity, "
          "an activity can switch one on, a deleted device leaves both")
    return 0


if __name__ == "__main__":
    sys.exit(main())
