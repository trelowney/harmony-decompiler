"""Read state variables from a Harmony remote over the vendor HID protocol.

SAFETY: only commands on the whitelist below are ever sent. Commands that write
persistently to the device are hard-refused and raise rather than being sent:

    0x30 WRITE_FLASH   0x40 WRITE_FLASH_DATA
    0xA0 WRITE_MISC    0xD0 ERASE_FLASH

Please do not remove or widen that whitelist as a side effect of another change.
Writing to a remote is worth working on, but as a deliberate, separate piece of
work - see CONTRIBUTING.md.

Framing per libconcord/libhidapi.cpp and libconcord/remote.cpp:
    write : 65 B = [0x00 report id][64 B payload]
    read  : 65 B = [0x00 report id][64 B payload]   -> payload starts at index 1

The implementation was validated against concordance: the time read here matched
`concordance --get-time` to the second, which is what makes the rest of the reads
trustworthy.

Windows only. Usage:
    python hid_query.py
"""
import ctypes as C
import sys
import time
from ctypes import wintypes

from hid_listen import (GUID, HIDD_ATTRIBUTES, OVERLAPPED, PID, VID,
                        find_paths, hid, kernel32)

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING, FILE_FLAG_OVERLAPPED = 3, 0x40000000
INVALID_HANDLE = wintypes.HANDLE(-1).value
WAIT_TIMEOUT = 0x102
ERROR_IO_PENDING = 997

kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, C.c_void_p, wintypes.DWORD,
    C.POINTER(wintypes.DWORD), C.POINTER(OVERLAPPED)]

# --- commands ---
COMMAND_GET_VERSION = 0x10
COMMAND_READ_FLASH = 0x50
COMMAND_READ_MISC = 0xB0
RESPONSE_VERSION_DATA = 0x20
RESPONSE_READ_FLASH_DATA = 0x60
RESPONSE_READ_MISC_DATA = 0xC0

MISC_EEPROM, MISC_STATE, MISC_RAM = 0x00, 0x01, 0x06

# How many data bytes a read-flash response carries, indexed by the low nibble
# of its first byte. Per the dlx table in libconcord/remote.cpp:ReadFlash;
# protocol 0, which is safe mode only, uses a different one.
READ_FLASH_LENGTHS = (0, 0, 1, 2, 3, 4, 5, 6, 14, 30, 62, 0, 0, 0, 0, 0)
READ_FLASH_CHUNK = 1022

ALLOWED_FIRST_BYTE = {0x10, 0x55, 0xB2, 0xB3}
FORBIDDEN = {0x30: "WRITE_FLASH", 0x40: "WRITE_FLASH_DATA",
             0xA0: "WRITE_MISC", 0xD0: "ERASE_FLASH"}

PKT = 64


class Remote:
    def __init__(self):
        self.h = None
        for p in find_paths():
            h = kernel32.CreateFileW(
                p, GENERIC_READ | GENERIC_WRITE,
                FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
            if h == INVALID_HANDLE:
                continue
            a = HIDD_ATTRIBUTES()
            a.Size = C.sizeof(a)
            if hid.HidD_GetAttributes(h, C.byref(a)) and \
                    a.VendorID == VID and a.ProductID == PID:
                self.h = h
                break
            kernel32.CloseHandle(h)
        if not self.h:
            raise SystemExit(f"No Harmony {VID:04X}:{PID:04X} found")
        self.ev = kernel32.CreateEventW(None, True, False, None)

    def close(self):
        if self.ev:
            kernel32.CloseHandle(self.ev)
        if self.h:
            kernel32.CloseHandle(self.h)

    def _io(self, fn, buf, n, timeout_ms):
        ov = OVERLAPPED()
        ov.hEvent = self.ev
        kernel32.ResetEvent(self.ev)
        done = wintypes.DWORD()
        ok = fn(self.h, buf, n, C.byref(done), C.byref(ov))
        if not ok and C.get_last_error() != ERROR_IO_PENDING:
            raise OSError(f"IO error {C.get_last_error()}")
        if kernel32.WaitForSingleObject(self.ev, timeout_ms) == WAIT_TIMEOUT:
            kernel32.CancelIo(self.h)
            return None
        kernel32.GetOverlappedResult(self.h, C.byref(ov), C.byref(done), True)
        return done.value

    def write(self, data: bytes):
        first = data[0] if data else 0
        if first in FORBIDDEN:
            raise RuntimeError(f"FORBIDDEN COMMAND 0x{first:02X} "
                               f"({FORBIDDEN[first]}) - refused")
        if first not in ALLOWED_FIRST_BYTE:
            raise RuntimeError(f"Command 0x{first:02X} is not on the whitelist")
        payload = data.ljust(PKT, b"\x00")[:PKT]
        buf = C.create_string_buffer(b"\x00" + payload, PKT + 1)
        return self._io(kernel32.WriteFile, buf, PKT + 1, 3000)

    def read(self, timeout_ms=3000):
        buf = C.create_string_buffer(PKT + 1)
        n = self._io(kernel32.ReadFile, buf, PKT + 1, timeout_ms)
        if not n:
            return None
        raw = buf.raw[:n]
        return raw[1:] if raw[0] == 0x00 else raw   # strip report id

    # --- commands ---
    def get_version(self):
        self.write(bytes([COMMAND_GET_VERSION]))
        r = self.read()
        if not r:
            return None, "timeout"
        if (r[0] & 0xF0) != RESPONSE_VERSION_DATA:
            return None, f"unexpected response 0x{r[0]:02X}"
        return r, f"length={r[0] & 0x0F}"

    def read_misc_word(self, addr, kind=MISC_STATE):
        self.write(bytes([COMMAND_READ_MISC | 0x03, kind,
                          (addr >> 8) & 0xFF, addr & 0xFF]))
        r = self.read()
        if not r:
            return None
        if (r[0] & 0xF0) != RESPONSE_READ_MISC_DATA or r[1] != kind:
            return None
        return (r[2] << 8) | r[3]

    def read_flash(self, addr, length, progress=None):
        """Read from the remote's flash. Read-only; see the safety note above.

        Per libconcord/remote.cpp:ReadFlash. The request names an address and
        a length, and the remote answers with a run of packets whose first
        byte carries the payload size in its low nibble and whose second byte
        is a sequence number stepping by 0x11 and wrapping at a byte.

        Returns (data, error). A short read still returns what arrived, which
        matters when the point of the exercise is finding out where the
        readable region stops.
        """
        out = bytearray()
        end = addr + length
        while addr < end:
            chunk = min(READ_FLASH_CHUNK, end - addr)
            self.write(bytes([COMMAND_READ_FLASH | 0x05,
                              (addr >> 16) & 0xFF, (addr >> 8) & 0xFF,
                              addr & 0xFF,
                              (chunk >> 8) & 0xFF, chunk & 0xFF]))
            seq, got = 1, 0
            while got < chunk:
                r = self.read()
                if not r:
                    return bytes(out), f"timeout at 0x{addr + got:06X}"
                if (r[0] & 0xF0) != RESPONSE_READ_FLASH_DATA:
                    if (r[0] & 0xF0) == 0xF0:      # COMMAND_DONE, a short read
                        break
                    return bytes(out), (f"unexpected response 0x{r[0]:02X} "
                                        f"at 0x{addr + got:06X}")
                if r[1] != seq:
                    return bytes(out), (f"sequence {r[1]:02X}, expected "
                                        f"{seq:02X}, at 0x{addr + got:06X}")
                seq = (seq + 0x11) & 0xFF
                n = READ_FLASH_LENGTHS[r[0] & 0x0F]
                if not n:
                    break
                out += r[2:2 + n]
                got += n
            # The remote closes each run of data packets with COMMAND_DONE.
            # It has to be consumed here: left in the pipe it turns up as the
            # first packet of the next request and looks like a short read.
            tail = self.read(timeout_ms=500)
            if tail and (tail[0] & 0xF0) != 0xF0:
                return bytes(out), (f"expected DONE after 0x{addr:06X}, "
                                    f"got 0x{tail[0]:02X}")
            # Advance by what actually arrived rather than by what was asked
            # for. The remote can answer short, and assuming otherwise leaves a
            # gap in the middle of the result with nothing to say so.
            if got == 0:
                return bytes(out), f"no data at 0x{addr:06X}"
            addr += got
            if progress:
                progress(len(out), length)
        return bytes(out), None

    def read_misc_byte(self, addr, kind=MISC_STATE):
        self.write(bytes([COMMAND_READ_MISC | 0x02, kind, addr & 0xFF]))
        r = self.read()
        if not r:
            return None
        if (r[0] & 0xF0) != RESPONSE_READ_MISC_DATA or r[1] != kind:
            return None
        return r[2]


def main():
    rm = Remote()
    try:
        print("=== 1. GET_VERSION (transport check) ===")
        r, note = rm.get_version()
        if not r:
            print(f"  FAILED: {note}")
            return 1
        print(f"  raw: {r[:10].hex(' ').upper()}   ({note})")
        print(f"  firmware {r[1] >> 4}.{r[1] & 0xF}   hardware {r[2] >> 4}.{r[2] & 0xF}"
              f"   flash id/mfg {r[3]:02X}/{r[4]:02X}")
        print(f"  architecture {r[5] >> 4}   skin {r[6]}   protocol {r[7]}")

        print("\n=== 2. CLOCK from state variables (validation) ===")
        print("    compare this against `concordance --get-time`")
        tsv = [rm.read_misc_word(a, MISC_STATE) for a in range(7)]
        print(f"  raw words 0-6: {tsv}")
        if all(v is not None for v in tsv):
            print(f"  -> {2000+tsv[6]:04d}-{tsv[5]+1:02d}-{tsv[3]+1:02d} "
                  f"{tsv[2]:02d}:{tsv[1]:02d}:{tsv[0]:02d}  (dow={tsv[4] & 7})")

        print("\n=== 3. OTHER STATE VARIABLES (addresses 7-47) ===")
        print("    the config's name table gives these names - see FORMAT.md section 5c")
        vals = {}
        for a in range(7, 48):
            v = rm.read_misc_word(a, MISC_STATE)
            if v is None:
                print(f"  address {a}: no response - stopping")
                break
            vals[a] = v
        for a in sorted(vals):
            if a % 8 == 7 or a == min(vals):
                print()
                print(f"  {a:3d}: ", end="")
            print(f"{vals[a]:5d} ", end="")
        print()
        nz = {a: v for a, v in vals.items() if v}
        print(f"\n  non-zero: {len(nz)} of {len(vals)}  -> {nz}")
    finally:
        rm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
