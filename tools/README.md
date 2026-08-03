# tools

Python 3, standard library only, no build step. Every script runs as
`python <script>.py` from inside this directory.

The analysis scripts default to the sample config in `samples/harmony525/`, which
is what every offset quoted in [`../docs/FORMAT.md`](../docs/FORMAT.md) refers to.
Pass a path to point them somewhere else:

```sh
python sections.py                     # the bundled 525 sample
python sections.py ../my-remote.EZHex  # your own dump, .EZHex or already split
```

## Offline analysis

| script | what it does |
|---|---|
| `split_ezhex.py` | split an .EZHex into XML + blob, verify size and checksum |
| `sections.py` | parse the 18-section pointer table from the header |
| `records.py` | parse the 114-record array indexed by section 6 |
| `keytable.py` | parse the key table in record #0 |
| `keymatrix.py` | test the keyboard-matrix hypothesis, render the 8×7 grid |
| `find_keytables.py` | generic key-table detector, ranked by code uniqueness |
| `compare_keytables.py` | compare tables within a config and across architectures |
| `ir_section.py` | sections 6 and 8 |
| `strings_blob.py` | printable strings and where they sit in the file |
| `find_text.py` | search for device names across encodings |
| `hexdump.py` | hexdump of a region |
| `diff_samples.py` | diff configs against each other |
| `manual_layout.py` | pull button labels with coordinates out of a manual PDF |

`_paths.py` is a shared helper, not a script.

`find_keytables.py` runs against the known-good 525 table first as a self-test: if
it does not report 51 entries at `0x0000FB`, the detector is broken rather than
the input being interesting.

`compare_keytables.py` and `diff_samples.py` need the arch 8 samples
(720/785/88x), which are not redistributed here. Download
[EZHex.Samples.zip](https://github.com/user-attachments/files/22412763/EZHex.Samples.zip)
from the concordance thread and unpack it into `samples/arch8/`.

`manual_layout.py` needs `pypdf` and a manual PDF, also not redistributed.
Logitech's documentation server is still up:
`images.harmonyremote.com/EasyZapper/Downloads/UserManual/525/enu/525_UserManual.pdf`

## Live communication with a remote

Windows only — these go through `setupapi`/`hid.dll` via ctypes, because the
32-bit `libhidapi-0.dll` shipped with concordance cannot be loaded from 64-bit
Python. Porting them to Linux/macOS would mean swapping the transport layer in
`hid_listen.py` for hidraw or libusb; everything above it is portable.

| script | what it does |
|---|---|
| `hid_query.py` | **protocol core** — GET_VERSION, ReadMisc, and the command whitelist |
| `hid_listen.py` | dump HID capabilities; with an argument, listen for input reports |
| `poll_state.py` | poll state variables, report changes |
| `capture_keys.py` | sample state during key presses, group by pause |
| `probe_kinds.py` | try every address space (EEPROM / STATE / RAM / REGISTER) |

Start with `hid_query.py`. Its second step reads the clock out of state variables,
which you can check against `concordance --get-time` — if those agree, the
transport is working and everything else it reports can be trusted.

### These do not write to your remote

`hid_query.py` carries a whitelist of permitted command bytes and hard-refuses
`0x30 WRITE_FLASH`, `0x40 WRITE_FLASH_DATA`, `0xA0 WRITE_MISC` and
`0xD0 ERASE_FLASH`. Everything else routes through it. `hid_listen.py` opens the
device with `GENERIC_READ` only, so Windows itself would reject a write.

Please leave that in place — see [../CONTRIBUTING.md](../CONTRIBUTING.md) for the
reasoning.
