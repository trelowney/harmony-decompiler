#!/usr/bin/env python3
"""Offline robustness checks for :mod:`ir_keymap_oracle`.

These checks use only the public Harmony 525 sample and synthetic captures.
They do not open a serial port, contact a remote, or write any artifact.
"""

from __future__ import annotations

import collections
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _paths
import ir_keymap_oracle as oracle


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def jitter(waveform: list[int], rng: random.Random,
           relative: float = 0.06, absolute_us: float = 40.0) -> list[int]:
    out = []
    for value in waveform:
        size = abs(value)
        if size >= oracle.MAX_US:
            out.append(value)
            continue
        size += rng.uniform(-relative, relative) * size
        size += rng.uniform(-absolute_us, absolute_us)
        size = max(1, int(round(size)))
        out.append(size if value > 0 else -size)
    return out


def parser_checks() -> int:
    checks = 0

    capture, carrier = oracle.read_capture("9000, 4500, 560, 560")
    require(capture == [9000, -4500, 560, -560],
            "unsigned captures must alternate from a mark")
    require(carrier is None, "carrier must stay unknown when omitted")
    checks += 2

    capture, carrier = oracle.read_capture(
        "+9000 -4500 +560 -560 # ignored 123\ncarrier=37.9")
    require(capture == [9000, -4500, 560, -560],
            "signed captures and comments must be preserved")
    require(carrier == 37900.0, "a named kHz carrier must become hertz")
    checks += 2

    capture, carrier = oracle.read_capture(
        "$38 9000 4500 560 560\n$38 560 1690")
    require(capture == [9000, -4500, 560, -560, 560, -1690],
            "LearnIR line prefixes must not leak into timings")
    require(carrier == 38000.0, "LearnIR $nn must be interpreted as kHz")
    checks += 2

    try:
        oracle.read_capture("# no timing values")
    except SystemExit as error:
        require(str(error) == "no numbers found in the capture",
                "empty-capture error changed unexpectedly")
    else:
        raise AssertionError("an empty capture must be rejected")
    checks += 1

    return checks


def normalization_checks() -> int:
    checks = 0
    raw = [-2000, 100, 200, -50, -75, 300, -4000]
    require(oracle.prepare(raw) == [300, -125, 300],
            "leading/trailing gaps and adjacent polarities must normalize")
    checks += 1

    full = [9000, -4500, 560, -560, 560, -1690]
    prefix = full[:2]
    require(oracle.distance(full, full) == 0.0,
            "an exact capture must have zero distance")
    require(oracle.distance(prefix, full) >= 0.60,
            "a truncated capture must not be a perfect prefix match")
    require(oracle.distance(full, prefix) >= 0.60,
            "a short stored record must not explain a longer capture")
    checks += 3

    split = [4500, 4500, -2250, -2250, 280, 280, -560]
    merged = [9000, -4500, 560, -560]
    require(oracle.distance(split, merged) == 0.0,
            "receiver-visible merging must be symmetric")
    checks += 1
    return checks


def corpus_checks() -> tuple[int, int, int]:
    blob = _paths.get_blob(_paths.SAMPLE_BLOB)
    cat = oracle.catalogue(blob)
    require(len(cat["signals"]) == 200, "public 525 record count changed")

    nameable = set()
    unique_by_mode = {}
    for mode in cat["per_mode"]:
        unique = oracle.unambiguous_in(cat, mode)[1]
        unique_by_mode[mode] = unique
        nameable |= unique
    require(len(nameable) == 41, "public 525 nameable-key count changed")

    # A prefix cut at half length must never retain a deceptively good score
    # against its complete source waveform.
    truncations = 0
    for signal in cat["signals"].values():
        for stored in signal["waveforms"]:
            stored = oracle.prepare(stored)
            if len(stored) < 8:
                continue
            cut = stored[:len(stored) // 2]
            require(oracle.distance(cut, stored) >= 0.45,
                    "half capture scored too well against its full waveform")
            truncations += 1

    # Use several independent noise streams. A key passes when at least one
    # mode identifies every tested replay of one of its unique records.
    seeds = range(20260816, 20260822)
    covered = set()
    replay_count = 0
    for code in sorted(nameable):
        for mode, unique in unique_by_mode.items():
            if code not in unique:
                continue
            keys = oracle.unique_keys_of(cat, mode, code)
            mode_passes = False
            for key in keys:
                waveforms = cat["signals"][key]["waveforms"]
                if not waveforms or not waveforms[0]:
                    continue
                all_right = True
                for seed in seeds:
                    rng = random.Random(seed + code + 1000 * mode)
                    capture = jitter(waveforms[0], rng)
                    carrier = cat["signals"][key]["carrier_hz"] * rng.uniform(0.99, 1.01)
                    ranked = oracle.match(cat, capture, carrier, mode=mode)
                    got = {sender["code"] for sender in ranked[0]["senders"]}
                    replay_count += 1
                    if code not in got:
                        all_right = False
                        break
                if all_right:
                    mode_passes = True
                    break
            if mode_passes:
                covered.add(code)
                break
    require(covered == nameable,
            "some nameable key lacks a mode robust across all synthetic replays: "
            + ", ".join(f"0x{code:02X}" for code in sorted(nameable - covered)))
    return len(cat["signals"]), truncations, replay_count


def main() -> int:
    parser_count = parser_checks()
    normalization_count = normalization_checks()
    records, truncations, replays = corpus_checks()
    print(f"parser assertions       : {parser_count}")
    print(f"normalization assertions: {normalization_count}")
    print(f"catalogue records       : {records}")
    print(f"truncated captures      : {truncations} rejected as incomplete")
    print(f"multi-seed replays      : {replays}")
    print("nameable-key coverage   : 41 of 41")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
