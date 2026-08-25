#!/usr/bin/env python3
"""Check @kkong42's H885 standard-button notes against the dumped config.

The human oracle is issue #20 comment 5254896572 in
``trelowney/harmony-decompiler``.  The config is public
``samples/arch8/H885-LivingRoom.EZHex``.  Danny Bloemendaal's MIT-licensed
``harmony-explorations`` parser supplies the structural readers; this script
adds only sample-specific joins and assertions.

For each named device the LCD page identifies a mode record and its IR group.
The mode record binds physical-key press tags to action-list indices, and an
action list names an IR ``(group, command)`` pair.  Reusing the exact same
action list or IR pair on a labelled LCD button and a standard hard button is
an operational name for that scan code, not an inference from PCB numbering.

Nothing here opens USB, modifies a sample, or writes to a remote.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Device:
    name: str
    page_program: int
    ir_group: int


DEVICES = {
    "TX-55GZ950B": Device("TX-55GZ950B", 0x04E8CF, 5),
    "HR-S6855": Device("HR-S6855", 0x0534EC, 6),
    "DMP-BD60": Device("DMP-BD60", 0x04F4A8, 2),
    "DTR-T2110": Device("DTR-T2110", 0x055E34, 1),
    "TX-NR626": Device("TX-NR626", 0x054886, 3),
    "RealBox 4K": Device("RealBox 4K", 0x0516C0, 4),
    "DVL-909": Device("DVL-909", 0x05297E, 0),
}

OFFICIAL_MANUAL_SHA256 = "1a095a0614074c4887d1621ca9494e42f0df981c17f136a66a9c96b98f863da5"

# H880/H885 scan codes independently closed by
# verify_arch8_key_matrix.py.  The four names at the irregular K51--K56 tail
# are the important consumers of this verifier.
SCAN = {
    "red": 20,
    "green": 19,
    "yellow": 60,
    "blue": 52,
    "ch_minus": 36,
    "ch_plus": 37,
    "previous": 40,
    "ok": 33,
    "menu": 17,
    "exit": 18,
    "guide": 50,
    "info": 51,
    "skip_back": 24,       # PCB K24, legend "Replay"
    "skip_forward": 53,    # PCB K51, legend "Skip"
    "rewind": 23,
    "fast_forward": 54,    # PCB K52, legend "Fwd"
    "pause": 55,           # PCB K53
    "play": 56,            # PCB K54
    "plus_clear": 31,      # PCB legend "+ Clear"
    "e_enter": 63,         # PCB legend "E Enter"
}


def _load_upstream(explorations: Path):
    tools = explorations / "tools"
    if not (tools / "_bootstrap.py").is_file():
        raise SystemExit(f"harmony-explorations tools not found at {tools}")
    sys.path.insert(0, str(tools))
    import _bootstrap  # noqa: F401, PLC0415
    from harmony import ezfile, gspm  # noqa: PLC0415
    return ezfile, gspm


def _mode_for_program(container, program: int):
    found = [
        mode for mode in container.mode_records() or []
        if any(page.program == program for page in mode.pages)
    ]
    assert len(found) == 1, (hex(program), len(found))
    return found[0]


def _page_for_program(mode, program: int):
    found = [page for page in mode.pages if page.program == program]
    assert len(found) == 1, (hex(program), len(found))
    return found[0]


def _entry(entries, scan: int):
    found = [entry for entry in entries if entry.tag == 0x80 | scan]
    assert len(found) == 1, (scan, len(found))
    return found[0]


def _ir(container, actions, entry):
    if entry.opcode == 0:
        return None
    assert entry.opcode == 0x7F, (hex(entry.opcode), hex(entry.operand))
    assert entry.operand < len(actions), entry.operand
    refs = [container.ir_reference(insn) for insn in actions[entry.operand]]
    refs = [ref for ref in refs if ref is not None]
    assert len(refs) == 1, (entry.operand, refs)
    return refs[0]


def _device_bindings(container, actions, device: Device):
    mode = _mode_for_program(container, device.page_program)
    bindings = {}
    for name, scan in SCAN.items():
        entry = _entry(mode.entries, scan)
        ref = _ir(container, actions, entry)
        if ref is not None:
            assert ref[0] == device.ir_group, (device.name, name, ref)
        bindings[name] = (entry, ref)
    return mode, bindings


def _page_ir(container, actions, mode, program: int, scan: int):
    page = _page_for_program(mode, program)
    entry = _entry(container.tagged_list(page.list_address) or [], scan)
    return entry, _ir(container, actions, entry)


def _assert_blank(bindings, *names: str):
    for name in names:
        entry, ref = bindings[name]
        assert entry.opcode == 0 and entry.operand == 0 and ref is None, name


def _assert_bound(bindings, *names: str):
    for name in names:
        entry, ref = bindings[name]
        assert entry.opcode == 0x7F and ref is not None, name


def _outcomes(container, actions, entries):
    """Map every press scan code in one tagged list to its IR result or None."""
    out = {}
    for entry in entries:
        if entry.tag & 0xC0 != 0x80:
            continue
        out[entry.tag & 0x3F] = _ir(container, actions, entry)
    return out


def _human_oracle_filter(
    container, actions, hr_mode, dmp_mode, dvl_mode, sample: Path, manual: Path, gspm,
):
    """Filter PCB relabellings first by issue 20, then by Logitech's manual."""
    import verify_arch8_key_matrix as matrix  # noqa: PLC0415

    hr = _outcomes(container, actions, hr_mode.entries)
    dmp = _outcomes(container, actions, dmp_mode.entries)
    dvl = _outcomes(container, actions, dvl_mode.entries)
    dmp_page = _page_for_program(dmp_mode, 0x04F4A8)
    dvl_page = _page_for_program(dvl_mode, 0x05297E)
    dmp_custom = _outcomes(
        container, actions, container.tagged_list(dmp_page.list_address) or [],
    )
    dvl_custom = _outcomes(
        container, actions, container.tagged_list(dvl_page.list_address) or [],
    )

    samples = sample.parent
    h880 = set(matrix.canonical_codes(samples / "H880-Bedroom.EZHex", 0x0A22, 53))
    h885 = set(matrix.canonical_codes(sample, 0x0A32, 55))
    candidates = matrix.enumerate_solutions(h880, h885)
    assert len(candidates) == 11_520

    def value(table, key_to_scan, key):
        return table.get(key_to_scan[key])

    def agrees_standard_notes(candidate):
        key_to_scan = candidate[3]
        get = lambda table, key: value(table, key_to_scan, key)
        return all((
            # DMP-BD60 named equality anchors and blank Enter key.
            get(dmp, 50) == get(dmp_custom, 8) is not None,
            get(dmp, 55) == get(dmp_custom, 48) is not None,
            get(dmp, 36) == get(dmp, 24) is not None,
            get(dmp, 37) == get(dmp, 51) is not None,
            get(dmp, 63) is None,
            # HR-S6855 exact bound/blank pattern.
            all(get(hr, key) is not None for key in (33, 18, 63)),
            all(get(hr, key) is None for key in (50, 55, 24, 51)),
            # DVL-909 Display equality and exact bound/blank pattern.
            get(dvl, 55) == get(dvl_custom, 46) is not None,
            all(get(dvl, key) is None for key in (
                20, 19, 60, 56, 36, 37, 40, 50, 24, 51, 31, 63,
            )),
            all(get(dvl, key) is not None for key in (
                33, 17, 18, 55, 54, 23, 52, 53,
            )),
        ))

    standard_survivors = [candidate for candidate in candidates if agrees_standard_notes(candidate)]
    assert len(standard_survivors) == 4
    assert {tuple(sorted(candidate[1].items())) for candidate in standard_survivors} == {
        (("A", 1), ("B", 2), ("C", 3), ("D", 4)),
    }

    # Issue 20 comment 5240795540 says all eight DVL-909 page-1 LCD positions
    # are assigned, including position 8 = Chp/Time.  @kkong42's PCB legend
    # independently names physical K44 as custom button 8.  Only two of the
    # four candidates put K44 on a scan with a page-specific action.
    lcd_keys = (5, 45, 6, 46, 7, 48, 8, 44)
    custom_survivors = [
        candidate for candidate in standard_survivors
        if all(value(dvl_custom, candidate[3], key) is not None for key in lcd_keys)
    ]
    assert len(custom_survivors) == 2

    # Pin the exact official source before using its role descriptions.  Page
    # 4 states that Activities displays the activity list, OFF turns all
    # devices off and HELP runs the on-remote fixer.  A candidate which maps
    # any of those physical keys to a scan that sends a selected device's IR
    # command contradicts those documented global roles.
    assert hashlib.sha256(manual.read_bytes()).hexdigest() == OFFICIAL_MANUAL_SHA256
    import verify_arch8_human_oracles as screens  # noqa: PLC0415

    programs, failed = container.reachable_screen_programs()
    assert not failed
    glyphs = next(oracle.glyphs for oracle in screens.ORACLES if oracle.sample == sample.name)
    device_modes = []
    for device in DEVICES.values():
        mode = _mode_for_program(container, device.page_program)
        titles = []
        for program in screens._closure(programs, device.page_program):
            for instruction in program:
                if tuple(instruction.operands[:2]) != (3, 10):
                    continue
                codes = None
                if instruction.opcode == gspm.SCREEN_TEXT_INLINE and instruction.glyphs:
                    codes = instruction.glyphs
                elif instruction.opcode == 4:
                    codes = screens._external_codes(container, instruction.operands)
                if codes is not None:
                    titles.append("".join(glyphs.get(code, "?") for code in codes))
        assert device.name in titles, (device.name, titles)
        device_modes.append(mode)
    device_outcomes = [_outcomes(container, actions, mode.entries) for mode in device_modes]

    def agrees_manual_roles(candidate):
        key_to_scan = candidate[3]
        global_keys_are_not_device_ir = all(
            table.get(key_to_scan[key]) is None
            for table in device_outcomes
            for key in (1, 2, 3)  # Activities, OFF/Power, Help
        )
        numeric_keys_are_device_ir = all(
            table.get(key_to_scan[key]) is not None
            for table in device_outcomes
            for key in (25, 26, 27)  # 1, 4, 8
        )
        return global_keys_are_not_device_ir and numeric_keys_are_device_ir

    survivors = [candidate for candidate in custom_survivors if agrees_manual_roles(candidate)]
    assert len(survivors) == 1

    consensus = {
        key: next(iter(values))
        for key in survivors[0][3]
        if len(values := {candidate[3][key] for candidate in survivors}) == 1
    }
    ambiguous = {
        key: sorted({candidate[3][key] for candidate in survivors})
        for key in survivors[0][3]
        if len({candidate[3][key] for candidate in survivors}) != 1
    }
    assert len(consensus) == 55
    assert not ambiguous
    assert {key: consensus[key] for key in range(51, 57)} == {
        51: 53, 52: 54, 53: 55, 54: 56, 55: 51, 56: 52,
    }
    return (
        len(candidates), len(standard_survivors), len(custom_survivors),
        len(survivors), consensus,
    )


def verify(sample: Path, explorations: Path, manual: Path) -> None:
    ezfile, gspm = _load_upstream(explorations)
    payload = ezfile.decode_payload(ezfile.load_image(sample)).payload
    container = gspm.parse(payload)
    assert container.architecture == 8
    actions = container.action_lists()
    assert actions is not None

    hr_mode, hr = _device_bindings(container, actions, DEVICES["HR-S6855"])
    dmp_mode, dmp = _device_bindings(container, actions, DEVICES["DMP-BD60"])
    dvl_mode, dvl = _device_bindings(container, actions, DEVICES["DVL-909"])

    # DMP-BD60: four semantic equalities close six standard-button labels.
    # Page 1 custom positions 6 and 7 are Display and Functions respectively.
    _, dmp_display = _page_ir(container, actions, dmp_mode, 0x04F4A8, 48)
    _, dmp_functions = _page_ir(container, actions, dmp_mode, 0x04F4A8, 8)
    assert dmp["info"][1] == dmp_display == (2, 31)
    assert dmp["guide"][1] == dmp_functions == (2, 37)
    assert dmp["ch_minus"][1] == dmp["skip_back"][1] == (2, 48)
    assert dmp["ch_plus"][1] == dmp["skip_forward"][1] == (2, 8)
    _assert_bound(dmp, "ok", "exit")
    _assert_blank(dmp, "e_enter")

    # DVL-909: the reported blank/nonblank pattern matches every listed key.
    # Its custom Display button is page 1 position 4, physical custom scan 46.
    _, dvl_display = _page_ir(container, actions, dvl_mode, 0x05297E, 46)
    assert dvl["info"][1] == dvl_display == (0, 44)
    _assert_blank(
        dvl,
        "red", "green", "yellow", "blue",
        "ch_minus", "ch_plus", "previous", "guide",
        "skip_back", "skip_forward", "plus_clear", "e_enter",
    )
    _assert_bound(
        dvl,
        "ok", "menu", "exit", "info", "play", "rewind",
        "fast_forward", "pause",
    )

    # HR-S6855 confirms the complete reported standard-button subset.  Unlike
    # the neighbouring activity handler set, its device mode leaves both
    # Replay and Skip unbound, exactly as the saved record says.
    _assert_bound(hr, "ok", "exit", "e_enter")
    _assert_blank(hr, "guide", "info", "skip_back", "skip_forward")

    total, standard, custom, final, consensus = _human_oracle_filter(
        container, actions, hr_mode, dmp_mode, dvl_mode, sample, manual, gspm,
    )

    print("PASS H885 slot-6 device modes -> action lists -> IR groups")
    print("PASS DMP-BD60: Guide=Functions, Info=Display, Ch-/Replay and Ch+/Skip pairs")
    print("PASS DVL-909: all 12 reported blanks and all 8 reported bindings; Info=Display")
    print("PASS irregular tail: scan 51=Info, 53=Skip, 54=Fwd, 55=Pause, 56=Play")
    print("PASS HR-S6855: OK, Exit and E bound; Guide, Info, Replay and Skip blank")
    print(
        f"PASS issue-20 standard mappings: {total} occupancy-compatible PCB "
        f"relabellings -> {standard}"
    )
    print(
        f"PASS DVL custom position 8: {standard} -> {custom}; "
        "K44=scan 44, eliminating the Net 8/11 swap"
    )
    print(
        f"PASS official H880 manual global-button roles across seven device modes: "
        f"{custom} -> {final}; all {len(consensus)}/55 physical keys fixed"
    )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample", type=Path,
        default=root / "samples" / "arch8" / "H885-LivingRoom.EZHex",
    )
    parser.add_argument(
        "--explorations", type=Path,
        default=None,
        help="a checkout of Danny Bloemendaal's harmony-explorations "
             "(github.com/dannybloe/harmony-explorations, MIT). Its parser is "
             "required, not optional: without it this check cannot run",
    )
    parser.add_argument(
        "--manual", type=Path, default=None,
        help="Logitech's Harmony 880 user guide as a PDF. It is not "
             "redistributed here, for the reason docs/BUTTON-LAYOUT.md gives "
             "about the 525 one; Logitech still serves it at the time of "
             "writing",
    )
    args = parser.parse_args()
    for value, flag, what in (
            (args.explorations, "--explorations", "a harmony-explorations checkout"),
            (args.manual, "--manual", "Logitech's H880 user guide PDF")):
        if value is None:
            raise SystemExit(
                f"{flag} is required: this check joins the public sample to "
                f"{what}, which is not in this repository. See --help.")
    verify(args.sample, args.explorations, args.manual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
