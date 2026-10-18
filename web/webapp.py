"""The Python half of the web page, run in the browser by Pyodide.

It reads a configuration with the same modules the command line uses
(tools/), so the page and the tests agree by construction.
"""
import hashlib

import compose525
import hconfig
import render_525_screens as renderer
import verify_525_semantics as sem
from clone_525_device import mode_fields

_TEMPLATE = None


def set_template(container: bytes) -> None:
    """The public sample's .EZHex, whose XML header a dumped blob is wrapped in."""
    global _TEMPLATE
    _TEMPLATE = hconfig.split_container(bytes(container))


def ezhex_from_blob(blob: bytes) -> bytes:
    """Wrap a blob read off a remote the way concordance --dump-config does: the
    header with BINARYDATASIZE and CHECKSUM recomputed."""
    xml, sep, _ = _TEMPLATE
    blob = bytes(blob)
    xml = hconfig._set_tag(xml, "BINARYDATASIZE", len(blob))
    xml = hconfig._set_tag(xml, "CHECKSUM", hconfig.blob_checksum(blob))
    return xml + sep + blob


def blob_of(container: bytes) -> bytes:
    """The bytes that go to flash: the container without its XML header, after
    the checks a write needs. Refuses rather than hands back a file that would
    not load."""
    raw = bytes(container)
    xml, _, blob = hconfig.split_container(raw)
    if blob[:4] != b"AHCM":
        raise ValueError("not an arch 9 (Harmony 525) configuration")
    if int.from_bytes(blob[-6:-4], "little") != hconfig.trailer_checksum(blob):
        raise ValueError("the configuration's own checksum is wrong")
    if hconfig.compile_config(hconfig.decompile(raw, "write")) != raw:
        raise ValueError("the configuration does not read back byte for byte")
    for tag in (b"<PROTOCOL>9</PROTOCOL>", b"<SKIN>22</SKIN>"):
        if tag not in xml:
            raise ValueError(f"the file is not for this remote: no {tag.decode()}")
    return blob


def summary(container: bytes) -> dict:
    raw = bytes(container)
    blob = hconfig.split_container(raw)[2]
    trailer_ok = int.from_bytes(blob[-6:-4], "little") == hconfig.trailer_checksum(blob)
    roundtrip_ok = hconfig.compile_config(hconfig.decompile(raw, "page")) == raw
    devices = [d.__dict__ for d in compose525.devices(raw)]
    try:
        activities = [dict(a.__dict__) for a in compose525.activities(raw)]
        for a in activities:
            a["does"] = " · ".join(_step_text(s) for s in compose525.describe_activity(raw, a["name"]))
    except compose525.ComposeError:
        activities = []
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blob_bytes": len(blob),
        "trailer_ok": trailer_ok,
        "roundtrip_ok": roundtrip_ok,
        "devices": devices,
        "activities": activities,
    }


def screens(container: bytes, modes) -> list:
    """Rendered pages of the given modes: (mode, page, 96 x 64 bytes, 1 = ink)."""
    blob = hconfig.split_container(bytes(container))[2]
    S = sem.section_offsets(blob)
    fonts = renderer.fonts(blob, S)
    out = []
    for mode in modes:
        for k, page in enumerate(mode_fields(blob, S, mode)["pages"]):
            canvas, _ = renderer.render_page(blob, fonts, page["program"])
            pixels = bytes(1 if v == renderer.INK else 0 for row in canvas for v in row)
            out.append((mode, k, pixels))
    return out


def key_names() -> list:
    """The 525's keys by name, for the page's key map."""
    return list(compose525.KEYS)


def power_devices(container: bytes) -> list:
    """Devices an activity can switch on: the ones with a power variable."""
    return sorted(compose525.power_variables(bytes(container)))


def op_delete_device(container: bytes, mode: int) -> bytes:
    return compose525.delete_device(bytes(container), int(mode))


def op_place_device(container: bytes, mode: int, page: int, corner: int) -> bytes:
    return compose525.place_device(bytes(container), int(mode), int(page), int(corner))


def op_place_activity(container: bytes, name: str, page: int, corner: int) -> bytes:
    return compose525.place_activity(bytes(container), str(name), int(page), int(corner))


def op_delete_activity(container: bytes, name: str) -> bytes:
    return compose525.delete_activity(bytes(container), str(name))


def _py(value):
    """A JavaScript object handed in by the page, as plain Python."""
    return value.to_py() if hasattr(value, "to_py") else value


def op_add_activity(container: bytes, name: str, like: str, on, steps=None) -> bytes:
    """steps: [[group, command], ...] sent after the switching, in order. With
    steps the source activity's own inputs are not copied: the steps say it."""
    steps = [tuple(int(v) for v in s) for s in (_py(steps) or [])]
    return compose525.add_activity(bytes(container), str(name), str(like),
                                   [str(x) for x in _py(on)],
                                   writes={} if steps else None, steps=steps)


def commands(container: bytes) -> list:
    """Every named command: [{device, label, group, command}], for the page's
    activity steps."""
    out = [{"device": dev, "label": label, "group": g, "command": cmd}
           for (g, cmd), (dev, label) in compose525.device_commands(bytes(container)).items()]
    return sorted(out, key=lambda c: (c["device"], c["label"]))


def _step_text(s: dict) -> str:
    if s["kind"] == "power":
        return f"{s['device']} {'on' if s['on'] else 'off'}"
    if s["kind"] == "send":
        return f"{s['device']}: {s['label']}"
    return f"variable {s['variable']} = {s['value']}"


def decode_capture(durations) -> dict:
    """A capture learned through the remote, read as NEC if it is NEC.
    durations: microseconds, mark first. Returns {nec: [a, a2, cmd]} or {}."""
    d = [int(x) for x in _py(durations)]
    for start in range(0, max(0, len(d) - 66), 2):
        if 8000 < d[start] < 10000 and 4000 < d[start + 1] < 5000:
            bits = []
            for k in range(32):
                mark, space = d[start + 2 + 2 * k], d[start + 3 + 2 * k]
                if not (300 < mark < 900):
                    break
                bits.append(1 if space > 1100 else 0)
            if len(bits) == 32:
                b = [sum(bits[8 * n + i] << i for i in range(8)) for n in range(4)]
                if b[2] ^ b[3] == 0xFF:
                    return {"nec": [b[0], b[1], b[2]]}
    return {}


def _raw_command(name: str, carrier: int, durations, repeat: bool):
    """A learned capture played back as it was heard, Logitech's 500 ms (or,
    repeating, 50 ms) lead in front, the capture's own last gap after it."""
    words = []
    for i, us in enumerate(int(x) for x in durations):
        us = max(1, us)
        if i % 2 == 0:
            words.append(0x8000 | min(us, 0x7FFF))
        else:
            words.extend(compose525._spaces(us))
    words = tuple(words) + (1,)
    lead = compose525._spaces(50_000 if repeat else 500_000)
    first = lead + words
    held = words if repeat else None
    period = round(1e9 / carrier) if carrier else 26_315
    return compose525.Command(name, (first, held, None), period_ns=period)


def op_add_device(container: bytes, name: str, address: int, commands, keys, lcd) -> bytes:
    """commands: [{name, code, repeat}] (NEC on `address`), or with `address`
    and `address2` of their own (learned NEC), or {name, carrier, durations,
    repeat} (a learned capture that is not NEC); keys: {key name: command name};
    lcd: four [label, command name] or null, in reading order."""
    commands, keys, lcd = _py(commands), _py(keys), _py(lcd)
    cmds = []
    for c in commands:
        if c.get("durations"):
            cmds.append(_raw_command(str(c["name"]), int(c.get("carrier") or 0), c["durations"],
                                     bool(c.get("repeat"))))
        else:
            a = int(c["address"]) if c.get("address") is not None else int(address)
            a2 = int(c["address2"]) if c.get("address2") is not None else None
            cmds.append(compose525.nec_command(str(c["name"]), a, int(c["code"]),
                                               repeat=bool(c.get("repeat")), address_high=a2))
    key_map = {str(k): str(v) for k, v in dict(keys).items() if v}
    page = [None if item is None or not item[0] else (str(item[0]), str(item[1])) for item in lcd]
    if not any(page):
        raise compose525.ComposeError("give the device at least one soft key")
    out, _ = compose525.add_device(bytes(container), str(name), cmds, key_map, [page])
    return out


def screen_modes(container: bytes) -> list:
    """The modes worth showing: the Devices screen, each device, the activity menu."""
    raw = bytes(container)
    d = compose525._Doc(raw)
    modes = [d.idle_devices_mode()]     # the Devices screen with no activity running
    modes += sorted({x.mode for x in compose525.devices(raw)})
    try:
        modes.append(compose525._menu_mode(d))
    except compose525.ComposeError:
        pass
    return modes
