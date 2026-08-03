# Harmony 525 config format (arch 9, protocol 9)

Derived by reverse engineering a config dumped from a physical Harmony 525 on
2026-08-02. Everything below is **verified against real data** unless explicitly
marked as a hypothesis. The distinction is kept deliberately - please preserve it
when editing.

The sample this document describes is in [`samples/harmony525/`](../samples/harmony525/).

---

## 1. The .EZHex container - SOLVED

A text XML header and a binary blob concatenated into one file.

| Part | Size | Notes |
|---|---|---|
| XML header | 3,153 B | ends with `</INFORMATION>` |
| separator | 2 B | `\r\n` |
| binary blob | 78,486 B | the config itself |

Integrity (per `libconcord/operationfile.cpp:find_config_binary`):

- `<BINARYDATASIZE>` = exact blob length -> **78486, matches**
- `<CHECKSUM>` = XOR of all blob bytes, seeded `0x69` -> **12 (0x0C), matches**

The XML also carries `<INTENDEDVERSION>` (PROTOCOL 9, SKIN 22, FLASH 0xFF:0x12,
BOARD 2.5.0). The remote validates these and refuses a mismatched config with
*"This configuration file is not compatible with your Harmony Remote."*

-> **A generated config must keep these tags identical.**

## 2. Binary blob - header SOLVED

```
0x00  'AHCM'                    magic (the blob ends with a mirrored 'MCHA')
0x04  u32  0x00033292           absolute flash address of end-of-config
0x08  u32  0x00001400           meaning unknown (5120)
0x0C  u32[18]                   section pointer table
0x54  zeros                     end of table
0x5B  'CMAH'                    end of header
```

**Addresses are absolute flash addresses, not file offsets.**
`config_base = 0x20000`, so `file_offset = address - 0x20000`.

Verification: `0x033292 - 78486 + 4 = 0x020000` exactly. Consistent with
pointer #0 (`0x02F35B` -> `0x00F35B`) landing immediately before the first
human-readable record.

## 3. Sections (18 entries, contiguous, no gaps)

| # | offset | length | what it is |
|---|---|---|---|
| 0 | 0x00F35B | 307 | **name table** (solved, see §4) |
| 1-3 | 0x00F48E | 7-14 | small tables, unknown |
| 4 | 0x00F4AB | 2,551 | unknown, 3-byte records |
| 5 | 0x00FEA2 | 13 | unknown |
| 6 | 0x00FEAF | 6,968 | **pointer table** into the low region (see §4b) |
| 7 | 0x0119E7 | 17 | unknown |
| 8 | 0x0119F8 | 1,086 | *hypothesis:* bytecode (see §4c) |
| 9-16 | ... | 16-1,463 | unknown |
| 17 | 0x01267A | 3,096 | unknown |

The region `0x0000-0xF35B` (~61 KiB, 78% of the blob) is **not** covered by the
section table. It is not unstructured, though - it is an array of 114 records
indexed by section 6, see §4b and §4d. The first of those records contains the
key table (§4e).

## 4. Name record - SOLVED

Section 0 is a table of named symbols belonging to a rule engine called
"HarmonyAssistant". The whole section is wrapped:

```
ED FE            0xFEED
u16 length       offset of the terminator, counted from the start of the table
u8               unknown, 0 here
records:
  A7  u16 len  u16 parent  u16 index  char name[len-4]
  ...
EF BE            0xBEEF
```

`len` counts the parent and index fields plus the name, so a record occupies
`len + 3` bytes. **13 records** in this config. The declared length reads 305 and
the terminator sits at exactly +305, with the section being 307 bytes - the two
agree, which is what makes this safe to rebuild rather than merely plausible.

> Two corrections to earlier revisions of this document, both caught by building
> the decompiler: the record count is 13, not 12, and the `ED FE ... EF BE` framing
> with its length field was not noticed at all - `0xFEED` and `0xBEEF` are not a
> coincidence.

The table round-trips byte-for-byte through `tools/hconfig.py`, so the structure
above is confirmed rather than inferred.

Contents of this particular remote:

| parent | index | name |
|---|---|---|
| 0 | 0 | `Root` |
| 0 | 1 | `State` |
| 0 | 2 | `HarmonyAssistant` |
| 1 | 17 | `TV_Panasonic_Power_2` |
| 1 | 16 | `Amplifier_Genius_Power_2` |
| 1 | 19 | `XBOX_360_Power_2` |
| 1 | 21 | `TV_Panasonic_InputType_8` |
| 1 | 20 | `TV_Panasonic_Input_9` |
| 1 | 22 | `TV_Panasonic_TVInput_2` |
| 1 | 13 | `CurrentLocation_1` |
| 1 | 15 | `CurrentActivityState_0_4` |
| 2 | 0 | `AssistantMenu` |
| 2 | 1 | `Show` |

-> The setup is a Panasonic TV, a Genius amplifier and an XBOX 360.

Note: these are rule-engine *state variables*. They are **not** a list of buttons
and **not** a complete list of device commands. The actual commands (Volume,
Channel, Menu, ...) are not here - they live elsewhere, most likely in the
uncovered region below 0xF35B.

## 4b. Pointers are 24-bit - SOLVED

The config uses **3-byte (24-bit) little-endian absolute flash addresses**, not
4-byte ones. This is why `0x02` is the most common byte in the whole blob (28%) -
it is the high byte of `0x02xxxx` addresses.

Verified on section 15 (`0x12669`): `05` = count, then 5 addresses
`0x032654, 0x032657, 0x03265A, 0x032663, 0x032666` -> offsets `0x12654 ...` which
fall **exactly** inside section 14 (0x12632 + 55 B). Sections 5, 11 and 12 are
consistent too.

There are three shapes, all ending in the same array of 24-bit addresses:

```
<u8 count>  <u24 address>[count]
<u16 count> <u24 address>[count]
<u16 count> <00> <u24 address>[count]      (section 6)
```

### Which sections carry pointers

This is the question that matters most for a compiler: a section whose pointers
are decoded can keep everything else opaque and still survive a change in length.
Running the decompiler over the 525 config answers it.

| section | pointers | shape | fills the section? |
|---|---|---|---|
| 5 | 4 | u8 | yes |
| 6 | 114 | u16 + pad | no - 345 B of 6,968 |
| 7 | 5 | u16 | yes |
| 9 | 8 | u8 | no - 25 B of 417 |
| 10 | **487** | u16 | **yes, all 1,463 B** |
| 11 | 22 | u16 | yes |
| 12 | 5 | u8 | yes |
| 14 | 11 | u8 | no - 34 B of 55 |
| 15 | 5 | u8 | yes |

Sections **1, 2, 3, 4, 8, 13, 16 and 17** contain no recognisable pointer table
and remain entirely opaque.

Section 10 was previously listed as unknown; it is a pointer array and nothing
else. That accounts for 656 addresses across the config, every one of which the
compiler now recomputes rather than copying.

Acceptance is deliberately strict - every address inside the config, addresses
strictly increasing, and either an exact fit or at least three entries. A loose
test finds pointer tables everywhere, for the reason given above: `0x02` is the
most common byte in the file precisely *because* it is the high byte of these
addresses.

### Section 6 = index into the low region

```
72 00 00 | F1 00 02 | 55 02 02 | F0 02 02 | 9D 03 02 | 47 04 02 | ...
   ^count=114        ^0x0000F1   ^0x000255  ^0x0002F0  ^0x00039D
```

The addresses are **monotonically increasing** with regular spacing
(~0xF0-0x160 B), pointing into the previously unexplored `0x0000-0xF35B` region.

-> **That region is not an unknown structure but an array of ~114 records of
~240-350 B each, indexed by section 6.** Given the size and count, these are very
likely IR codes / device commands.

One caveat worth recording: the statistic "35% of positions in the blob decode to
a valid 24-bit address" looks convincing but is *partly circular* - it follows
mostly from `0x02` being such a common byte. The real evidence is the monotonic
table above, not that percentage.

## 4c. Section 8 looks like bytecode - HYPOTHESIS

Interpreting it as pointers **fails** (addresses out of range). The shape
`<u16 operand> <u8 opcode>` does fit:

```
0B | 00 00 7E | 2F 00 7F | 30 00 7F | 31 00 7F | 32 00 7F | 06 00 7E | B8 00 7F
```

Operands 47, 48, 49, 50 (a contiguous run), then 6, 184, 8, 184, 10, 184.
Opcodes `0x9E, 0x9F, 0xA6, 0xA7` appear further on with the same shape.

This **independently matches** the original developer's description (§6):
*"Von-Neumann style computing device with a 16 bit instruction."*

## 4d. The 114-record array - header SOLVED

Section 6 indexes **114 records** in the region `0x0000F1-0x00E0F8` (57,351 B).
Sizes range 142-29,939 B, median ~177 B. **All 114 start with byte `0x00`.**

Record header, now verified on **all 114**:

```
00
u24         a back-reference, usually the previous record's start + 9
u16 count
u24[count]  addresses
```

> An earlier revision of this document read the `01 00` as a literal and
> described the header as a fixed pair of pointers. It is not: `01 00` is the
> **count**, and it looks like a constant only because 108 of the 114 records
> happen to carry exactly one address. The other six carry 2, 4, 5 and 6.
>
> | addresses | records |
> |---|---|
> | 1 | 108 |
> | 2 | 1 |
> | 4 | 2 |
> | 5 | 1 |
> | 6 | 2 |
>
> Reading three records by hand was never going to catch this, however carefully
> it was done, because all three were in the 108. Decoding the field and letting
> the round trip check all 114 did.

Record trailer: `00 <u24 into section 8> <u24 back into itself>`.

That pointer into section 8 is strong evidence that **each record references its
own bytecode** - exactly as the original developer described.

The headers account for 249 of the pointers the compiler recomputes. Record
bodies are still opaque, apart from the key table in record #0.

## 4e. KEY TABLE - SOLVED

Inside record #0, starting at offset `0x0000FB`:

```
<u8 key code> <u16 target> <0x7F>
```

**51 entries**, and the count is explicitly declared by byte `0x33` = 51 at offset
`REC0+9`. The table ends exactly at `0x0001C7`, where the next structure begins.
No key code repeats. This is not a guess - the declared count, the terminators and
the boundaries all agree.

Key codes fall in the range `0x81-0xB9` (plus one exception, `0x06`) and form
obvious groups: `0x81-0x8F`, `0x91-0x9F`, `0xA1-0xAF`, `0xB1-0xB9`.
Targets are mostly a contiguous run 0-46, with the exceptions 95, 155, 179, 311.

-> The main table holds **51 entries: 50 physical keys + 1 virtual** (code `0x06`,
see §5g). What each code physically *means* is **still unknown** - sniffing it
over USB was tried and does not work (§5d).

## 4f. A repeating block

The pattern `16 <i> 03 00 <i*8> 00 <i*8> 60 08 8B 2F 03`, with index `i` cycling
0-7, occurs **472x** in the blob (= 59 groups of 8). Meaning unknown, but it is
the most prominent repeating structure in the file.

## 5. Samples from issue #66 - different architecture, SAME container

Downloaded from `github.com/user-attachments/files/22412763/EZHex.Samples.zip`.

All four are **PROTOCOL 8, SKIN 15, BOARD 1.8.0** -> arch 8 (720/785/88x), not
arch 9. Their magic is `TPTP ... DKDK`, not `AHCM ... MCHA`. They are 444-492 KB
against our 78 KB.

**But the header has an identical shape:**

```
sample:  TPTP | 57 2E 09 00 | 00 15 00 00 | 7B CC 04 00 | FE CD 04 00 | ...
ours:    AHCM | 92 32 03 00 | 00 14 00 00 | 5B F3 02 00 | 8E F4 02 00 | ...
         magic  u32 end       u32 ?         u32[] pointer table
```

And the key check: `0x092E57 - 470619 + 4 = 0x20000` - **`config_base` is
`0x20000` on arch 8 too.** The container is therefore shared across
architectures; only the magic and the section contents differ. Our structural
findings generalise.

One extra detail: the sample has `00 00 00 00` at position 9 of the pointer table
-> null = "subsystem not present".

### Negative result: diffing samples does not work

Three of the samples were created ten minutes apart, so they should logically
differ only in a detail. A byte diff nonetheless shows:

| pair | length delta | differing bytes |
|---|---|---|
| (1) vs (2) | +20,085 B | 84.4% |
| (2) vs (3) | +1,446 B | 73.2% |

The first differing byte is always at `0x000004` - i.e. right inside `u32 end`.

-> **A small logical change reshuffles the entire image.** The config is a compiled
image with absolute pointers, so any length change shifts everything after it.
Naive differential analysis is therefore dead.

Practical consequence: patching is only possible **without changing any length**.
The moment anything grows, every absolute pointer in the file has to be
recomputed. This is the main argument for the decompile/recompile approach rather
than in-place patching.

## 5b. Live communication with the remote - WORKS

The vendor HID protocol was reimplemented in Python (`tools/hid_query.py`),
without concordance and without external libraries (just ctypes + the Windows HID
API).

Framing (per `libhidapi.cpp` + `remote.cpp`):

```
write : 65 B = [0x00 report id][64 B payload]
read  : 65 B = [0x00 report id][64 B payload]
```

| command | packet | response |
|---|---|---|
| GET_VERSION | `10` | `2N ...` (N = length) |
| ReadMiscByte | `B2 <kind> <addr>` | `C2 <kind> <data>` |
| ReadMiscWord | `B3 <kind> <addr_hi> <addr_lo>` | `Cx <kind> <hi> <lo>` |

`kind`: `00` EEPROM, `01` STATE, `06` RAM. Arch >= 8 uses the **word** variants.

**Validation:** the time read this way, `2024-02-21 14:36:19 dow=3`, is identical
to `concordance --get-time` -> `2024/02/21 Wed 14:36:19`. The transport is
demonstrably correct, so subsequent reads can be trusted.

## 5c. Config names are addresses of live state variables

This is the most useful runtime finding. The `index` field in the name table (§4)
is **not an ordinal - it is the address of a state variable readable live over
USB.**

Measured (`ReadMiscWord(addr, kind=STATE)`) against the names from the config:

| addr | name in config | live value | declared range | in range? |
|---|---|---|---|---|
| 13 | `CurrentLocation_1` | 0 | 1 | yes |
| 15 | `CurrentActivityState_0_4` | 3 | 0-4 | yes |
| 16 | `Amplifier_Genius_Power_2` | 0 | 2 | yes |
| 17 | `TV_Panasonic_Power_2` | 0 | 2 | yes |
| 19 | `XBOX_360_Power_2` | 0 | 2 | yes |
| 20 | `TV_Panasonic_Input_9` | 5 | 9 | yes |
| 21 | `TV_Panasonic_InputType_8` | 5 | 8 | yes |
| 22 | `TV_Panasonic_TVInput_2` | 0 | 2 | yes |

-> **The numeric suffix in a name is the number of possible values.**
`Power_2` = 2 states (off/on), `Input_9` = 9 inputs, and so on. All 8 measured
values lie inside the declared range - 8/8, no exceptions.

Unnamed variables with a non-zero value: addr 8 = 2, addr 11 = 1, addr 18 = 1.
Addresses 23-47 read as zero; from 48 up the remote stops responding.

All three `Power_*` are 0, i.e. devices off, which is correct - the remote was
sitting on a desk.

**Why it matters:** there is a bridge between the static config and live state.
A tool can read the current device state, not just parse a file.

## 5d. Identifying keys over USB is IMPOSSIBLE - closed

Three independent attempts, all negative. Not worth reopening.

**1. HID input reports** - the remote sent none in 45 s of key pressing. This
matches the descriptor: `NumberInputButtonCaps = 0`, usage page `0xFF00`
(vendor-defined). There is no keyboard or consumer collection; the USB interface
exists purely for configuration.

**2. State variables during a press** - a press does show up, but **only as a
boolean**.

The key to understanding this: in USB mode the remote ignores most keys entirely
(Volume, digits - 100 s, 412 polling rounds, 0 errors, **0 changes**). The keys
that do react light up the display, and that is the only externally visible
effect:

```
17:05:51  [9] 0->1     17:06:01  [9] 1->0     <- exactly 10 s
17:06:05  [9] 0->1     17:06:15  [9] 1->0     <- exactly 10 s
```

**Address 9 = backlight, 10 s timeout.** Reproducible.

The decisive test: two *different* keys, both of which light the display, produced
an **identical** result - only `[9] 0->1`, no other address moved. The remote does
not expose *which* key was pressed.

**3. Other address spaces** - probing every `kind`:

| kind | byte mode | word mode |
|---|---|---|
| `00` EEPROM | all zeros | all zeros |
| `01` STATE | all zeros | **the only one with data** |
| `06` RAM | all zeros | all zeros |
| `07` REGISTER | all zeros | all zeros |

Byte mode returns zeros everywhere, consistent with arch >= 8 using word variants.

**Conclusion:** in USB mode the remote locks its UI and does not expose key
presses. The meaning of codes `0x81-0xB9` has to come from the config or the
hardware, not from the USB interface.

## 5e. There are several key tables - one per activity

Detector: runs of 4-byte groups terminated by `0x7F`, ranked by **ratio of unique
codes** (not by length - the longest runs are always filler bytes). Validated
against known ground truth: it finds our table, 51/51 unique, at `0x0000FB`.

**Our config (arch 9) - 4 tables:**

| offset | entries | codes | relation to main |
|---|---|---|---|
| `0x0000FB` | 51 | 0x06-0xB9 | **main** |
| `0x00E4D8` | 26 | 0x01-0xAF | 23/26 shared, plus 0x01, 0x02, 0x05 |
| `0x00E586` | 17 | 0x05-0xAF | 16/17 shared, plus 0x05 |
| `0x00E60F` | 22 | 0x05-0xAF | 21/22 shared, plus 0x05 |

-> The smaller tables are near-subsets of the main one, i.e. **per-activity
overlays**. Each activity remaps a subset of the keys. That is exactly how Harmony
remotes behave.

The main table is essentially the identity mapping (targets 0...46) with **four
exceptions**: `0x06->155`, `0x9D->311`, `0x97->179`, `0xB4->95`. Those four keys point
somewhere other than a plain IR command - most likely a macro or an activity.

## 5f. Key codes are shared across architectures

Arch 8 turns out to carry **two** distinct key tables, which is worth stating
plainly because an earlier revision of this document conflated them:

| offset | entries | targets | across the four samples |
|---|---|---|---|
| `0x000A22` | 53 | 17-69, a contiguous run | byte-identical in all four |
| `0x0001EF` | 40 | 3-715, with exceptions | byte-identical in all four |

A third, at `0x000293` with 15 entries, is also identical in all four. The
remaining tables each appear in only one sample, so nothing can be concluded
about them.

The contiguous 17-69 target run at `0x000A22` looks like a canonical or default
table. But **do not read too much into "identical in all four"**: the samples
share a board revision and a flash ID and came from one person, so they are very
probably four configs for a *single* remote. What that identity shows is that
these tables do not change with configuration - not that they are the same on
every model. Confirming the latter needs a sample from a different arch 8 remote,
which nobody has yet.

Overlap with the 51-entry arch 9 main table depends on which one you compare
against, so both are given:

| against | shared | arch 9 only | arch 8 only |
|---|---|---|---|
| canonical `0x000A22` (53) | **41** | 10 | 12 |
| per-config `0x0001EF` (40) | 34 | 17 | 6 |

The canonical comparison is the meaningful one for the question "do models share
key codes", and 41 of 51 is a strong yes.

The code ordering is strikingly similar between the two architectures:

```
arch 8:  88 8B 8A 8D 8C    8F 8E 81 83 82 85    87 86  98 99 9A 9B ...
arch 9:  89 8B 8A 8D 8C 06 8F 8E 81 83 82 85 84 87 86  99 9A 9B 9C ...
```

Same groups (`0x8x` -> `0x9x` -> `0xAx` -> `0xBx`), same order within them, with a
few codes inserted or omitted.

-> **The code <-> physical key assignment is shared across models**, and the table
order is canonical (Logitech's standard key ordering).

**Practical consequence:** obtaining the mapping for *one* model in this family
would carry most codes over to the 525. Arch 8 = Harmony 720/785/88x, whose key
layouts are publicly documented.

## 5g. Key codes are keyboard-matrix addresses

```
code = 0x80 | (row << 3) | column        row 0-7, column 0-7
```

Harmony 525 matrix (`.` = not wired):

```
      c0   c1   c2   c3   c4   c5   c6   c7
  r0    .  81   82   83   84   85   86   87
  r1    .  89   8A   8B   8C   8D   8E   8F
  r2    .  91   92   93   94   95   96   97
  r3    .  99   9A   9B   9C   9D   9E   9F
  r4    .  A1   A2   A3   A4   A5   A6   A7
  r5    .  A9   AA   AB   AC   AD   AE   AF
  r6    .  B1   B2   B3   B4   B5   B6   B7
  r7    .  B9    .    .    .    .    .    .
```

**8 rows x 7 columns**, 50 occupied positions, zero collisions. Column 0 is
unwired. Arch 8 (720/785/88x) does use column 0 and has gaps elsewhere - a
different wiring, the same scheme. That is why codes agree across models.

The 51st entry of the main table is code `0x06` (target 155). It does not fit the
matrix; it is not a physical key but a virtual event.

### The key count is NOT independently confirmed

An earlier revision of this document claimed the specification states "exactly 50
buttons". **That was false and has been corrected.**

- The official `Appendix C - Product Specification` (p. 35) **does not state a
  button count at all**.
- The source that number came from also claimed "LCD 84 x 84 px", while the manual
  says **96 x 64**. That source is therefore unreliable.

What stands is only this: **our matrix has 50 occupied positions.** That is our own
measurement from the config, it stands on its own and needs no external
confirmation - but the apparent agreement with "50 buttons" is **not** independent
evidence.

### Buttons documented in the manual (pp. 5 and 9)

Summarised here; the full extraction, including the physical arrangement and
Logitech's own descriptions, is in [BUTTON-LAYOUT.md](BUTTON-LAYOUT.md).

| group | count | documented? |
|---|---|---|
| system: Off, Activities, Devices, Help, Glow | 5 | yes, by name |
| screen navigation: Menu, Info, Exit, Guide | 4 | yes, by name |
| D-pad + OK | 5 | yes, by name |
| Vol +/-, Ch +/- | 4 | manual describes a *rocker* in the singular; electrically 4 switches |
| Mute, Prev | 2 | yes, by name |
| transport: Play, Pause, Stop, Rec, Rew, Fwd, Skip, Replay | 8 | yes, by name |
| numeric 0-9, `*`, `#` | 12 | yes, by name |
| LCD paging arrows | 2 | mentioned, count not given |
| LCD side keys | ? | mentioned ("side LCD button"), count not given |
| coloured teletext keys | ? | existence documented (p. 9), count not given |

**Firmly documented: 40 buttons.** The remainder up to 50 is inferred from items
whose count the manual does not give. It is consistent, but it is not proof.

### Table order is NOT the physical layout

The codes in the config table are grouped by matrix row, but the columns are
permuted differently in each pair of rows:

| rows | column order |
|---|---|
| 1, 0 | 1, 3, 2, 5, 4, 7, 6 |
| 3, 2 | 1, 2, 3, 4, 5, 6, 7 |
| 5, 4 | 3, 2, 1, 7, 6, 5, 4 |
| 7, 6 | 1 ... 2, 3, 1, 6, 7, 4, 5 |

-> **The table order cannot be used to deduce which key is which.** The missing
link is a map from matrix position to physical button. See
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).

## 6. Prior art

The key thread is
[jaymzh/concordance issue #66](https://github.com/jaymzh/concordance/issues/66),
opened 2025-06-02 with *"Has anyone tried reverse engineering the binary blobs to
see if they can be recreated?"*

From that thread:

- **glenharris** (2025-06-05) - **the original Harmony developer**. Confirmed the
  architecture: a binary image written straight to flash, header plus pointer
  tables, and the remote being *"a Von-Neumann style computing device with a 16 bit
  instruction"*. Warned that reversing it would be *"very hard"* without
  documentation or more comparable samples.
- **guyman70718** (2025-09-18) - shared 4 sample .EZHex files (arch 8).
- **dorktoast** (2025-11-13) - drafted a formal request to Logitech to release the
  legacy Harmony configuration header files.
- **glenharris** (2025-11-13) - offered to go through the original codebase, pull
  out the relevant subset and submit it to Logitech for approval.
- **trn1ty** (2026-03-26) - proposed archiving the remaining EZ files on the
  Internet Archive.
- **glenharris** (2026-08-02) - proposed the decompile/recompile model this
  repository is built around, and noted that the remote itself is "incredibly
  dumb": IR data, device state machines, activities, menu UI and key bindings are
  *all* defined by the config.
- **glenharris** (2026-08-03) - on brick risk: transferring a bad config via
  concordance should confuse the runtime rather than brick the remote, and safe
  mode ignores the config, so a new one can be sent. With the caveat that a config
  could in principle contain an instruction sequence that makes the runtime write
  to arbitrary flash, including firmware or bootloader.

**No public solution of the format exists.** The findings above independently
agree with the original developer's description (AHCM/MCHA, pointer tables,
config_base 0x20000, 24-bit addresses, a bytecode-like section), which is a useful
cross-check that the model is on the right track.

## 7. Tools

See [`tools/`](../tools/). All are plain `python <script>.py`.

### Offline config analysis

| script | what it does |
|---|---|
| `split_ezhex.py` | split .EZHex into XML + blob, verify size and checksum |
| `strings_blob.py` | printable strings plus a map of where they sit |
| `hexdump.py` | hexdump of selected regions |
| `sections.py` | parse the 18-section table from the header |
| `records.py` | parse the 114-record array indexed by section 6 |
| `keytable.py` | parse the key table in record #0 |
| `find_text.py` | search for device names in all encodings |
| `ir_section.py` | analysis of sections 6 and 8 |
| `find_keytables.py` | generic key-table detector (ranked by uniqueness) |
| `compare_keytables.py` | compare tables across configs |
| `keymatrix.py` | test the keyboard-matrix hypothesis, render the grid |
| `diff_samples.py` | diff sample configs against each other |

### Live communication with the remote (read-only)

| script | what it does |
|---|---|
| `hid_listen.py` | dump HID capabilities; with an argument, listen for input reports |
| `hid_query.py` | **protocol core** - GET_VERSION, ReadMisc; command whitelist |
| `poll_state.py` | poll state variables, report changes |
| `capture_keys.py` | sample state during key presses, group by pause |
| `probe_kinds.py` | try every address space (EEPROM/STATE/RAM/REGISTER) |

**Safety:** `hid_query.py` carries a whitelist (`ALLOWED_FIRST_BYTE`) and hard
refuses `0x30 WRITE_FLASH`, `0x40 WRITE_FLASH_DATA`, `0xA0 WRITE_MISC` and
`0xD0 ERASE_FLASH`. Every other script goes through it. `hid_listen.py` opens the
device with `GENERIC_READ` only. Nothing in this repository writes to a remote.
