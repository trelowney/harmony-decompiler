"""Check irdb.py's protocol mapping against what the remote is known to send.

Every Panasonic and NECx row in the irdb files given is encoded by flipper_ir
and decoded to bits with verify_flipper_ir's decoder, which takes nothing from
either; those bits must be among the frames the composed sample's Panasonic TV
(Logitech's own records) and Samsung T200HD (confirmed on the owner's monitor)
send. A layout with one byte's bits reversed must lose the matches. NEC rows
must equal compose525.nec_frame, which wrote the X96 and the PC.

    python verify_irdb.py <irdb checkout>
"""
from __future__ import annotations

import pathlib
import sys

import _paths  # noqa: F401
import compose525
import flipper_ir
import irdb
from verify_flipper_ir import group_bits, pulse_distance_bits

ROOT = pathlib.Path(__file__).resolve().parents[1]


def irdb_bits(db: pathlib.Path, pattern: str, protocol: str, header, scramble=False) -> dict:
    out = {}
    for path in sorted(db.glob(pattern)):
        signals, _ = irdb.parse(path.read_text(encoding="utf-8", errors="replace"))
        for s in signals:
            if s["protocol"] != protocol:
                continue
            f = flipper_ir.frame(protocol, s["address"], s["command"])
            if scramble:
                f = f[:2] + f[2:18][::-1] + f[18:]
            for b in pulse_distance_bits(f, header):
                out.setdefault(b, f"{path.parent.name}/{path.name}:{s['name']}")
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    db = pathlib.Path(sys.argv[1]) / "codes"
    raw = (ROOT / "samples/harmony525/composed-2026-10-18.EZHex").read_bytes()
    failed = 0
    for name, pattern, protocol, header in (
            ("TV Panason", "Panasonic/*/*.csv", "Panasonic", (3000, 3900)),
            ("Samsung", "Samsung/*/*.csv", "NECx", (4000, 5000))):
        ours = group_bits(raw, name, header)
        theirs = irdb_bits(db, pattern, protocol, header)
        hit = sorted(set(theirs) & ours)
        wrong = set(irdb_bits(db, pattern, protocol, header, scramble=True)) & ours
        ok = len(hit) >= 5 and len(wrong) < len(hit) / 4
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'} {protocol}: {len(ours)} distinct frames on the remote, "
              f"{len(theirs)} from irdb, {len(hit)} identical (scrambled layout: {len(wrong)}); "
              f"e.g. {[theirs[b] for b in hit[:3]]}")
    bad = 0
    for a in range(0, 256, 9):
        for f in range(0, 256, 13):
            s = irdb._signal("x", "NEC1", a, -1, f)
            ours = [w & 0x7FFF for w in compose525.nec_frame(a, f)]
            mine = flipper_ir.frame(s["protocol"], s["address"], s["command"])
            bad += not (len(ours) == len(mine) and all(abs(p - q) < 150 for p, q in zip(ours, mine)))
    failed += bool(bad)
    print(f"{'PASS' if not bad else 'FAIL'} NEC1 rows equal compose525.nec_frame ({bad} differ)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
