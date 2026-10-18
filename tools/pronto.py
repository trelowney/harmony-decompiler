"""Pronto hex codes as 525 commands.

Pronto hex is how infrared codes are passed around on forums and in older
databases (RemoteCentral and the like): words of four hex digits. The first
word says what follows:

    0000 learned:  frequency word, once pairs, repeat pairs, then the pairs,
                   each a mark and a space in carrier periods
    0100 the same, unmodulated (sent here at 38 kHz)
    5000 RC5:      0073 0000 0001, system, command
    5001 RC5X:     likewise, command up to 127
    6000 RC6 mode 0: 0073 0000 0002, system, command

The carrier is 1,000,000 / (frequency word * 0.241246) Hz (Philips' Pronto
clock). A learned code's once part is sent once, its repeat part while the key
is held; the RC5 and RC6 forms go through flipper_ir's encoders.
"""
from __future__ import annotations

import re

import _paths  # noqa: F401
import flipper_ir
from compose525 import Command, _spaces

PRONTO_CLOCK = 0.241246            # microseconds per frequency word unit


class ProntoError(ValueError):
    """Text that is not a Pronto code this module can send."""


def words(text: str) -> list[int]:
    parts = re.findall(r"[0-9A-Fa-f]{4}", str(text))
    if len(parts) < 4 or len(parts) * 4 != len(re.sub(r"\s", "", str(text))):
        raise ProntoError("a Pronto code is groups of four hex digits, at least four of them")
    return [int(p, 16) for p in parts]


def looks_like(text: str) -> bool:
    try:
        return words(text)[0] in (0x0000, 0x0100, 0x5000, 0x5001, 0x6000)
    except ProntoError:
        return False


def _us(pairs: list[int], period_us: float) -> list[int]:
    return [max(1, round(n * period_us)) for n in pairs]


def parse(text: str) -> dict:
    """{carrier, once, repeat}, durations in microseconds, mark first; or for
    RC5/RC6 a flipper_ir signal under `signal`."""
    w = words(text)
    kind = w[0]
    if kind in (0x0000, 0x0100):
        freq, n_once, n_rep = w[1], w[2], w[3]
        body = w[4:]
        if len(body) != 2 * (n_once + n_rep) or n_once + n_rep == 0:
            raise ProntoError(f"the header says {n_once} + {n_rep} pairs and {len(body) // 2} follow")
        carrier = 38_000 if kind == 0x0100 or not freq else round(1e6 / (freq * PRONTO_CLOCK))
        period = 1e6 / carrier
        return {"carrier": carrier, "once": _us(body[:2 * n_once], period),
                "repeat": _us(body[2 * n_once:], period)}
    if kind in (0x5000, 0x5001, 0x6000):
        if len(w) < 6:
            raise ProntoError("an RC5 or RC6 Pronto code ends with its system and command")
        system, command = w[-2], w[-1]
        protocol = {0x5000: "RC5", 0x5001: "RC5X", 0x6000: "RC6"}[kind]
        return {"signal": {"type": "parsed", "protocol": protocol, "address": system, "command": command}}
    raise ProntoError(f"Pronto codes of kind {kind:04X} are not supported; 0000, 0100, 5000, 5001 and 6000 are")


def _words_of(durations: list[int]) -> tuple:
    out = []
    for i, us in enumerate(durations):
        if i % 2 == 0:
            out.append(0x8000 | min(us, 0x7FFF))
        else:
            out.extend(_spaces(us))
    return tuple(out)


def command(name: str, text: str, repeat: bool = False, repeat_delay_ms: int = 450) -> Command:
    """A 525 command for a Pronto code, laid out like compose525.nec_command:
    one-shot after Logitech's 500 ms lead; repeating after a 50 ms lead, a
    pause before the held block, then the repeat part (or the once part again)
    while the key is held."""
    p = parse(text)
    if "signal" in p:
        return flipper_ir.command(name, p["signal"], repeat, repeat_delay_ms)
    once, rep = p["once"] or p["repeat"], p["repeat"] or p["once"]
    period = round(1e9 / p["carrier"])
    if not repeat:
        return Command(name, (_spaces(500_000) + _words_of(once) + (1,), None, None), period_ns=period)
    first = _spaces(50_000) + _words_of(once[:-1]) + _spaces(repeat_delay_ms * 1000) + (1,)
    return Command(name, (first, _words_of(rep) + (1,), None), period_ns=period)
