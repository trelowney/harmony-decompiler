"""Check pronto.py against what the remote is known to send.

The Samsung power code in its usual Pronto form (the one RemoteCentral and
many forums carry for Samsung televisions) is parsed and decoded to bits with
verify_flipper_ir's decoder, and those bits must be among the frames the
Samsung T200HD records of the composed sample send, which were confirmed on
the owner's monitor (FORMAT 5af). A scrambled code must not match. The carrier
word 006D must give 38 kHz, and the RC5 form must equal flipper_ir's RC5.

    python verify_pronto.py
"""
from __future__ import annotations

import pathlib

import _paths  # noqa: F401
import flipper_ir
import pronto
from verify_flipper_ir import group_bits, pulse_distance_bits

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMSUNG_POWER = (
    "0000 006D 0000 0022 00AC 00AB 0015 0041 0015 0041 0015 0041 0015 0016 0015 0016 0015 0016 "
    "0015 0016 0015 0016 0015 0041 0015 0041 0015 0041 0015 0016 0015 0016 0015 0016 0015 0016 "
    "0015 0016 0015 0016 0015 0041 0015 0016 0015 0016 0015 0016 0015 0016 0015 0016 0015 0016 "
    "0015 0041 0015 0016 0015 0041 0015 0041 0015 0041 0015 0041 0015 0041 0015 0041 0015 0689")


def main() -> int:
    raw = (ROOT / "samples/harmony525/composed-2026-10-18.EZHex").read_bytes()
    ours = group_bits(raw, "Samsung", (4000, 5000))
    p = pronto.parse(SAMSUNG_POWER)
    bits = pulse_distance_bits(p["repeat"], (4000, 5000))
    w = SAMSUNG_POWER.split()
    w[11], w[13] = w[13], w[11]                 # a 1 and a 0 swapped (spaces 0041, 0016)
    assert w[11] != w[13]
    wrong = pulse_distance_bits(pronto.parse(" ".join(w))["repeat"], (4000, 5000))
    rc5 = pronto.parse("5000 0073 0000 0001 0000 000C")["signal"]
    checks = [
        ("006D is 38 kHz", abs(p["carrier"] - 38_000) < 200),
        ("the Samsung power code is a frame the remote sends", len(bits) == 1 and bits[0] in ours),
        ("a scrambled code is not", len(wrong) == 1 and wrong[0] not in ours),
        ("RC5 5000 form is system 0, command 12",
         flipper_ir.frame(rc5["protocol"], rc5["address"], rc5["command"]) == flipper_ir.frame("RC5", 0, 12)),
        ("a command builds", bool(pronto.command("Power", SAMSUNG_POWER, repeat=True).streams[1])),
    ]
    for text, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'} {text}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
