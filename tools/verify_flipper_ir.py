"""Check flipper_ir's encoders against signals the 525 is known to send.

The composed sample (samples/harmony525/composed-2026-10-18.EZHex) holds a
Panasonic TV, Kaseikyo, learned by Logitech, and a Samsung T200HD whose codes
came from a public database and were confirmed on the owner's monitor
(FORMAT 5af). Every frame in those records is decoded to bits here, with
nothing taken from flipper_ir; then every Kaseikyo and Samsung32 signal in the
Flipper-IRDB files given is encoded by flipper_ir and decoded the same way.
The check: the bit strings the database gives for the same buttons must be
among the remote's own, and they must be found by protocol layout, not by
chance (a negative: flipping one bit of the layout must lose the matches).

    python verify_flipper_ir.py <Flipper-IRDB checkout>

Also checks the NEC encoder against compose525.nec_frame, which wrote the X96
and PC devices the remote sends.
"""
from __future__ import annotations

import pathlib
import sys

import _paths  # noqa: F401
import analyze_525_ir as ir
import compose525
import flipper_ir
import hconfig
import verify_525_semantics as sem

ROOT = pathlib.Path(__file__).resolve().parents[1]


def pulse_distance_bits(us: list[int], header_mark: tuple[int, int]) -> list[str]:
    """Every frame that starts with a mark in `header_mark` (min, max): its bits
    by space length, split at the median of the frame's spaces."""
    out = []
    for k in range(len(us) - 3):
        if not (k % 2 == 0 and header_mark[0] <= us[k] <= header_mark[1]):
            continue
        bits, at = [], k + 2
        while at + 1 < len(us) and us[at] < 1000 and us[at + 1] < 2500:
            bits.append(us[at + 1])
            at += 2
        if len(bits) >= 24:
            cut = (min(bits) + max(bits)) / 2
            out.append("".join("1" if s > cut else "0" for s in bits))
    return out


def record_durations(blob: bytes, address: int) -> list[int]:
    """All pointer streams of a record as alternating mark/space durations."""
    rec = ir.record(blob, address)
    us = []
    for p in rec["pointers"]:
        for w in ir.body(blob, p)["words"]:
            mark, v = bool(w & 0x8000), w & 0x7FFF
            if (len(us) % 2 == 0) == mark:
                us.append(v)
            elif us:
                us[-1] += v
    return us


def group_bits(raw: bytes, name: str, header: tuple[int, int]) -> set[str]:
    blob = hconfig.split_container(raw)[2]
    S = sem.section_offsets(blob)
    dev = [d for d in compose525.devices(raw) if d.name.startswith(name)]
    assert len(dev) == 1, name
    out = set()
    for address in ir.ir_groups(blob, S)[dev[0].group]:
        for b in pulse_distance_bits(record_durations(blob, address), header):
            out.add(b)
    return out


def flipper_bits(db: pathlib.Path, pattern: str, protocol: str, header, layout=None) -> dict:
    out = {}
    for path in sorted(db.glob(pattern)):
        for s in flipper_ir.parse(path.read_text(encoding="utf-8", errors="replace")):
            if s.get("protocol") != protocol:
                continue
            f = (layout or flipper_ir.frame)(protocol, s["address"], s["command"])
            for b in pulse_distance_bits(f, header):
                out.setdefault(b, f"{path.name}:{s['name']}")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    db = pathlib.Path(sys.argv[1])
    raw = (ROOT / "samples/harmony525/composed-2026-10-18.EZHex").read_bytes()
    failed = 0

    for name, pattern, protocol, header in (
            ("TV Panason", "TVs/Panasonic/*.ir", "Kaseikyo", (3000, 3900)),
            ("Samsung", "TVs/Samsung/*.ir", "Samsung32", (4000, 5000))):
        ours = group_bits(raw, name, header)
        theirs = flipper_bits(db, pattern, protocol, header)
        hit = sorted(set(theirs) & ours)
        # negative: the same files through a layout with one byte's order wrong
        def broken(p, a, c):
            f = flipper_ir.frame(p, a, c)
            return f[:2] + f[2:18][::-1] + f[18:]
        wrong = set(flipper_bits(db, pattern, protocol, header, broken)) & ours
        ok = len(hit) >= 5 and len(wrong) < len(hit) / 4
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {protocol}: {len(ours)} distinct frames on the remote, "
              f"{len(theirs)} from the database, {len(hit)} identical "
              f"(scrambled layout: {len(wrong)}); e.g. {[theirs[b] for b in hit[:3]]}")

    # NEC and NECext against the encoder that wrote the X96 and the PC
    bad = 0
    for a in range(0, 256, 7):
        for c in range(0, 256, 11):
            ours = [w & 0x7FFF for w in compose525.nec_frame(a, c)]
            mine = flipper_ir.frame("NEC", a, c)
            ext = flipper_ir.frame("NECext", a | (~a & 0xFF) << 8, c | (~c & 0xFF) << 8)
            same = lambda x, y: len(x) == len(y) and all(abs(p - q) < 150 for p, q in zip(x, y))
            bad += not (same(ours, mine) and same(ours, ext))
    failed += bool(bad)
    print(f"{'PASS' if not bad else 'FAIL'} NEC and NECext frames equal compose525.nec_frame "
          f"({bad} differ)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
