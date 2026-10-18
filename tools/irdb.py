"""irdb files as signals flipper_ir can send.

irdb (github.com/probonopd/irdb, Simon Peter and contributors) keeps a remote
as a CSV of `functionname,protocol,device,subdevice,function`, the protocol
named as IrScrutinizer and MakeHex name it. This module turns each row into
the parsed signal flipper_ir encodes, so a file from irdb goes through the same
key proposal and the same commands as one from Flipper-IRDB.

Contains/accesses irdb by Simon Peter and contributors, used under permission.
For licensing details and for information on how to contribute to the
database, see https://github.com/probonopd/irdb
"""
from __future__ import annotations

import csv
import io

import _paths  # noqa: F401


def _signal(name: str, protocol: str, dev: int, sub: int, fn: int) -> dict:
    p = protocol.strip().lower().replace(" ", "")
    s = sub if sub >= 0 else None
    if p in ("nec", "nec1", "nec2"):
        if s is None:
            return {"protocol": "NEC", "address": dev, "command": fn}
        return {"protocol": "NECext", "address": dev | s << 8, "command": fn | (~fn & 0xFF) << 8}
    if p in ("nec1-f16", "nec2-f16"):
        return {"protocol": "NECext", "address": dev | (s if s is not None else ~dev & 0xFF) << 8,
                "command": fn & 0xFFFF}
    if p in ("necx1", "necx2"):
        return {"protocol": "NECx", "address": dev | (s if s is not None else dev) << 8, "command": fn}
    if p == "rc5":
        return {"protocol": "RC5X" if fn >= 64 else "RC5", "address": dev, "command": fn}
    if p == "rc6":
        return {"protocol": "RC6", "address": dev, "command": fn}
    if p == "sony12":
        return {"protocol": "SIRC", "address": dev, "command": fn}
    if p == "sony15":
        return {"protocol": "SIRC15", "address": dev, "command": fn}
    if p == "sony20":
        return {"protocol": "SIRC20", "address": dev | (s or 0) << 5, "command": fn}
    if p == "panasonic":
        return {"protocol": "Panasonic", "address": dev | (s or 0) << 8, "command": fn}
    raise ValueError(f"protocol {protocol} is not supported yet")


def parse(text: str) -> tuple[list[dict], list[str]]:
    """(signals in file order as flipper_ir takes them, rows skipped with why)."""
    signals, skipped = [], []
    for row in csv.DictReader(io.StringIO(str(text))):
        name = (row.get("functionname") or "").strip()
        try:
            dev, sub, fn = (int(row.get(k, "").strip()) for k in ("device", "subdevice", "function"))
            sig = _signal(name, row.get("protocol") or "", dev, sub, fn)
        except ValueError as e:
            skipped.append(f"{name}: {e}")
            continue
        signals.append({"name": name, "type": "parsed", **sig})
    return signals, skipped
