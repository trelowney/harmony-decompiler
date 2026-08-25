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

## The decompiler

| script | what it does |
|---|---|
| `hconfig.py` | the library - parse a config into regions, rebuild it from them |
| `decompile.py` | config -> JSON, and a summary of what is decoded |
| `compile.py` | JSON -> config, recomputing pointers and checksums |
| `roundtrip.py` | **the correctness test** - decompile, recompile, compare bytes |
| `pointer_census.py` | reader-backed inventory of every address field the arch-9 tools can name |
| `relocate_arch9.py` | in-memory arch-9 insertion, driven only by the pointer census |
| `verify_arch9_relocation.py` | positive, refusal and one-omitted-holder-at-a-time relocation check |
| `show_bitmaps.py` | draw the LCD bitmaps as text, to check them by eye |

```sh
python roundtrip.py                       # the bundled sample
python roundtrip.py --all                 # every sample in the repo
python decompile.py --summary             # region map, nothing written
python decompile.py ../samples/harmony525/config.EZHex out.json
python compile.py out.json new.EZHex --against ../samples/harmony525/config.EZHex
```

A config is modelled as an ordered list of regions that tiles the blob with no
gaps and no overlaps. Each region is either understood - stored as fields, rebuilt
from them - or not, in which case it is hex that passes straight through. Adding a
recogniser to `hconfig.py` moves a region from the second kind to the first, and
`roundtrip.py` says immediately whether the new decoding is right.

Pointers decompile as `{"to": "r0042", "delta": 12}` rather than as offsets, so
they survive things moving. `compile.py` works out where every region lands before
emitting any of them - possible because a pointer is three bytes whatever it points
at - and then resolves each against the new layout. Pass `--absolute` to
`decompile.py` for raw offsets instead.

That is what `roundtrip.py --resize` checks: it lengthens a name, which shifts
every section after it, then confirms the rebuilt config has the same region
structure and the same *symbolic* pointers as before. On arch 9 it does two more
things, and both were added because the pointer comparison on its own said
everything was fine when it was not:

- it renders all 135 mode pages before and after and requires the pixels to
  match, which is what exposed the font-set, mode-page, value-map and
  non-row picture pointers;
- it expands all 200 class-5 IR records before and after and requires the
  carrier, the slots including the NULLs, and every duration word to match,
  which is what exposed the IR pointer graph. See `docs/FORMAT.md` section 4n.

**The caveat that matters.** All of that covers only the pointers this code can
see, and twice now the ones it could not see were the ones that mattered. On
the public arch-9 sample, 512 bytes in the 114-record array remain opaque; on
arch 8 almost everything is. Anything pointer-shaped in there is hex being
copied. A length-changing edit will leave such a pointer aimed at whatever
moved into its place, and nothing here will notice. Length-neutral edits,
retargeting a key or renaming something to a name of the same length, do not
have that problem.

### The reader-backed relocation check

`pointer_census.py` turns every address field exposed by an existing arch-9
reader into one consumable list. `relocate_arch9.py` inserts bytes in memory,
rewrites only that list, restamps `end_addr`, and computes the trailer checksum
last. Both refuse arch 8 / protocol 8, protocol 10 and protocol 14 by name.

The design is Danny Bloemendaal's, from `harmony-explorations` commit
`edb1349e669316320341e769c0434bb92c05571a`. The Python census and its mapping to
this repository's readers are local to this project.

The census cannot say **where** the gap went, only that every stated address
still points where it did - and a gap in the wrong place breaks no pointer. So
`relocate` asks a second reader instead: it decompiles the result, and refuses
if any structure the decompiler recognised before has stopped being recognised.
Inserting zero bytes adds no structure, so growth is expected and loss is a bug.
That guard reaches exactly as far as the decompiler reads and no further. Rooted
record lists and screen instructions reduced the public 525's opaque remainder
from 8,513 to 512 bytes; a gap inside those 512 can still be invisible to it.
See FORMAT.md 4s.

The arch-9 insertion floor is **`0x5F` in the public 525 sample**, the first byte
after `CMAH`. It was derived here rather than copied from Danny's Harmony One
implementation. The 525 firmware loads section 6, indexes
`operand * 3 + 3`, and follows that entry's u24; the `<u16 count><00>` header in
the file supplies the `+3`. Record placement is therefore stated through
section 6 and is not fixed immediately after the marker. What does force the
floor is the file's fixed header below it: magic, `end_addr`, 18 section slots,
their trailing null/padding and `CMAH` must retain their layout.

Run the complete check with:

```sh
python tools/verify_arch9_relocation.py
```

It checks the mechanical byte diff and the parsed meaning together, constructs
a second explicitly derived input, exercises four insertion cases, asserts that
every holder class has at least one pointer above a tested offset, and then
omits each holder class in turn and demands failure.

It does **not** prove that semantics outside the pointer graph are right. For
example, changing a key-table u16 to a different valid action-list index and
restamping the checksums can keep every pointer, region count, device count and
round trip unchanged while making a button perform the wrong action.

## Offline analysis

| script | what it does |
|---|---|
| `split_ezhex.py` | split an .EZHex into XML + blob, verify size and checksum |
| `sections.py` | parse the 18-section pointer table from the header |
| `records.py` | parse the 114-record array indexed by section 6 |
| `keytable.py` | parse the key table in record #0 |
| `keymatrix.py` | test the keyboard-matrix hypothesis, render the 8x7 grid |
| `find_keytables.py` | generic key-table detector, ranked by code uniqueness |
| `compare_keytables.py` | compare tables within a config and across architectures |
| `ir_section.py` | sections 6 and 8 |
| `strings_blob.py` | printable strings and where they sit in the file |
| `find_text.py` | search for device names across encodings |
| `hexdump.py` | hexdump of a region |
| `diff_samples.py` | diff configs against each other |
| `repair_890_dump.py` | undo the duplicated 54-byte blocks a Harmony 890 read leaves behind |
| `verify_arch8_key_matrix.py` | join the measured 880/885 keypad matrix to the scan codes in the configs |
| `verify_arch8_human_oracles.py` | check @kkong42's H880/H885 screen inventories against the public configs. Needs `--explorations` at a `dannybloe/harmony-explorations` checkout |
| `verify_arch8_standard_bindings.py` | the join that fixes all 55 arch 8 keys, 11,520 relabellings to one. Needs `--explorations` and `--manual` at Logitech's H880 user guide, which is not redistributed here |
| `ir_keymap_oracle.py` | name a key from what it transmits: catalogue, measurement plan, matcher |
| `check_525_mode_pages.py` | group modes by physical-list shape, and refuse a page count the file has no precedent for |
| `count_devices.py` | how many devices a config has, two independent ways, on any architecture |
| `harmony-ir-learner/` | capture infrared through a Harmony over USB, no LearnIR and no account (PowerShell) |
| `manual_layout.py` | pull button labels with coordinates out of a manual PDF |

`_paths.py` is a shared helper, not a script.

`find_keytables.py` runs against the known-good 525 table first as a self-test: if
it does not report 51 entries at `0x0000FB`, the detector is broken rather than
the input being interesting.

`compare_keytables.py` and `diff_samples.py` need the arch 8 samples
(720/785/88x). Those are in `samples/arch8/` now, mirrored from what their owners
uploaded to this repository's issues and to the concordance thread.

`harmony-ir-learner/` is the one thing here that is not Python: it is PowerShell,
because it calls the 32-bit `libconcord-6.dll` from a Concordance install and
that is the shortest path to doing so on Windows without a compiler. It captures
and never writes. See its own README.

`manual_layout.py` needs `pypdf` and a manual PDF, also not redistributed.
Logitech's documentation server is still up:
`images.harmonyremote.com/EasyZapper/Downloads/UserManual/525/enu/525_UserManual.pdf`

## Live communication with a remote

Windows only - these go through `setupapi`/`hid.dll` via ctypes, because the
32-bit `libhidapi-0.dll` shipped with concordance cannot be loaded from 64-bit
Python. Porting them to Linux/macOS would mean swapping the transport layer in
`hid_listen.py` for hidraw or libusb; everything above it is portable.

| script | what it does |
|---|---|
| `hid_query.py` | **protocol core** - GET_VERSION, ReadMisc, and the command whitelist |
| `hid_listen.py` | dump HID capabilities; with an argument, listen for input reports |
| `poll_state.py` | poll state variables, report changes |
| `capture_keys.py` | sample state during key presses, group by pause |
| `probe_kinds.py` | try every address space (EEPROM / STATE / RAM / REGISTER) |

Start with `hid_query.py`. Its second step reads the clock out of state variables,
which you can check against `concordance --get-time` - if those agree, the
transport is working and everything else it reports can be trusted.

### These do not write to your remote

`hid_query.py` carries a whitelist of permitted command bytes and hard-refuses
`0x30 WRITE_FLASH`, `0x40 WRITE_FLASH_DATA`, `0xA0 WRITE_MISC` and
`0xD0 ERASE_FLASH`. Everything else routes through it. `hid_listen.py` opens the
device with `GENERIC_READ` only, so Windows itself would reject a write.

Please leave that in place - see [../CONTRIBUTING.md](../CONTRIBUTING.md) for the
reasoning.

# Semantic evidence checks

`verify_525_semantics.py` pins the public Harmony 525 evidence for slot 8,
state-variable writes, `0x7C` operand closure, the four rendered device modes
against their infrared groups, and every reachable screen program. It can
optionally verify the `0x75` tone handler and screen opcodes 22/23 against a
local 32 KiB firmware image; firmware is never included in or copied into this
repository.

```sh
python tools/verify_525_semantics.py
python tools/verify_525_semantics.py --firmware path/to/mcu.bin
```

`render_525_screens.py` reconstructs all public 525 menu pages and font sheets
as ordinary 24-bit BMP files without third-party packages:

```sh
python tools/render_525_screens.py --out rendered-525
```

`analyze_525_ir.py` expands the 525's class-5 symbol dictionaries, reports
structural outliers and decodes valid NEC frames. Its default view correlates
the X96 group with mode 111 without writing a config or contacting a remote:

```sh
python tools/analyze_525_ir.py
```

`class5_ir_encoder.py` goes the other way. It takes already-normalised duration
words and emits a self-contained, relocatable class-5 record, preserving all
three header pointer slots including the NULLs. It packs literally, one symbol
per unique complete stream, which is deliberately simpler than what Logitech's
compiler does. It does not turn a single physical capture into press and repeat
streams, and it does not put anything into a config.

`verify_525_class5_encoder.py` is the check on it: repack the six learned X96
signals out of the public sample and require an exact decode afterwards, plus
twelve inputs it has to refuse.

```sh
python tools/verify_525_class5_encoder.py
python tools/verify_525_class5_encoder.py --bundle class5-vectors.json
```

The byte layout is credited to Danny Bloemendaal's `harmony-explorations` at
`a6516c7`. The literal packing and the X96 golden harness are this project's.

`clone_525_device.py` is an arch-9-only, offline fifth-device proof. It clones
the smallest existing device (`Amplifier Genius`, IR group 0 / mode 73) as IR
group 4 / mode 114, duplicates only its eight action lists, adds a second page
to the Devices menu, and shares the donor's IR records and two screen programs.
It refuses to overwrite its source or an existing output:

```sh
python tools/clone_525_device.py --out cloned.EZHex --proof cloned-proof.json
```

For the narrower class-5 placement proof, command 0 of the clone can instead
point to a newly packed copy of the known X96 record 9 while commands 1 through
7 remain shared:

```sh
python tools/clone_525_device.py --place-x96-record-9 \
  --out cloned-with-ir.EZHex --proof cloned-with-ir-proof.json
```

The resize and clone regressions expand all 200 original class-5 records before
and after relocation. The header/body/table/symbol-block layout and independent
reader are credited to Danny Bloemendaal's `harmony-explorations`; the literal
packer, placement construction and exact-corpus regression are from this
project.

The output is a structural artifact, not hardware-safe. It deliberately keeps
the same visible name, does not invent a state-variable identity, and omits the
three compiler-era page-list copies that Danny's firmware audit established are
unread. Danny Bloemendaal is credited for the device-as-IR-group model, mode
reader, value-map layout and independent codec oracle. The minimal clone layout
and proof harness are this project's work.

`edit_525_label.py` is a deliberately bounded offline authoring proof. It
changes the public sample's one shared `X96 Box` glyph string to the same-length
`X96 BOX`, rebuilds both the firmware trailer checksum and the EZHex checksum,
checks that only the two expected glyph bytes and the two-byte trailer field can
change, confirms that exactly five device-selection variants and six X96 pages
render differently, and requires the edited output to round-trip byte for byte.
It refuses to overwrite either its source or an existing output:

```sh
python tools/edit_525_label.py --out edited.EZHex --proof edited-proof.json
```

`relocate_525_label.py` is the bounded longer-string experiment. It appends
`X96 Boxx` immediately before the firmware checksum, retargets all 24 existing
opcode-4 users, converts the one inline opcode-5 draw into an opcode-4 draw plus
an opcode-20 continuation, and proves that all 25 symbolic users reach the new
string without moving an existing section or payload:

```sh
python tools/relocate_525_label.py --out relocated.EZHex --proof relocated-proof.json
```

**Its output should not be used, and the reason is worth reading.** Appending
puts the new string between the last picture in section 17 and the trailer.
Section 17 is self-describing: its four pictures run back to back, so their
total length is an invariant the file states about itself, and `@dannybloe`'s
reader checks it. Under that check the edited file has no picture bank at all.
Every test written here passed it: both checksums, all 25 pointer users, the
round trip, the rendered pages. None of them knew the bank existed.

`relocate_525_label_in_place.py` does the same edit without appending anything.
It grows the inline string by one byte into the instruction behind it, rewrites
that instruction as a jump to a suffix of the string, and leaves the file the
same length. Nine old users are preserved as aliases into the longer string and
24 external pointers keep their targets:

```sh
python tools/relocate_525_label_in_place.py --out relocated.EZHex
```

The container size, the end address, the section table and the position of the
picture bank all come back unchanged, which is what the appending version could
not manage. It is still an offline structural proof. Neither file has been
written to a remote.

All of these are arch-9/Harmony-525-specific. Screen opcode meanings are not
assumed to carry over to architectures 8, 12 or 14.
