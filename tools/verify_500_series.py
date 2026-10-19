"""The Harmony 515 and 555 read, and the 515 composed on, with the 525's tools.

The three run the same firmware (FORMAT 5ab); their configurations differ in
the typeface, in which state variables CurrentActivityState and the device
flag are, and in where a corner's second line is drawn (FORMAT 5ai). Checks:

* every glyph code of the three samples reads as the character it draws, the
  525's and the 515's against their alphabets read by eye;
* the 515's devices, activities and commands read with whole names;
* on the 515, adding a device and an activity, editing the activity, renaming,
  moving and deleting a device, and a power state each pass the composer's own
  checks, and adding then deleting an activity gives the file back;
* the 555, empty, takes a first device with nothing to copy from.
"""
from __future__ import annotations

import sys
from pathlib import Path

import _paths  # noqa: F401
import build_arch9_glyphs as glyphs
import compose525 as c

ROOT = Path(__file__).resolve().parents[1] / "samples"


def main() -> int:
    s525 = (ROOT / "harmony525" / "config.EZHex").read_bytes()
    s515 = (ROOT / "harmony515" / "H515-Config.EZHex").read_bytes()
    s555 = (ROOT / "harmony555" / "H555-Config.EZHez").read_bytes()
    failed = 0

    def check(ok, text):
        nonlocal failed
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {text}")

    d525, d515, d555 = c._Doc(s525), c._Doc(s515), c._Doc(s555)
    unread = sorted(set(c._glyph_shapes(d555.blob, d555.fonts)) - set(d555.alphabet))
    check(d525.alphabet == c.alphabet_map() and d515.alphabet == c.alphabet_map(glyphs.H515_ALPHABET)
          and not unread,
          f"alphabets by pixels: 525 {len(d525.alphabet)} codes, 515 {len(d515.alphabet)}, "
          f"555 {len(d555.alphabet)} with none unread")
    check((d525.cas_var, d525.device_flag_op, d515.cas_var, d515.device_flag_op) == (15, 0x92, 17, 0x95),
          "CurrentActivityState 15 and the device flag 18 on the 525, 17 and 21 on the 515")

    devs = sorted((x.name, x.page, x.corner) for x in c.devices(s515))
    acts = sorted(a.name for a in c.activities(s515))
    labels = {v[1] for v in c.device_commands(s515).values() if v[0] == "Mini System"}
    check(devs == [("Digital Music", 0, 2), ("Mini System", 0, 0), ("squeeze..", 0, 1)]
          and acts == ["Listen to CDs", "Listen to Digital"]
          and {"Input Video", "Next Track", "InputAux", "Input Dbs/Ld"} <= labels,
          f"515 reads: {devs}, {acts}, {len(labels)} Mini System commands")

    modes = {x.name: x.mode for x in c.devices(s515)}
    cmds = [c.nec_command("Power", 0x40, 0x12), c.nec_command("Vol+", 0x40, 0x13, repeat=True)]
    added, placed = c.add_device(s515, "Test Box", cmds, {"VolumeUp": "Vol+"}, [[("Power", "Power"), None, None, None]])
    party = c.add_activity(s515, "Party", "Listen to CDs", ["Mini System"], writes={})
    edited = c.edit_activity(party, "Party", "Party 2", "Party", ["Mini System", "squeeze.."],
                             writes={}, show=modes["squeeze.."], volume=modes["Mini System"])
    power = c.add_power(added, placed.mode, next(k[1] for k, v in c.device_commands(added).items()
                                                 if v == ("Test Box", "Power")))
    renamed = c.rename_device(s515, modes["Mini System"], "Hifi")
    moved = c.place_device(s515, modes["Mini System"], 0, 3)
    gone = c.delete_device(s515, modes["squeeze.."])
    check("Test Box" in [x.name for x in c.devices(added)]
          and c.delete_activity(party, "Party") == s515
          and "Party 2" in [a.name for a in c.activities(edited)]
          and c.power_variables(power).get("Test Box") is not None
          and "Hifi" in [x.name for x in c.devices(renamed)]
          and {x.name: x.corner for x in c.devices(moved)}["Mini System"] == 3
          and "squeeze.." not in [x.name for x in c.devices(gone)],
          "515 composes: a device, an activity edited, a power state, a rename, a move, a deletion; "
          "an activity added and deleted gives the file back")

    # letters the 515's font 0 lacks come in its own bold style: borrowed
    # from a font set that draws like it, or drawn for its 12 rows
    kitchen = c.rename_device(s515, modes["Mini System"], "Kitchen Q7&J")
    check({x.mode: x.name for x in c.devices(kitchen)}[modes["Mini System"]] == "Kitchen Q7&J",
          "515 takes a name with K, Q, 7, & and J, none of which its font had")

    # the empty 555 takes a first device with nothing to copy: every key as
    # Logitech lists them, the Devices screen's frame, section 5 grown from 0
    first, where = c.add_device(s555, "TV Box", cmds, {"VolumeUp": "Vol+"},
                                [[("Power", "Power"), None, None, None]])
    names = {v[1] for v in c.device_commands(first).values()}
    check(not c.devices(s555) and [(x.name, x.page, x.corner) for x in c.devices(first)] == [("TV Box", 0, 0)]
          and {"Power", "VolumeUp"} <= names,
          f"555 starts empty and takes a first device, top left of Devices, its keys named {sorted(names)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
