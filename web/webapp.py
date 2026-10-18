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
        activities = [a.__dict__ for a in compose525.activities(raw)]
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


def op_delete_activity(container: bytes, name: str) -> bytes:
    return compose525.delete_activity(bytes(container), str(name))


def _py(value):
    """A JavaScript object handed in by the page, as plain Python."""
    return value.to_py() if hasattr(value, "to_py") else value


def op_add_activity(container: bytes, name: str, like: str, on) -> bytes:
    return compose525.add_activity(bytes(container), str(name), str(like), [str(x) for x in _py(on)])


def op_add_device(container: bytes, name: str, address: int, commands, keys, lcd) -> bytes:
    """commands: [{name, code, repeat}], NEC on `address`; keys: {key name: command
    name}; lcd: four [label, command name] or null, in reading order."""
    commands, keys, lcd = _py(commands), _py(keys), _py(lcd)
    cmds = [compose525.nec_command(str(c["name"]), int(address), int(c["code"]),
                                   repeat=bool(c.get("repeat")))
            for c in commands]
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
