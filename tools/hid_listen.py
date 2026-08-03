"""Read HID input reports from a Harmony remote - READ ONLY.

The device is opened with GENERIC_READ alone, so writing to the remote is not
possible even by accident: Windows refuses it, not merely this code.

The answer this script produces for the 525 is negative and already recorded:
NumberInputButtonCaps = 0, usage page 0xFF00 (vendor-defined), and zero input
reports across 45 s of key pressing. The remote's USB interface exists purely for
configuration and never reports key presses. Kept here so the result can be
reproduced, and in case another model behaves differently.

Windows only - it goes through setupapi/hid.dll via ctypes.

Usage:
    python hid_listen.py            # describe the device only
    python hid_listen.py 30         # then listen for input reports for 30 s
"""
import ctypes as C
import sys
import time
from ctypes import wintypes

VID, PID = 0x046D, 0xC111

setupapi = C.WinDLL("setupapi")
hid = C.WinDLL("hid")
kernel32 = C.WinDLL("kernel32", use_last_error=True)

DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ = 0x80000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = wintypes.HANDLE(-1).value
WAIT_TIMEOUT, WAIT_OBJECT_0 = 0x102, 0


class GUID(C.Structure):
    _fields_ = [("D1", wintypes.DWORD), ("D2", wintypes.WORD),
                ("D3", wintypes.WORD), ("D4", wintypes.BYTE * 8)]


class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wintypes.DWORD), ("Reserved", C.POINTER(wintypes.ULONG))]


class HIDD_ATTRIBUTES(C.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", C.c_ushort),
                ("ProductID", C.c_ushort), ("VersionNumber", C.c_ushort)]


class HIDP_CAPS(C.Structure):
    _fields_ = [("Usage", C.c_ushort), ("UsagePage", C.c_ushort),
                ("InputReportByteLength", C.c_ushort),
                ("OutputReportByteLength", C.c_ushort),
                ("FeatureReportByteLength", C.c_ushort),
                ("Reserved", C.c_ushort * 17),
                ("NumberLinkCollectionNodes", C.c_ushort),
                ("NumberInputButtonCaps", C.c_ushort),
                ("NumberInputValueCaps", C.c_ushort),
                ("NumberInputDataIndices", C.c_ushort),
                ("NumberOutputButtonCaps", C.c_ushort),
                ("NumberOutputValueCaps", C.c_ushort),
                ("NumberOutputDataIndices", C.c_ushort),
                ("NumberFeatureButtonCaps", C.c_ushort),
                ("NumberFeatureValueCaps", C.c_ushort),
                ("NumberFeatureDataIndices", C.c_ushort)]


class OVERLAPPED(C.Structure):
    _fields_ = [("Internal", C.POINTER(wintypes.ULONG)),
                ("InternalHigh", C.POINTER(wintypes.ULONG)),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE)]


# --- prototypes: without these, ctypes truncates 64-bit handles to 32 bits,
# --- which fails in ways that look like the device is absent
setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    C.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, C.c_void_p, C.POINTER(GUID), wintypes.DWORD,
    C.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, C.POINTER(SP_DEVICE_INTERFACE_DATA), C.c_void_p,
    wintypes.DWORD, C.POINTER(wintypes.DWORD), C.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, C.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [
    C.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, C.c_void_p, wintypes.DWORD,
    C.POINTER(wintypes.DWORD), C.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [
    wintypes.HANDLE, C.POINTER(OVERLAPPED), C.POINTER(wintypes.DWORD),
    wintypes.BOOL]
kernel32.CancelIo.argtypes = [wintypes.HANDLE]
kernel32.ResetEvent.argtypes = [wintypes.HANDLE]

hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, C.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, C.POINTER(C.c_void_p)]
hid.HidP_GetCaps.argtypes = [C.c_void_p, C.POINTER(HIDP_CAPS)]
hid.HidD_FreePreparsedData.argtypes = [C.c_void_p]
for _f in (hid.HidD_GetManufacturerString, hid.HidD_GetProductString):
    _f.argtypes = [wintypes.HANDLE, C.c_void_p, wintypes.ULONG]


def find_paths():
    """Return paths to every HID interface, unfiltered."""
    guid = GUID()
    hid.HidD_GetHidGuid(C.byref(guid))
    hdev = setupapi.SetupDiGetClassDevsW(
        C.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE:
        raise OSError("SetupDiGetClassDevs failed")

    paths, i = [], 0
    while True:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = C.sizeof(did)
        if not setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, C.byref(guid), i, C.byref(did)):
            break
        i += 1
        need = wintypes.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdev, C.byref(did), None, 0, C.byref(need), None)
        buf = C.create_string_buffer(need.value)
        # cbSize of the first field: 8 on x64, 6 on x86
        C.cast(buf, C.POINTER(wintypes.DWORD))[0] = 8 if C.sizeof(C.c_void_p) == 8 else 6
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, C.byref(did), C.cast(buf, C.c_void_p), need.value,
                None, None):
            continue
        paths.append(C.wstring_at(C.addressof(buf) + C.sizeof(wintypes.DWORD)))
    setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def open_read(path):
    """Open the device FOR READING ONLY."""
    h = kernel32.CreateFileW(path, GENERIC_READ,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
    return None if h == INVALID_HANDLE else h


def describe(h):
    attrs = HIDD_ATTRIBUTES()
    attrs.Size = C.sizeof(attrs)
    if not hid.HidD_GetAttributes(h, C.byref(attrs)):
        return None, None
    pp = C.c_void_p()
    caps = HIDP_CAPS()
    if hid.HidD_GetPreparsedData(h, C.byref(pp)):
        hid.HidP_GetCaps(pp, C.byref(caps))
        hid.HidD_FreePreparsedData(pp)
    return attrs, caps


def get_str(fn, h):
    buf = C.create_unicode_buffer(256)
    return buf.value if fn(h, buf, C.sizeof(buf)) else "(unavailable)"


def main():
    listen_s = int(sys.argv[1]) if len(sys.argv) > 1 else 0

    targets = []
    for p in find_paths():
        h = open_read(p)
        if not h:
            continue
        attrs, caps = describe(h)
        if attrs and attrs.VendorID == VID and attrs.ProductID == PID:
            targets.append((p, h, attrs, caps))
        else:
            kernel32.CloseHandle(h)

    if not targets:
        print(f"No {VID:04X}:{PID:04X} device found, or it cannot be opened "
              f"for reading.")
        return 1

    for p, h, attrs, caps in targets:
        print(f"=== {p} ===")
        print(f"  VID:PID          {attrs.VendorID:04X}:{attrs.ProductID:04X}"
              f"  ver {attrs.VersionNumber:04X}")
        print(f"  manufacturer     {get_str(hid.HidD_GetManufacturerString, h)}")
        print(f"  product          {get_str(hid.HidD_GetProductString, h)}")
        print(f"  UsagePage:Usage  0x{caps.UsagePage:04X}:0x{caps.Usage:04X}"
              f"  {'(vendor-defined)' if caps.UsagePage >= 0xFF00 else ''}")
        print(f"  input report     {caps.InputReportByteLength} B")
        print(f"  output report    {caps.OutputReportByteLength} B")
        print(f"  feature report   {caps.FeatureReportByteLength} B")
        print(f"  button/value caps in={caps.NumberInputButtonCaps}/"
              f"{caps.NumberInputValueCaps}")

    if not listen_s:
        print("\n(to listen, pass a number of seconds, e.g. 'hid_listen.py 30')")
        for _, h, _, _ in targets:
            kernel32.CloseHandle(h)
        return 0

    # --- listening ---
    p, h, attrs, caps = targets[0]
    n = caps.InputReportByteLength
    if n == 0:
        print("\nThe device declares no input report, so it does not send key"
              " presses over USB. Listening would be pointless.")
        kernel32.CloseHandle(h)
        return 0

    print(f"\n=== LISTENING {listen_s} s, report {n} B ===")
    print("Press buttons on the remote...\n")

    ev = kernel32.CreateEventW(None, True, False, None)
    buf = C.create_string_buffer(n)
    got = 0
    t_end = time.time() + listen_s
    while time.time() < t_end:
        ov = OVERLAPPED()
        ov.hEvent = ev
        kernel32.ResetEvent(ev)
        read = wintypes.DWORD()
        ok = kernel32.ReadFile(h, buf, n, C.byref(read), C.byref(ov))
        if not ok and C.get_last_error() not in (997,):  # ERROR_IO_PENDING
            print(f"ReadFile error {C.get_last_error()}")
            break
        if kernel32.WaitForSingleObject(ev, 500) == WAIT_TIMEOUT:
            kernel32.CancelIo(h)
            continue
        kernel32.GetOverlappedResult(h, C.byref(ov), C.byref(read), True)
        if read.value:
            got += 1
            data = buf.raw[:read.value]
            print(f"[{got:3d}] {time.strftime('%H:%M:%S')}  {read.value} B: "
                  f"{data.hex(' ').upper()}")

    kernel32.CloseHandle(ev)
    kernel32.CloseHandle(h)
    print(f"\ndone, {got} reports received")
    return 0


if __name__ == "__main__":
    sys.exit(main())
