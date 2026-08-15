#!/usr/bin/env python3
"""Name a physical key by catching what it transmits.

The chain from a key to an infrared signal is decoded end to end:

    key -> scan code -> action list -> (group, command) -> IR record -> waveform

Every arrow but the first. So point a receiver at the remote, press a key, and
match the burst against the config's own records: that names the scan code, and
the label printed on the key you pressed names the button.

Why this route rather than one of the others in `docs/OPEN-QUESTIONS.md`:

  * **Nothing is written to the remote.** No learning mode, no flash write, no
    command outside the read-only set. The remote need not even be plugged in.
  * **No account is needed.** The catalogue is the config's own IR data, so it
    works on a config that nobody holds the Logitech-side records for.

Two things it cannot do, both measurable in advance with `--report`:

  * A key that transmits nothing cannot be heard. On the bundled 525 that is 8
    of the 50, and they are the ones you would guess: Off, Activities, Devices,
    Help, Glow and the paging arrows.
  * Two keys sending the same command in the same mode cannot be told apart by
    listening, which is why `--plan` picks a mode per key where the signal is
    that key's alone.

    python tools/ir_keymap_oracle.py --report
    python tools/ir_keymap_oracle.py --plan
    python tools/ir_keymap_oracle.py --match capture.txt --mode 113
    python tools/ir_keymap_oracle.py --selftest

Capture format is deliberately loose, because every receiver prints something
different. Integers in microseconds separated by whitespace or commas, either
signed as +mark/-space or unsigned and alternating starting with a mark. A token
`carrier=37900` anywhere sets the measured carrier, and `#` starts a comment.
AnalysIR LearnIR raw lines beginning `$nn` are understood, with `nn` read as the
measured carrier in kHz.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths
import analyze_525_ir as ir
import verify_525_semantics as semantics

MARK = 0x8000
MAX_US = 0x7FFF
VIRTUAL = 0x06          # the queue event that is not a key at all

# Established by rendering these modes; see verify_525_semantics.
DEVICE_MODES = {73: "Amplifier Genius", 78: "TV Panasonic",
                111: "X96 Box", 113: "XBOX 360"}


def waveform(words: list[int]) -> list[int]:
    """Class-5 words to signed microseconds: positive mark, negative space."""
    return [(w & MAX_US) if w & MARK else -(w & MAX_US) for w in words]


def trim(sequence: list[int]) -> list[int]:
    """Drop the leading and trailing gaps; they carry no information."""
    start, end = 0, len(sequence)
    while start < end and sequence[start] < 0:
        start += 1
    while end > start and sequence[end - 1] < 0:
        end -= 1
    return sequence[start:end]


def merge(sequence: list[int]) -> list[int]:
    """Sum neighbouring entries of the same polarity.

    A stored waveform does not strictly alternate: two dictionary symbols in a
    row can both be marks, and the emitter simply holds the carrier on across
    both. A receiver hears one pulse of the combined length and has no way to
    report the split, so the stored form has to be collapsed the same way before
    anything is compared. Getting this wrong makes a record look unlike itself.
    """
    out: list[int] = []
    for value in sequence:
        if out and (out[-1] < 0) == (value < 0):
            merged = out[-1] + value
            out[-1] = max(-MAX_US, min(MAX_US, merged))
        else:
            out.append(value)
    return out


def prepare(sequence: list[int]) -> list[int]:
    return trim(merge(trim(sequence)))


def catalogue(blob: bytes) -> dict:
    sections = semantics.section_offsets(blob)
    groups = ir.ir_groups(blob, sections)

    signals = {}
    for group_index, group in enumerate(groups):
        for command, address in enumerate(group):
            header = ir.record(blob, address)
            bodies = [ir.body(blob, pointer) for pointer in header["pointers"]]
            signals[(group_index, command)] = {
                "carrier_hz": round(header["frequency_hz"], 1),
                "waveforms": [prepare(waveform(b["words"])) for b in bodies],
                "nec": ir.nec_frame(bodies[0]["words"]) if bodies else None,
            }

    senders = collections.defaultdict(list)
    per_mode = collections.defaultdict(lambda: collections.defaultdict(set))
    for mode in range(ir.u24(blob, sections[semantics.MODE_SLOT])):
        for binding in ir.mode_bindings(blob, sections, mode):
            key = (binding["group"], binding["command"])
            if key not in signals:
                continue
            senders[key].append({"mode": mode, "source": binding["source"],
                                 "page": binding["page"], "code": binding["tag"]})
            per_mode[mode][binding["tag"]].add(key)
    return {"signals": signals, "senders": senders, "per_mode": per_mode}


def signature(cat: dict, key) -> tuple:
    signal = cat["signals"][key]
    return (signal["nec"], tuple(tuple(w) for w in signal["waveforms"]),
            signal["carrier_hz"])


def unambiguous_in(cat: dict, mode: int) -> tuple[set[int], set[int]]:
    """Codes bound in this mode, and those whose signal is theirs alone here."""
    bound = cat["per_mode"].get(mode, {})
    owners = collections.defaultdict(set)
    for code, keys in bound.items():
        for key in keys:
            owners[signature(cat, key)].add(code)
    unique = {code for code, keys in bound.items()
              if code != VIRTUAL and any(len(owners[signature(cat, k)]) == 1
                                         for k in keys)}
    return set(bound) - {VIRTUAL}, unique


def matrix_position(code: int) -> str:
    if code == VIRTUAL:
        return "virtual event, not a key"
    if code & 0xC0 != 0x80:
        return "not a press event"
    return f"row {(code >> 3) & 7}, column {code & 7}"


# ---------------------------------------------------------------- matching

def distance(capture: list[int], stored: list[int]) -> float:
    """Mean relative error across both complete sequences, lower is better.

    Scored over the longer sequence rather than the overlap on purpose. A
    record that runs out early cannot explain the rest of what was heard, and a
    capture that runs out early has not established the rest of the record.
    Missing positions in either direction therefore count as full mismatches;
    without the first rule short records win everything, and without the second
    a truncated prefix can look like a perfect capture.
    """
    capture = prepare(capture)
    stored = prepare(stored)
    if not capture or not stored:
        return 9.9
    errors = []
    for index in range(max(len(capture), len(stored))):
        if index >= len(capture) or index >= len(stored):
            errors.append(1.0)
            continue
        heard = capture[index]
        want = stored[index]
        if (heard < 0) != (want < 0):
            errors.append(1.0)
            continue
        a, b = abs(heard), abs(want)
        if a >= MAX_US and b >= MAX_US:      # both saturated, no information
            continue
        errors.append(min(1.0, abs(a - b) / max(a, b, 200)))
    return statistics.fmean(errors) if errors else 9.9


def match(cat: dict, capture: list[int], carrier: float | None = None,
          mode: int | None = None, carrier_tolerance: float = 0.05):
    capture = prepare(capture)
    if mode is not None:
        allowed = {k for keys in cat["per_mode"].get(mode, {}).values() for k in keys}
    else:
        allowed = set(cat["senders"])
    results = []
    for key in sorted(allowed):
        signal = cat["signals"][key]
        best = min((distance(capture, w) for w in signal["waveforms"]), default=9.9)
        carrier_ok = None
        if carrier is not None:
            carrier_ok = abs(signal["carrier_hz"] - carrier) <= carrier_tolerance * carrier
            if not carrier_ok:
                best += 1.0
        senders = cat["senders"].get(key, [])
        if mode is not None:
            senders = [s for s in senders if s["mode"] == mode]
        results.append({"key": key, "distance": best, "carrier_hz": signal["carrier_hz"],
                        "carrier_ok": carrier_ok, "nec": signal["nec"], "senders": senders})
    results.sort(key=lambda r: r["distance"])
    return results


# ---------------------------------------------------------------- capture I/O

def read_capture(text: str) -> tuple[list[int], float | None]:
    carrier = None
    learnir = re.search(r"^\s*\$(\d+)", text, re.MULTILINE)
    if learnir:                                   # LearnIR raw line, carrier in kHz
        carrier = float(learnir.group(1)) * 1000.0
        text = re.sub(r"^\s*\$\d+", " ", text, flags=re.MULTILINE)
    named = re.search(r"carrier\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if named:
        carrier = float(named.group(1))
        if carrier < 1000:                        # given in kHz
            carrier *= 1000.0
    text = re.sub(r"carrier\s*[=:]\s*[0-9.]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"#.*", " ", text)
    numbers = [int(round(float(n))) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    if not numbers:
        raise SystemExit("no numbers found in the capture")
    if all(n >= 0 for n in numbers):
        numbers = [n if index % 2 == 0 else -n for index, n in enumerate(numbers)]
    return numbers, carrier


# ---------------------------------------------------------------- reports

def report(cat: dict) -> None:
    sources = collections.defaultdict(set)
    everywhere = set()
    for key, senders in cat["senders"].items():
        for s in senders:
            if s["code"] != VIRTUAL:
                sources[s["code"]].add(s["source"])
                everywhere.add(s["code"])

    anywhere_unique = set()
    for mode in cat["per_mode"]:
        anywhere_unique |= unambiguous_in(cat, mode)[1]

    print(f"IR records in this config       : {len(cat['signals'])}")
    print(f"key codes that transmit         : {len(everywhere)}")
    print(f"  reachable from a hard key     : "
          f"{len([c for c in everywhere if 'physical' in sources[c]])}")
    print(f"  only from an LCD page         : "
          f"{len([c for c in everywhere if sources[c] == {'lcd'}])}")
    print(f"unique in at least one mode     : {len(anywhere_unique)}")
    stuck = sorted(everywhere - anywhere_unique)
    if stuck:
        print(f"never unique in any single mode : "
              f"{', '.join(f'0x{c:02X}' for c in stuck)}")
        for code in stuck:
            partners = set()
            for mode, bound in cat["per_mode"].items():
                if code not in bound:
                    continue
                owners = collections.defaultdict(set)
                for other, keys in bound.items():
                    for k in keys:
                        owners[signature(cat, k)].add(other)
                for k in bound[code]:
                    partners |= owners[signature(cat, k)] - {code}
            named = [p for p in partners if p in anywhere_unique]
            print(f"  0x{code:02X} shares with "
                  f"{', '.join(f'0x{p:02X}' for p in sorted(partners))}"
                  + (f"; recoverable by elimination once "
                     f"{', '.join(f'0x{p:02X}' for p in sorted(named))} "
                     f"{'is' if len(named) == 1 else 'are'} named" if named else ""))
    else:
        print("never unique                    : none")


def unique_keys_of(cat: dict, mode: int, code: int) -> list:
    """The records this code sends in this mode that no other code sends here."""
    owners = collections.defaultdict(set)
    for other, keys in cat["per_mode"][mode].items():
        for key in keys:
            owners[signature(cat, key)].add(other)
    return sorted(k for k in cat["per_mode"][mode].get(code, ())
                  if len(owners[signature(cat, k)]) == 1)


def survives_replay(cat: dict, mode: int, code: int, seed: int = 20260816) -> bool:
    """Being unique on paper is not enough; two records can be a hair apart.

    Replay the record as a capture with worse jitter than a decent receiver and
    require the matcher to hand back this code. Deterministic, so the plan a
    reader gets is the plan that was checked.
    """
    rng = random.Random(seed + code + 1000 * mode)
    for key in unique_keys_of(cat, mode, code):
        stored = cat["signals"][key]["waveforms"]
        if not stored or not stored[0]:
            continue
        noisy = []
        for value in stored[0]:
            size = abs(value)
            if size >= MAX_US:
                noisy.append(value)
                continue
            size += rng.uniform(-0.08, 0.08) * size + rng.uniform(-60, 60)
            size = max(1, int(round(size)))
            noisy.append(size if value > 0 else -size)
        carrier = cat["signals"][key]["carrier_hz"] * rng.uniform(0.99, 1.01)
        ranked = match(cat, noisy, carrier, mode=mode)
        if ranked and code in {s["code"] for s in ranked[0]["senders"]}:
            return True
    return False


def plan(cat: dict) -> None:
    """Fewest mode selections that name every transmitting key, replay checked."""
    unique_here = {mode: unambiguous_in(cat, mode)[1] for mode in cat["per_mode"]}
    targets = set().union(*unique_here.values()) if unique_here else set()

    remaining, steps = set(targets), []
    while remaining:
        best = max(unique_here, key=lambda m: len(unique_here[m] & remaining))
        got = unique_here[best] & remaining
        if not got:
            break
        steps.append([best, got])
        remaining -= got

    # Verify every assignment and move the ones that do not survive.
    moved, unresolved = [], set()
    for step in steps:
        mode, codes = step
        failed = {c for c in codes if not survives_replay(cat, mode, c)}
        step[1] = codes - failed
        for code in sorted(failed):
            elsewhere = sorted(m for m in unique_here
                               if code in unique_here[m] and m != mode
                               and survives_replay(cat, m, code))
            if elsewhere:
                moved.append((code, mode, elsewhere[0]))
            else:
                unresolved.add(code)
    for code, _, destination in moved:
        for step in steps:
            if step[0] == destination:
                step[1] |= {code}
                break
        else:
            steps.append([destination, {code}])

    print("Measurement plan. Select each mode on the remote, then press every key.")
    print("Every line below was checked by replaying the stored record as a noisy")
    print("capture and requiring the matcher to hand back that key.\n")
    for mode, codes in steps:
        if not codes:
            continue
        name = DEVICE_MODES.get(mode, "activity or device")
        print(f"  mode {mode:3}  {name:18}  names {len(codes)} keys")
        ordered = sorted(codes)
        for start in range(0, len(ordered), 12):
            print("      " + " ".join(f"0x{c:02X}" for c in ordered[start:start + 12]))
    total = sum(len(c) for _, c in steps)
    print(f"\n{total} keys in {len([s for s in steps if s[1]])} selections.")
    for code, was, now in moved:
        print(f"  0x{code:02X} moved from mode {was} to {now}: too close to another "
              f"record in {was} to survive jitter")
    if unresolved:
        print("  no mode names these: "
              + ", ".join(f"0x{c:02X}" for c in sorted(unresolved)))
    print("\nPass the mode to --match so only that mode's records are considered.")


def selftest(cat: dict, seed: int = 20260816) -> int:
    """Replay every record as a jittered capture and see if the code comes back.

    Scoped per mode, because that is how a measurement actually happens: you
    select an activity and then press keys. Jitter is worse than a decent
    receiver, 8 percent plus 60 microseconds, so this is a floor.
    """
    rng = random.Random(seed)
    total = right = 0
    failures = []
    for mode in sorted(cat["per_mode"]):
        bound, unique = unambiguous_in(cat, mode)
        owners = collections.defaultdict(set)
        for other, keys in cat["per_mode"][mode].items():
            for k in keys:
                owners[signature(cat, k)].add(other)
        for code in sorted(unique):
            # Only the records that are this code's alone here. A key can also
            # send a shared one, and replaying that would be testing nothing.
            for key in sorted(k for k in cat["per_mode"][mode][code]
                              if len(owners[signature(cat, k)]) == 1):
                stored = cat["signals"][key]["waveforms"]
                if not stored or not stored[0]:
                    continue
                noisy = []
                for value in stored[0]:
                    size = abs(value)
                    if size >= MAX_US:
                        noisy.append(value)
                        continue
                    size += rng.uniform(-0.08, 0.08) * size + rng.uniform(-60, 60)
                    size = max(1, int(round(size)))
                    noisy.append(size if value > 0 else -size)
                carrier = cat["signals"][key]["carrier_hz"] * rng.uniform(0.99, 1.01)
                ranked = match(cat, noisy, carrier, mode=mode)
                total += 1
                got = {s["code"] for s in ranked[0]["senders"]}
                if code in got:
                    right += 1
                else:
                    failures.append((mode, code, sorted(got), round(ranked[0]["distance"], 4)))
    print(f"replays: {right} of {total} returned the right key code")
    for mode, code, got, d in failures[:10]:
        print(f"  mode {mode} 0x{code:02X} came back as "
              f"{[hex(g) for g in got]} at distance {d}")
    if failures:
        print("  Those are records that are unique on paper but sit within jitter of")
        print("  another one in the same mode. They are not a problem in themselves;")
        print("  what matters is whether every key still has some mode that works.")

    # The real pass/fail: does every nameable key survive in at least one mode?
    nameable, covered = set(), set()
    for mode in cat["per_mode"]:
        unique = unambiguous_in(cat, mode)[1]
        nameable |= unique
        covered |= {code for code in unique if survives_replay(cat, mode, code)}
    missing = sorted(nameable - covered)
    print(f"coverage: {len(covered)} of {len(nameable)} nameable keys survive "
          f"in at least one mode")
    if missing:
        print("  no working mode for: " + ", ".join(f"0x{c:02X}" for c in missing))
    return 0 if not missing else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?", type=Path, default=_paths.SAMPLE_BLOB)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--build", action="store_true", help="write the catalogue as JSON")
    parser.add_argument("--match", type=Path, help="a capture file, or - for stdin")
    parser.add_argument("--mode", type=int, help="restrict matching to one mode")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--top", type=int, default=4)
    args = parser.parse_args()

    blob = _paths.get_blob(args.config)
    cat = catalogue(blob)

    if args.plan:
        plan(cat)
        return 0
    if args.selftest:
        return selftest(cat)
    if args.build:
        out = {"records": [
            {"group": g, "command": c, "carrier_hz": s["carrier_hz"],
             "nec": list(s["nec"]) if s["nec"] else None,
             "waveforms": s["waveforms"], "sent_by": cat["senders"].get((g, c), [])}
            for (g, c), s in sorted(cat["signals"].items())]}
        text = json.dumps(out, indent=1)
        if args.output:
            args.output.write_text(text, encoding="utf-8")
            print(f"wrote {args.output}, {len(out['records'])} records")
        else:
            print(text)
        return 0
    if not args.match:
        report(cat)
        return 0

    text = sys.stdin.read() if str(args.match) == "-" else args.match.read_text(encoding="utf-8")
    capture, carrier = read_capture(text)
    print(f"capture: {len(prepare(capture))} transitions"
          + (f", carrier {carrier:.0f} Hz" if carrier else ", no carrier given")
          + (f", restricted to mode {args.mode}" if args.mode is not None else
             ", every mode considered (pass --mode to narrow it)"))
    ranked = match(cat, capture, carrier, mode=args.mode)
    if not ranked:
        print("no records to match against; is that mode number right?")
        return 1
    for result in ranked[:args.top]:
        group, command = result["key"]
        flag = "" if result["carrier_ok"] is not False else "   CARRIER MISMATCH"
        print(f"\ndistance {result['distance']:.4f}  group {group} command {command}"
              f"  {result['carrier_hz']:.0f} Hz{flag}")
        if result["nec"]:
            print("      NEC " + " ".join(f"{v:02X}" for v in result["nec"]))
        for code in sorted({s["code"] for s in result["senders"]}):
            modes = sorted({s["mode"] for s in result["senders"] if s["code"] == code})
            kinds = sorted({s["source"] for s in result["senders"] if s["code"] == code})
            print(f"      key code 0x{code:02X}  {matrix_position(code)}"
                  f"  via {'/'.join(kinds)} in mode(s) {modes[:6]}"
                  + (" ..." if len(modes) > 6 else ""))
        if not result["senders"]:
            print("      no key sends this record in the modes considered")
    if len(ranked) > 1:
        best, second = ranked[0], ranked[1]
        gap = second["distance"] - best["distance"]
        print(f"\nmargin over the runner up: {gap:.4f}")
        rival = {s["code"] for s in second["senders"]} - {s["code"] for s in best["senders"]}
        if rival and second["distance"] < best["distance"] * 1.5:
            print("That is close, and the runner up is a different key "
                  f"({', '.join(f'0x{c:02X}' for c in sorted(rival))}). Capture again, "
                  "or use --plan to find a mode where this key is on its own.")
    if ranked[0]["distance"] > 0.25:
        print("Nothing matched well. Check the units are microseconds and that the "
              "remote was in the mode you passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
