"""Reproduce FORMAT.md 5q: how a duration block is spelt, on both architectures.

5q was published while the arch 8 half of it could not be recomputed from this
repository - the reader it needed lived outside. `arch8_ir_duration_reader.py`
is here now, and this joins it to the arch 9 side, which has always used
`class5_ir_encoder.py`.

Every long run - one whose duration exceeds what a single word can hold - is
put in exactly one class, and the check is that **nothing is left over**:

    literal          the rule as @dannybloe stated it
    sentinel         the rule applied to total - 1, then a separate word 1
    lead + sentinel  a leading word set aside, then the above on the rest

Usage:
    python tools/verify_ir_spelling.py
    python tools/verify_ir_spelling.py --negative
"""
from __future__ import annotations

import argparse
import collections
from pathlib import Path

import _paths  # noqa: F401
import class5_ir_encoder
import hconfig
from arch8_ir_duration_reader import read_arch8_ir

MAXIMUM = class5_ir_encoder.IR_DURATION_MAX
MARK = class5_ir_encoder.IR_MARK
REPO_ROOT = Path(__file__).resolve().parent.parent


def spell(total: int, maximum: int = MAXIMUM) -> list[int]:
    """@dannybloe's rule: maximal words, remainder balanced across the last two.

    Balancing happens only when the remainder would otherwise fall below half
    the maximum, which is what "no word falls below half" means. Pinned by the
    three values his own measurement pinned it with, in the negative check.
    """
    if total <= maximum:
        return [total]
    words = -(-total // maximum)
    full = words - 1
    last = total - full * maximum
    if full >= 1 and last * 2 < maximum:
        rest = last + maximum
        half = rest // 2
        return [maximum] * (full - 1) + [half, rest - half]
    return [maximum] * full + [last]


def word_runs(words):
    """Consecutive words of one polarity, which together spell one duration."""
    runs: list[tuple[bool, list[int]]] = []
    for word in words:
        mark = bool(word & MARK)
        duration = word & MAXIMUM
        if runs and runs[-1][0] == mark:
            runs[-1][1].append(duration)
        else:
            runs.append((mark, [duration]))
    return runs


def classify(stored: list[int]) -> tuple[str, int | None]:
    """Which spelling produced this run, and the leading word if one was set aside."""
    total = sum(stored)
    if total <= MAXIMUM:
        return "short", None
    if stored == spell(total):
        return "literal", None
    if stored[-1] == 1 and stored[:-1] == spell(total - 1):
        return "sentinel", None
    if len(stored) > 2 and stored[-1] == 1 and stored[1:-1] == spell(total - stored[0] - 1):
        return "lead + sentinel", stored[0]
    return "unexplained", None


def arch9_runs():
    raw = (REPO_ROOT / "samples" / "harmony525" / "config.EZHex").read_bytes()
    _xml, _sep, blob = hconfig.split_container(raw)
    doc = hconfig.decompile(raw)
    base = int(doc["blob"]["config_base"], 0)
    groups = [r for r in doc["blob"]["regions"] if r["kind"] == "ir_group"]
    for group in sorted(groups, key=lambda r: r["offset"]):
        for offset in group["targets"]:
            record = class5_ir_encoder.decode_class5_record(
                blob, base_address=base, record_address=base + offset)
            for stream in record.pointer_streams:
                if stream:
                    yield from word_runs(stream)


def arch8_runs():
    for path in sorted((REPO_ROOT / "samples" / "arch8").glob("*.EZHex")):
        raw = path.read_bytes()
        _xml, _sep, blob = hconfig.split_container(raw)
        database = read_arch8_ir(blob, hconfig.decompile(raw, filename=str(path)))
        for record in database.records:
            for block in record.blocks:
                if block:
                    yield from word_runs(block.words)


def tally(runs):
    counts: collections.Counter[str] = collections.Counter()
    leads: collections.Counter[int] = collections.Counter()
    leftovers = []
    for _mark, stored in runs:
        kind, lead = classify(stored)
        if kind == "short":
            continue
        counts[kind] += 1
        if lead is not None:
            leads[lead] += 1
        if kind == "unexplained":
            leftovers.append(stored)
    return counts, leads, leftovers


# A bare leading word with no sentinel was tried as a fourth class and never
# fired on either architecture, so it is not offered. Leaving it in would have
# accepted any two-word run whose second word fits one word, which is most of
# them - a class that explains everything explains nothing.
ORDER = ("literal", "sentinel", "lead + sentinel", "unexplained")


def report(name: str, runs) -> int:
    counts, leads, leftovers = tally(runs)
    total = sum(counts.values())
    print(f"{name}: {total} long runs")
    for kind in ORDER:
        if counts[kind]:
            print(f"  {counts[kind]:6}  {kind}")
    if leads:
        print("  leading words set aside: " +
              ", ".join(f"{value} us x{n}" for value, n in sorted(leads.items())))
    if leftovers:
        print(f"  FAIL {len(leftovers)} runs fit no spelling, first: {leftovers[0]}")
        return 1
    print("  PASS nothing left over")
    return 0


def negative() -> int:
    """The classifier has to be able to say no, and the rule has to be pinned."""
    failures = 0
    for total, want in ((50_000, [32767, 17233]),
                        (40_222, [20111, 20111]),
                        (42_033, [21016, 21017]),
                        (35_101, [17550, 17551]),
                        (96_078, [32767, 32767, 30544])):
        got = spell(total)
        ok = got == want
        failures += not ok
        print(f"  {'ok    ' if ok else 'FAIL  '} spell({total}) = {got}")
    # Each of these has to come back unexplained. Two earlier candidates were
    # dropped because they turned out to be the honest spelling of a different
    # total, which is the trap in writing negative cases by hand.
    cases = [
        ("a maximal word after a short one", [32767, 1000, 32767]),
        ("the same words in a different order", [32767, 30544, 32767]),
        ("a sentinel run with the sentinel changed", [17550, 17550, 2]),
        ("the sentinel moved to the front", [1, 17550, 17550]),
        ("a lead run with the lead moved to the end", [32767, 17938, 17938, 1, 446]),
    ]
    for label, stored in cases:
        kind, _lead = classify(stored)
        ok = kind == "unexplained"
        failures += not ok
        print(f"  {'ok    ' if ok else 'FAIL  '} {label} -> {kind}")
    print(f"\n{failures} negative case(s) failed")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--negative", action="store_true",
                        help="pin the rule and require the classifier to refuse")
    args = parser.parse_args()
    if args.negative:
        print("negative check")
        return negative()
    bad = report("arch 9, the 525", arch9_runs())
    bad += report("arch 8, 13 samples", arch8_runs())
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
