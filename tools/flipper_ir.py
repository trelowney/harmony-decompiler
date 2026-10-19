"""Flipper-IRDB files as 525 commands.

Flipper-IRDB (github.com/Lucaslhm/Flipper-IRDB, CC0) holds some nine thousand
remotes as `.ir` files: each button a name and either a parsed signal
(protocol, address, command) or raw durations. This module reads such a file,
turns each signal into the durations an infrared LED has to send, and proposes
which 525 key each button goes on.

The timings and bit layouts are the ones Flipper Zero's own firmware encodes
(flipperdevices/flipperzero-firmware, lib/infrared/encoder_decoder), since that
is what the files' numbers mean: NECext's address is 16 bits sent low byte
first, Kaseikyo's address packs vendor, genres and id, and so on. Two of them
are checked against signals the 525 is known to send: Kaseikyo against the
Panasonic TV of the public sample and Samsung32 against the Samsung T200HD
(tools/verify_flipper_ir.py).
"""
from __future__ import annotations

import re

import _paths  # noqa: F401
from compose525 import Command, _spaces

# protocol -> (carrier Hz, frame period us, frames in one press)
PROTOCOLS = {
    "NEC": (38_000, 110_000, 1), "NECext": (38_000, 110_000, 1),
    "NEC42": (38_000, 110_000, 1), "NEC42ext": (38_000, 110_000, 1),
    # Panasonic's own: 36.7 kHz, and three frames a press, as every Panasonic
    # code Logitech learned on the owner's remote has them (one frame does not
    # switch that TV on)
    "Samsung32": (38_000, 108_000, 1), "Kaseikyo": (36_700, 130_000, 3),
    "RC5": (36_000, 113_778, 1), "RC5X": (36_000, 113_778, 1),
    "RC6": (36_000, 106_667, 1),
    "SIRC": (40_000, 45_000, 3), "SIRC15": (40_000, 45_000, 3), "SIRC20": (40_000, 45_000, 3),
    # two more that irdb uses and Flipper does not name: NECx (Samsung and its
    # kin, a 4.5 ms header and two free address bytes) and Panasonic (Kaseikyo
    # with Panasonic's vendor code, device, subdevice and function)
    "NECx": (38_000, 108_000, 1), "Panasonic": (36_700, 130_000, 3),
}
# frames for switching on and off, where a television wants the key held: the
# owner's Panasonic takes 14, and Logitech stored its PwrOn and PwrOff so
POWER_FRAMES = {"Kaseikyo": 14, "Panasonic": 14}
_POWER = re.compile(r"(?i)(power|pwr)|on.?off|standby")


class FlipperError(ValueError):
    """A signal this module cannot turn into durations."""


# ------------------------------------------------------------------ parsing ---

def parse(text: str) -> list[dict]:
    """The signals of a Flipper `.ir` file, in file order: {name, type,
    protocol, address, command} or {name, type: raw, frequency, data}."""
    out, cur = [], {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            if line.startswith("#") and cur.get("name"):
                out.append(cur)
                cur = {}
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "name" and cur.get("name"):
            out.append(cur)
            cur = {}
        if key in ("address", "command"):
            cur[key] = int.from_bytes(bytes(int(b, 16) for b in value.split()), "little")
        elif key == "data":
            cur[key] = [int(v) for v in value.split()]
        elif key == "frequency":
            cur[key] = int(value)
        elif key in ("name", "type", "protocol"):
            cur[key] = value
    if cur.get("name"):
        out.append(cur)
    return [s for s in out if s.get("type") in ("parsed", "raw")]


# ------------------------------------------------------------------ framing ---

def _pulse_distance(header, bits, mark, zero, one, stop=True):
    d = list(header)
    for b in bits:
        d += [mark, one if b else zero]
    if stop:
        d.append(mark)
    return d


def _lsb(value, n):
    return [value >> i & 1 for i in range(n)]


def _msb(value, n):
    return [value >> (n - 1 - i) & 1 for i in range(n)]


def _bytes_lsb(data):
    return [b for byte in data for b in _lsb(byte, 8)]


def _halves_to_durations(halves):
    """(level, us) halves -> durations, mark first, merged, ending on a mark."""
    merged = []
    for level, us in halves:
        if merged and merged[-1][0] == level:
            merged[-1][1] += us
        else:
            merged.append([level, us])
    while merged and not merged[0][0]:
        merged.pop(0)
    while merged and not merged[-1][0]:
        merged.pop()
    return [us for _, us in merged]


def frame(protocol: str, address: int, command: int) -> list[int]:
    """One frame of a parsed signal, durations in us, mark first, ending on a
    mark; the gap after it is the caller's."""
    p = protocol
    if p == "NEC":
        a, c = address & 0xFF, command & 0xFF
        return _pulse_distance((9000, 4500), _bytes_lsb((a, ~a & 0xFF, c, ~c & 0xFF)), 560, 560, 1690)
    if p == "NECext":
        data = (address & 0xFF, address >> 8 & 0xFF, command & 0xFF, command >> 8 & 0xFF)
        return _pulse_distance((9000, 4500), _bytes_lsb(data), 560, 560, 1690)
    if p in ("NEC42", "NEC42ext"):
        if p == "NEC42":
            bits = _lsb(address & 0x1FFF, 13) + _lsb(~address & 0x1FFF, 13) + \
                _lsb(command & 0xFF, 8) + _lsb(~command & 0xFF, 8)
        else:
            bits = _lsb(address & 0x3FFFFFF, 26) + _lsb(command & 0xFFFF, 16)
        return _pulse_distance((9000, 4500), bits, 560, 560, 1690)
    if p == "Samsung32":
        a, c = address & 0xFF, command & 0xFF
        return _pulse_distance((4500, 4500), _bytes_lsb((a, a, c, ~c & 0xFF)), 550, 550, 1650)
    if p == "NECx":
        data = (address & 0xFF, address >> 8 & 0xFF, command & 0xFF, ~command & 0xFF)
        return _pulse_distance((4500, 4500), _bytes_lsb(data), 560, 560, 1690)
    if p == "Panasonic":
        dev, sub, fn = address & 0xFF, address >> 8 & 0xFF, command & 0xFF
        return _pulse_distance((3456, 1728), _bytes_lsb((0x02, 0x20, dev, sub, fn, dev ^ sub ^ fn)),
                               432, 432, 1296)
    if p == "Kaseikyo":
        ident, vendor = address >> 24 & 3, address >> 8 & 0xFFFF
        genre1, genre2 = address >> 4 & 0xF, address & 0xF
        d = [vendor & 0xFF, vendor >> 8, 0, 0, 0, 0]
        parity = d[0] ^ d[1]
        parity = (parity & 0xF) ^ (parity >> 4)
        d[2] = (parity & 0xF) | genre1 << 4
        d[3] = (genre2 & 0xF) | (command & 0xF) << 4
        d[4] = (ident << 6 | command >> 4) & 0xFF
        d[5] = d[2] ^ d[3] ^ d[4]
        return _pulse_distance((3456, 1728), _bytes_lsb(d), 432, 432, 1296)
    if p in ("SIRC", "SIRC15", "SIRC20"):
        n = {"SIRC": 5, "SIRC15": 8, "SIRC20": 13}[p]
        bits = _lsb(command & 0x7F, 7) + _lsb(address & ((1 << n) - 1), n)
        d = [2400, 600]
        for b in bits:
            d += [1200 if b else 600, 600]
        return d[:-1]
    if p in ("RC5", "RC5X"):
        # bi-phase, 888 us halves, a 1 is space then mark; start bits, toggle 0
        bits = [1, 1 if p == "RC5" else 0, 0] + _msb(address & 0x1F, 5) + _msb(command & 0x3F, 6)
        halves = []
        for b in bits:
            halves += [(0, 888), (1, 888)] if b else [(1, 888), (0, 888)]
        return _halves_to_durations(halves)
    if p == "RC6":
        # leader, then bi-phase 444 us halves, a 1 is mark then space; mode 0,
        # the toggle bit twice as long
        halves = [(1, 2666), (0, 889)]
        bits = [(1, 444), (0, 444), (0, 444), (0, 444), (0, 889)] + \
            [(b, 444) for b in _msb(address & 0xFF, 8) + _msb(command & 0xFF, 8)]
        for b, t in bits:
            halves += [(1, t), (0, t)] if b else [(0, t), (1, t)]
        return _halves_to_durations(halves)
    raise FlipperError(f"protocol {p} is not supported yet")


def durations(signal: dict) -> tuple[int, list[int], int, int]:
    """(carrier Hz, one frame, gap after it us, frames per press)."""
    if signal.get("type") == "raw":
        data = [max(1, int(v)) for v in signal["data"]]
        if len(data) % 2 == 0:
            gap = data.pop()
        else:
            gap = 40_000
        return int(signal.get("frequency") or 38_000), data, max(gap, 20_000), 1
    p = signal.get("protocol", "")
    if p not in PROTOCOLS:
        raise FlipperError(f"protocol {p} is not supported yet")
    carrier, period, count = PROTOCOLS[p]
    f = frame(p, int(signal["address"]), int(signal["command"]))
    return carrier, f, max(period - sum(f), 4_000), count


def _words(frame_us: list[int], gap_us: int) -> tuple:
    words = []
    for i, us in enumerate(frame_us):
        if i % 2 == 0:
            words.append(0x8000 | min(us, 0x7FFF))
        else:
            words.extend(_spaces(us))
    return tuple(words) + _spaces(gap_us)


def command(name: str, signal: dict, repeat: bool = False, repeat_delay_ms: int = 450,
            like: dict | None = None) -> Command:
    """A 525 command for a Flipper signal, laid out like compose525.nec_command:
    one-shot after Logitech's 500 ms lead; repeating after a 50 ms lead with a
    pause before the held block, so a normal press sends once. `like`, one of
    known_presses(), sends it as the remote already sends that protocol: its
    frames per press, its power frames and its carrier."""
    carrier, f, gap, count = durations(signal)
    power = not repeat and _POWER.search(str(signal.get("name") or name))
    if power:
        count = POWER_FRAMES.get(signal.get("protocol"), count)
    if like is not None:
        carrier = like["carrier"]
        count = like["power"] if power and like.get("power") else like["frames"]
    one = _words(f, gap)
    if repeat:
        first = _spaces(50_000) + one * (count - 1) + _words(f, repeat_delay_ms * 1000) + (1,)
        return Command(name, (first, one + (1,), None), period_ns=round(1e9 / carrier))
    first = _spaces(500_000) + one * count + (1,)
    return Command(name, (first, None, None), period_ns=round(1e9 / carrier))


def describe(signal: dict) -> str:
    """Short text for the page: "NECext 7201 1E" or "raw, 38 kHz"."""
    if signal.get("type") == "raw":
        return f"raw, {round((signal.get('frequency') or 38000) / 1000)} kHz"
    return f"{signal.get('protocol')} {signal.get('address', 0):X} {signal.get('command', 0):X}"


# ------------------------------------------------------------------ mapping ---

def _norm(name: str) -> str:
    n = name.lower().replace("+", "up").replace("-", "down")
    n = re.sub(r"[^a-z0-9#*]", "", n)
    return n.replace("dn", "down")


# 525 key -> names a Flipper file uses for it, best first
SYNONYMS = {
    "Up": ["up", "uparrow", "arrowup", "cursorup", "navup"],
    "Down": ["down", "downarrow", "arrowdown", "cursordown", "navdown"],
    "Left": ["left", "leftarrow", "arrowleft", "cursorleft", "navleft"],
    "Right": ["right", "rightarrow", "arrowright", "cursorright", "navright"],
    "OK": ["ok", "select", "okenter", "center", "cursorenter", "cursorenterselect", "cursorok", "enter"],
    "Exit": ["exit", "back", "return", "cursorexit"],
    "Menu": ["menu", "mainmenu", "setup", "home"],
    "Guide": ["guide", "epg", "topmenu"],
    "Info": ["info", "inforecall", "display", "osd", "information"],
    "VolumeUp": ["volup", "volumeup"],
    "VolumeDown": ["voldown", "volumedown"],
    "Mute": ["mute", "mutetoggle", "volumemutetoggle", "audiomute"],
    "ChannelUp": ["chup", "channelup", "chnext", "progup", "pup", "pageup"],
    "ChannelDown": ["chdown", "channeldown", "chprev", "progdown", "pdown", "pagedown"],
    "Prev": ["recall", "last", "prech", "lastch", "previouschannel", "channelprevious"],
    "Play": ["play", "playpause"],
    "Pause": ["pause", "playpause"],
    "Stop": ["stop"],
    "Record": ["rec", "record"],
    "Rewind": ["rew", "rewind", "rwd", "fastback", "fr"],
    "FastForward": ["ff", "ffwd", "fastforward", "fwd"],
    "SkipBack": ["prev", "previous", "skipback", "skipprev"],
    "SkipForward": ["next", "skipnext", "skipfwd", "skipforward"],
    "Red": ["red", "colorbuttonred"], "Green": ["green", "colorbuttongreen"],
    "Yellow": ["yellow", "colorbuttonyellow"], "Blue": ["blue", "colorbuttonblue"],
    "Star": ["star", "*", "asterisk"], "Hash": ["#", "hash", "pound"],
    **{str(n): [str(n), f"num{n}", f"digit{n}", f"key{n}"] + (["digit010"] if n == 0 else []) for n in range(10)},
}
REPEATING = {"Up", "Down", "Left", "Right", "VolumeUp", "VolumeDown", "ChannelUp",
             "ChannelDown", "Rewind", "FastForward"}


def propose(signals: list[dict]) -> dict:
    """Which button goes where: {keys: {525 key: index}, soft: [index, ...]},
    indexes into `signals`. Every button not on a key is offered for the soft
    keys, power first and Flipper's "unknown" ones last; one signal per name."""
    by_norm: dict[str, int] = {}
    for i, s in enumerate(signals):
        by_norm.setdefault(_norm(s["name"]), i)
    keys, used = {}, set()
    for key, names in SYNONYMS.items():
        for n in names:
            if n in by_norm:
                keys[key] = by_norm[n]
                used.add(by_norm[n])
                break
    rest = [i for n, i in by_norm.items() if i not in used]
    rest.sort(key=lambda i: (0 if "power" in _norm(signals[i]["name"]) else
                             2 if _norm(signals[i]["name"]).startswith("unknown") else 1, i))
    return {"keys": keys, "soft": rest}


def label(name: str) -> str:
    """A soft key label from a database button name: "Vol_up" -> "Vol up",
    irdb's capitals "INPUT VIDEO 1" -> "Input video 1". The remote wraps a
    label over two lines and cuts what still does not fit."""
    text = re.sub(r"\s+", " ", name.replace("_", " ")).strip()
    if text.isupper():
        text = text.capitalize()
    return text[:20].strip()


# ------------------------------------------- as the remote already sends it ---

def _record_us(blob: bytes, address: int) -> list[int]:
    """A record's first block as alternating mark and space microseconds."""
    import analyze_525_ir as ir
    rec = ir.record(blob, address)
    us: list[int] = []
    for w in ir.body(blob, rec["pointers"][0])["words"]:
        mark, v = bool(w & 0x8000), w & 0x7FFF
        if (len(us) % 2 == 0) == mark:
            us.append(v)
        elif us:
            us[-1] += v
    return us


def _frames(us: list[int], header: tuple[int, int]) -> int:
    near = lambda a, b: abs(a - b) <= 0.15 * b
    return sum(1 for k in range(0, len(us) - 1, 2) if near(us[k], header[0]) and near(us[k + 1], header[1]))


def known_presses(raw: bytes) -> list[dict]:
    """How the remote already sends each device's codes, from its own records:
    [{device, header: (mark, space), frames, power, carrier}]. `frames` is what
    most of the device's commands send in one press; `power` what its power
    state sends to switch it on and off, None without one. The owner's
    Panasonic: three, and fourteen (FORMAT 5ah)."""
    import analyze_525_ir as ir
    import compose525
    import hconfig
    import verify_525_semantics as sem
    blob = hconfig.split_container(raw)[2]
    S = sem.section_offsets(blob)
    d = compose525._Doc(raw)
    groups = ir.ir_groups(blob, S)
    power_sends: dict[int, set] = {}
    for name, var in compose525.power_variables(raw).items():
        record = ir.u24(blob, S[13] + 8 + 3 * var) - compose525.BASE
        for t in range(ir.u16(blob, record + 4)):
            at = record + 7 + 8 * t
            stack = [(blob[at + 7], ir.u16(blob, at + 5))]
            while stack:
                o, x = stack.pop()
                if o == 0x7D:
                    power_sends.setdefault(x >> 8, set()).add(x & 0xFF)
                elif o == 0x7F:
                    stack.extend(d.ins(x))
    out = []
    for dev in compose525.devices(raw):
        if dev.group is None:
            continue
        heads, counts, power, carriers = {}, {}, {}, []
        for i, address in enumerate(groups[dev.group]):
            us = _record_us(blob, address)
            k = next((k for k in range(0, len(us) - 1, 2) if us[k] > 2000), None)
            if k is None:
                continue
            h = (round(us[k], -2), round(us[k + 1], -2))
            heads[h] = heads.get(h, 0) + 1
            carriers.append(ir.record(blob, address)["frequency_hz"])
            n = _frames(us, (us[k], us[k + 1]))
            (power if i in power_sends.get(dev.group, ()) else counts)[i] = n
        if not counts:
            continue
        header = max(heads, key=heads.get)
        mode = lambda xs: max(set(xs), key=list(xs).count)
        out.append({"device": dev.name, "header": header, "frames": mode(counts.values()),
                    "power": mode(power.values()) if power else None,
                    "carrier": round(sorted(carriers)[len(carriers) // 2])})
    return out


def like_known(known: list[dict], signal: dict) -> dict | None:
    """The device the remote already sends this signal's protocol to, by the
    frame's header, or None. A raw signal is left as it is."""
    if signal.get("type") == "raw":
        return None
    try:
        f = durations(signal)[1]
    except (FlipperError, KeyError, ValueError):
        return None
    near = lambda a, b: abs(a - b) <= 0.15 * b
    hits = [k for k in known if near(f[0], k["header"][0]) and near(f[1], k["header"][1])]
    return hits[0] if hits else None
