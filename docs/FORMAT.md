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
| 13 | 24 | u16 + 6 B | yes |
| 14 | 11 | u8 | no - 34 B of 55 |
| 15 | 5 | u8 | yes |

Sections **1, 2, 3, 4, 8, 16 and 17** contain no recognisable pointer table and
remain entirely opaque.

Section 10 was previously listed as unknown; it is a pointer array and nothing
else. Together these hold 685 addresses, and with the record headers and the
references found in record bodies the compiler recomputes 944 in total rather
than copying them.

Acceptance is deliberately strict, because a loose test finds pointer tables
everywhere for the reason given above: `0x02` is the most common byte in the file
precisely *because* it is the high byte of these addresses. Every address has to
land inside the config, and the table has to either fit its span exactly - the
declared count, the header length and the section boundary all agreeing - or,
failing that, ascend.

Section 13 is why the rule is phrased that way rather than simply demanding
ascent. Its 24 addresses ascend for 23 entries and then the last one jumps
backwards, so a strict monotonic test rejected the whole thing. The header reads
`24, 23, 1, 23`, which says as much out loud: 23 of one thing and 1 of another.

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

### Record bodies: eight blocks, one per matrix row

Each body is eight blocks indexed 0-7. A block opens with

```
16 <i> 03 00 <i*8> 00 <i*8> 60 08 8B 2F 03
```

and closes with `17`. That `i*8` is `row << 3` from the key-code arithmetic in
section 5g, so **there is one block per row of the keyboard matrix**. Blocks are
often empty; where they are not, the payload sits between the header and the
`17`.

That is as far as the bodies are decoded. What the payload means is unknown, and
it is the largest single gap left in the format.

### References hiding in the bodies - 124 of them

The same `0x16` also appears followed by a **valid 24-bit address**:

```
16 <u24 address>
```

There is no ambiguity with the block headers, whose next three bytes read as
`<row> 03 00`, around 0x0300 and never a valid config address.

124 turn up in the 525 config. Roughly one would be expected by chance, and they
arrive in pairs referring to the same target, so they are real. 123 point within
the region below 0xF35B and one into the section area.

These matter out of proportion to their number. They were sitting inside regions
the decompiler was copying as opaque hex, which means a length-changing edit would
have left every one of them pointing at whatever had moved into its place, with
nothing to report it. They are decoded and relinked now. **The lesson generalises:
assume more of them are still hidden in the sections that remain opaque.**

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

### A fifth key table, in a wider shape

Inside the largest record body (29,841 B at `0x01B3C`) sits another key table,
with a five-byte entry:

```
<u8 flag> <u8 code> <u16 target> <0x7F>
```

The flag reads `01` on all 51 entries. As with the main table it is preceded by a
count byte, `0x33` = 51.

What makes it interesting is not the shape but the contents: **the same 50
physical key codes, in exactly the same order as the main table.** The only
difference is the virtual entry, `0x06` in the main table against `0x17` here.
Every target reads 79.

| | main table | this one |
|---|---|---|
| offset | `0x0000FB` | `0x001BB9` |
| entries | 51 | 51 |
| entry width | 4 B | 5 B |
| physical codes | 50, identical set and order | 50, identical set and order |
| virtual code | `0x06` | `0x17` |
| targets | 0-46 plus four exceptions | all 79 |

A table with every key pointing at one target looks like a default or a catch-all
rather than a working mapping. Whether the flag byte is what distinguishes the two
shapes, or whether the wider form means something else entirely, is unknown.

It went unnoticed until now because the detector strode four bytes at a time, and
a five-byte table read that way dissolves into noise.

## 4f. Block headers - SOLVED

Earlier revisions described `16 <i> 03 00 <i*8> 00 <i*8> 60 08 8B 2F 03` as a
repeating constant of unknown meaning, counted at 472 occurrences. Both parts of
that were wrong.

It is a **block header**, twelve bytes, and its tail is not constant:

```
16 <row> 03 00 <row*8> 00 <row*8> 60 08 <u24 address>
```

The `8B 2F 03` that looked like part of a fixed pattern is an address. It only
appeared constant because the records read by hand happened to share a value; a
different record has `81 29 03` in the same place. Searching for the literal bytes
is what capped the count at 472 - there are **1072**.

Every one of the 1072 carries a valid address, and **every one points into section
17**. Not most: all of them. Section 17 is 3,096 bytes, was listed as unknown, and
has address-shaped bytes at 0.3x the chance rate, so it holds no pointers of its
own. It is a destination, not a source.

`<row>` is `row << 3` from the key-code arithmetic in section 5g, so **each block
belongs to one row of the keyboard matrix**. Most records carry eight blocks, one
per row; a few carry 16, 32, 40 or 48, and one carries none.

A block runs from its header to a `17` terminator. What sits in between is not
decoded and is the largest remaining gap in the format.

## 4g. Record trailers - SOLVED

The last seven bytes of a record:

```
00 <u24 into section 8> <u24 back into this record>
```

113 of the 114 records end this way. The second address lands on the record's own
start + 11. The first points into section 8, the suspected bytecode, which is what
supports the reading that **each record carries its own program**, as the original
developer described.

## 4h. Section 17 is LCD bitmaps - SOLVED

Every one of the 1072 block headers points into section 17, and there are only
**three distinct targets**, spaced 773 bytes apart. Section 17 is 3,096 bytes:
two bytes, four chunks of 773, two more bytes.

Each chunk is a bitmap:

```
02          format
u16 width   = 96
u16 height  = 64
bytes[768]  1 bit per pixel, row-major
```

96 x 64 is exactly the LCD size given in the manual, and 96 x 64 / 8 is exactly
the 768 bytes that follow. Rendering the pixels produces axis-aligned dotted rules
and a vertical divider rather than noise, which is the other half of the argument.
`tools/show_bitmaps.py` prints them so this can be checked by looking.

Of the four, three are referenced by block headers. The fourth is filled with
`0xFF` - erased flash - and one of the referenced ones is entirely blank.

So a block header is, among other things, choosing which screen layout to draw.

> The bitmap recogniser is deliberately restricted to plausible screen sizes:
> width and height both multiples of 8, width up to 256, height up to 128. A
> looser test that accepts anything whose pixel count divides by eight finds
> fifteen more "bitmaps" in this config - mostly claiming to be 256 pixels tall,
> because a `0x0100` landed where the height should be - and swallows four of the
> five key tables on its way past. Another model with a different screen will need
> these bounds widened on purpose.

## 4i. Section 10 indexes ACTION LISTS - SOLVED

Section 10 was written up above as "487 addresses and nothing else", which was
true and unhelpful. Every one of those addresses lands on a structure:

```
<u8 count>  <u16 operand> <u8 opcode>   [count]
```

An action list. The instruction is the same three bytes section 8 is built from,
so this is not a new encoding, it is the same one indexed differently.

The evidence is that the array **tiles**:

- 482 of the 486 consecutive pairs sit exactly `1 + 3 * count` apart, i.e. each
  list begins on the byte after the previous one ends
- of the remaining four, none overlaps; all four leave a gap, and three of them
  are at a jump between clusters
- the last list ends at `0x011FD7`, which is the byte the index itself starts on

Lengths run 2 to 7 instructions, overwhelmingly 2 (448 of 487). The 525 config
holds **1,043 instructions across 487 lists**.

### This is what a key table's `target` selects

`target` is an index into that array. So a key binding is
`button -> run action list N`, which is what the original developer's
`bindings: { button: executeActionList(n) }`
([discussion #5](https://github.com/trelowney/harmony-decompiler/discussions/5))
looks like after it has been through the compiler.

All 109 distinct targets across the five key tables index inside the array, and
107 of the 109 land on a list whose first instruction byte is `0x02`. No other
pointer table in the config can hold all of them - the next largest has 114
entries against a maximum target of 391.

That answers what had been the most blocking question here that was answerable
from data. The 51-entry table where every key reads 79 is every key running the
same list; the three per-menu tables run a different list per key; and the
382/388/391 at the end of those three are three different lists allocated one
per menu.

### Both architectures, same instructions

The recogniser is not told which section to look in. It accepts a pointer table
as an index of action lists only if the targets tile that way, and on that test
alone it finds them in arch 8 too: **1,318 lists, 3,311 instructions** in
`Update.EZHex`, in section 10 there as well as in the uncovered low region.

The opcode vocabulary is shared:

| opcode | arch 9 | arch 8 |
|---|---|---|
| `0x7F` | 235 | 828 |
| `0x7E` | 134 | 521 |
| `0x7D` | 200 | 239 |
| `0x7C` | 203 | 242 |
| `0x1F` | 57 | 252 |
| `0x07` | 20 | 640 |

Four opcodes carry three quarters of arch 9. **What any of them mean is not
known**, and that is now the interesting question rather than what `target` was.

**This table is arch 8 and arch 9 only, and it does not cover arch 12 and 14.**
@dannybloe reports `0x6C` as the third most common opcode on arch 14, 2832
occurrences in a Harmony 700 config, and it does not occur in the 525 sample at
all. The inventories overlap heavily, so the likeliest reading is one instruction
set with per architecture extensions, but anything derived from the table above
should be treated as arch 9 until an arch 12 or 14 file says otherwise.

### What can be said about the instructions without knowing what they do

`tools/actions.py` prints this; it is reproducible rather than a claim to be
taken on trust. Everything below holds in **both** architectures unless stated.

**`0x7F` references another action list.** Every one of its operands is a valid
index into the array: 235 of 235 in the 525, 828 of 828 in the arch 8 sample.
Nothing else in the file constrains it that way, so this is the closest thing to
a named opcode we have.

**The array holds two nearly disjoint populations.** 109 lists are bound to a
key by a key table and 76 are called by a `0x7F`, and only **2** are both. On
arch 8, 147 and 289 with the same overlap of 2. So a list is either an entry
point that a key binding enters, or a subroutine only ever reached from another
list - which is a real structural fact about the config and not an inference
about opcodes.

**`0x7D` opens a list and `0x7C` closes it.** All 200 `0x7D` instructions in the
525 are the first instruction of their list, and their operands are **200
distinct values**. All but one of the 203 `0x7C` are the last, and they take
only **9 distinct values**. A unique value at a fixed position is an identifier;
a small vocabulary at the end is a kind or a terminator.

**`0x7E` stays inside the record count.** 134 instructions, operands 1 to 113
against 114 records, 100% in range. The records are believed to hold the IR
data, so this is where an "emit this command" instruction would be expected.
Arch 8 does not reproduce this cleanly - 52% in range - so it is stated for
arch 9 only.

**Four opcodes always carry a negative number.** Read as signed 16-bit, `0x07`,
`0x0F`, `0x1F` and `0x3F` are negative in every single instance in both
architectures, and each clusters in its own band:

| opcode | arch 9 range | distinct |
|---|---|---|
| `0x07` | -6 to -1 | 5 |
| `0x0F` | -160 to -159 | 2 |
| `0x1F` | -6,144 to -249 | 28 |
| `0x3F` | -12,288 to -3,839 | 4 |

Those four opcodes are `0b0000111`, `0b0001111`, `0b0011111` and `0b0111111`,
which is unlikely to be a coincidence, and the magnitudes grow with the opcode.

**They are not negative numbers.** The firmware settles it: the handler for
`0x07` does not act on the operand, it **dispatches on it again**, comparing the
operand's low byte at `0x3D7` against `0xFF`, `0xFE`, `0xFD` and `0xFC` in the
same `MOVLW / SUBWF / BNC` idiom the opcode dispatch uses. And `0x07`'s operands
in real configs are `0xFFFA` to `0xFFFF` - a high byte of `0xFF` and a low byte
in exactly that range.

So these are **two-level opcodes**: the byte at `0x3D9` selects a family and the
operand bytes carry a sub-opcode rather than a value. `pic18dis.py --dispatch`
sees the same shape from the other side, reporting comparison chains against
`0x3D7` and `0x3D8` alongside the one against `0x3D9`.

An earlier revision read the values as signed and negative, which is arithmetic
that happens to be true of `0xFFFA` and says nothing.

This moved the 525 from 27.44% to **32.04%** decoded and arch 8 from about 2% to
between 4.3% and 5.1%.

## 4j. The firmware's opcode dispatch - FOUND

The instructions above are executed by the remote's firmware, which is a
Microchip PIC18 image any owner can pull off a working unit with
`concordance --dump-firmware`. No firmware is included in this repository - it
is Logitech's code, not ours to redistribute - but `tools/pic18dis.py`
disassembles one and will find the dispatch in it.

### Read the program memory, not the .EZUp

Get this wrong and every address is off by 4 KB, which is exactly what happened
here first time.

An `.EZUp` is the **firmware update image**, and it corresponds to program
memory **from `0x1000` onwards**: the bootloader in the first 4 KB is not part
of an update and is not in the file. Verified rather than assumed - `mcu[0x1000:
0x8000]` and `EZUp[0:0x7000]` are 28,672 bytes and **100.00% identical**. The
`.EZUp` then runs on to 64 KB with padding, because the PIC18LF4550 only has
32 KB and there is nothing past `0x8000` to describe.

The whole of program memory can be read straight off a connected remote
instead, which is what `tools/read_flash.py` does:

```
python tools/read_flash.py --check              # verify against known bytes first
python tools/read_flash.py 0x000000 0x8000 --out mcu.bin
```

That gives 32 KB, 2.1% erased, starting with a textbook PIC18 vector table.
Addresses below are in those coordinates. `--check` exists because a read
routine that has not been checked against something already known is not
evidence about anything; it reads the head of the config, where the answer is
sitting in `samples/harmony525/config.bin`.

**The interpreter is at `0x01C94` to `0x02346`.** The compiler emitted the same
three instructions for every case, so the decision tree can be read off
mechanically:

```
0E 7F     MOVLW 0x7F          ; the opcode being tested
5D D9     SUBWF 0xD9, W       ; against the byte in bank 3 at 0xD9
E3 07     BNC   <next case>    ; lower? try the next one
...       handler
D3 8A     BRA   0x013E4       ; back to the interpreter loop
```

So **the opcode lives in bank 3 at `0x3D9`, and its operand in `0x3D7` and
`0x3D8`** - which independently confirms the field order used above, `<u16
operand> <u8 opcode>`, rather than the other way round.

The tests are `>=` rather than `==`, forming a descending chain, so each
handler ends up covering exactly one value because the surrounding tests have
already bracketed it.

`tools/pic18dis.py --dispatch` extracts all 23 cases with the routine each one
calls. Which of the several comparison chains in the firmware is the dispatch
is decided from the configs rather than by eye: the right one is the chain
whose compared values overlap the opcodes that actually occur in an action
list, and it beats the runners-up 16 to 0.

### RE2 is the external flash chip select

Needed before any handler can be read, and it is measurable rather than a
matter of opinion.

`BCF LATE, 2` occurs 46 times and `BSF LATE, 2` 47 times, and they pair up:
43 of them bracket a span shorter than 0x300 bytes. The routines called inside
those brackets are exactly the most-called routines in the whole firmware -
`0x06576` (78 calls overall, 19 of them inside a bracket), `0x066A8`, `0x06560`,
`0x0658E`. `SSPBUF`, the SPI buffer, is touched in only two places in 32 KB, and
both sit inside this machinery.

What settles which peripheral it is: **six of those brackets are in the
bootloader**, below `0x1000`, which contains its own copy of the same SPI
routine shape. A bootloader has one reason to use SPI, and that is to read the
external serial flash the firmware image is stored in - which is the same flash
the config lives in, and which we have already confirmed byte for byte.

So a `BCF LATE, 2 ... BSF LATE, 2` bracket is an access to the flash this
repository decodes.

### How the firmware walks a config

Four routines do all of it, and naming them makes the handlers readable.

| routine | what it does |
|---|---|
| `0x07572` | read one byte over SPI |
| `0x06576` | read the next byte and advance the cursor |
| `0x06560` | read three bytes into `TBLPTR`, i.e. **follow a 24-bit pointer** |
| `0x066A8` | **seek to section N**, where N is the byte in `0x158` |
| `0x0672C` | **advance by `operand * 3 + k`**, operand in `0x15E`/`0x15F`, k in `0x15D` |

The cursor into the external flash is kept in **`TBLPTR`**, the PIC's own table
pointer, which is a neat trick: `TBLRD*+` costs one instruction and increments a
24-bit register, and the byte it loads is simply discarded. So `TBLPTR` is
being used as an address register for a device it was never meant to address.

`0x066A8` computes `4 * N + 11` before seeking, which is the section pointer
table at offset `0x0C` with four-byte entries, read one byte early. And
`0x0672C` is how a pointer array is indexed: three bytes per entry, past a
header of `k` bytes.

### Opcode 0x7F runs another action list - CONFIRMED

The handler calls `0x07EF4`, which is one flash bracket end to end:

```
07EF4:  BCF LATE, 2            ; select the flash
07EF8:  MOVLW 0x0A             ; section 10
07EFC:  CALL 0x066A8           ; seek to it
07F00:  MOVFF 0x2E4, 0x15E     ; the opcode's operand
07F04:  MOVFF 0x2E5, 0x15F
07F0A:  MOVLW 0x02             ; add 2
07F0E:  CALL 0x0672C           ; so: operand * 3 + 2
07F12:  CALL 0x06560           ; follow the pointer found there
07F16:  CALL 0x06576           ; read the count
07F1E:  MOVF 0x702, W          ; then read that many bytes
07F24:  CALL 0x01AF2
07F2A:  DECF 0x702, F
07F2C:  BRA 0x07F1E
07F2E:  BSF LATE, 2            ; deselect
```

**`operand * 3 + 2`.** Section 10's pointer table, as the decompiler works it
out from the file with no reference to any of this, is a `u16` count followed by
three-byte entries, so entry N begins `2 + 3N` bytes in. The firmware's
arithmetic and the file's layout are the same expression.

So `0x7F` is *run action list [operand]*, and it is confirmed by arithmetic
rather than inferred from behaviour. It also explains the two populations in
[§4i](#4i-section-10-indexes-action-lists--solved): 109 lists a key binding
enters, 76 that only a `0x7F` reaches.

### Opcode 0x7E selects a record - CONFIRMED the same way

Same shape, different section:

```
05BB6:  BCF LATE, 2
05BBA:  MOVLW 0x06             ; section 6
05BBE:  CALL 0x066A8
05BC2:  MOVFF 0x3E7, 0x15E     ; the operand
05BCC:  MOVLW 0x03             ; add 3
05BD0:  CALL 0x0672C           ; so: operand * 3 + 3
05BD4:  CALL 0x06560           ; follow it
05BD8:  MOVFF TBLPTRL, 0x3E4   ; and keep the address
```

Section 6 is `<u16 count> <00> <u24 address>[count]` per the decompiler, so its
entries begin `3 + 3N` bytes in. Again the same expression.

Section 6 indexes the 114-record array, and `0x7E`'s operands never leave the
range 1 to 113 in any config. So **`0x7E` selects record [operand]**, and since
those records are where the IR data is believed to live, this is the
instruction a button press ultimately goes through to emit a command. Note that
this is the claim withdrawn earlier arriving properly: not by recognising a
routine, but by two independent derivations of the same offset arithmetic.

### The same check, four sections over

Running the firmware through Ghidra's decompiler makes the rest of the handlers
readable, and the pattern above holds everywhere it can be checked. Each
handler loads a section number, an operand and an addend, and the addend is
always the width of that section's header as the decompiler works it out from
the file:

| opcode | section | firmware adds | header per the decompiler | entries |
|---|---|---|---|---|
| `0x7E` | 6 | 3 | 3 (`u16` count + a pad byte) | 114 |
| `0x7F` | 10 | 2 | 2 (`u16` count) | 487 |
| `0x73` | 11 | 2 | 2 (`u16` count) | 22 |
| `0x72` | 14 | 1 | 1 (`u8` count) | 11 |

Four sections, four agreements, from two derivations that share no input. That
is worth more than the individual opcodes: it checks the decompiler's reading of
pointer-table headers, which until now rested on one sample and an argument
about which interpretation fitted.

**`0x73` is confirmed** as selecting entry [operand] of section 11 - it is the
same shape as `0x7F`, seek, index, follow, use.

**`0x72` is not** simply that, despite the arithmetic matching. Its routine
indexes section 14 and then enters a loop comparing further operand bytes
against what it reads, so it searches rather than looks up. That also resolves
the objection raised earlier, that `0x72`'s operands run to 1,805 while section
14 holds 11 pointers: only part of the operand is the index and the rest is
what is being matched.

### 0x7C and 0x7D are a queue, with 0x7D outranking 0x7C

Decompiled, the twins from the previous section read:

```c
// 0x7D
DAT_023a = DAT_023c;  DAT_0239 = DAT_023b;      // the operand
if (enqueue()) {
    if (DAT_016c < 2) { DAT_0238 = 2; start(); }
    return 1;
}

// 0x7C
DAT_0239 = DAT_023d | 0x40;  DAT_023a = DAT_023e;   // operand, bit 6 set
if (enqueue()) {
    if (DAT_016c == 0) { DAT_0238 = 1; start(); }
    return 1;
}
```

`enqueue()` writes the two operand bytes one at a time through a shared routine
and returns whether there was room. `DAT_016c` holds what is currently running:
**`0x7D` starts if that is below 2, `0x7C` only if it is zero.** So the two
instructions put work in the same queue at two priorities, and `0x7D` can
pre-empt `0x7C` but not the reverse.

Which fits their positions: `0x7D` opens an action list with an operand unique
to that list, `0x7C` closes one with a small vocabulary of values.

The assembly underneath, for anyone checking the above:

```
0x7D:  MOVFF 0x3D7, 0x23C   MOVFF 0x3D8, 0x23B   CALL 0x026F6
0x7C:  MOVFF 0x3D7, 0x23E   MOVFF 0x3D8, 0x23D   CALL 0x02718
```

`0x026D0` is the enqueue and `0x026AC` the start, per the section above.

The operand of these two is not a flat number: bit 6 of its high byte is set by
`0x7C` before use, and on the config side every one of `0x7D`'s 200 operands has
a high byte of 0 to 3. There is a field in there.

### It is an accumulator machine

The handlers that are short enough to be inlined do not appear as functions -
the whole interpreter is one - so these were read from the disassembly. Between
them they settle what the language is.

`0x0333:0x0334` is a **16-bit accumulator**, and `0x02558` copies it into a
working pair at `0x0717:0x0718` before an operation.

| opcode | what it does |
|---|---|
| `0x7A` | **load**: `0x3D7:0x3D8 -> 0x0333:0x0334`, the operand straight into the accumulator |
| `0x79` | **add**: copy to the working pair, then add the operand's low byte |
| `0x78` | **multiply** by the operand, 16 by 16 |
| `0x77` | **divide** by the operand |
| `0x71` | load `0x0332` into the working pair |
| `0x6F` | copy the accumulator, then test whether it is zero and branch |

Which is a small, ordinary accumulator machine sitting underneath the config,
exactly as the original developer described the remote: *"a Von-Neumann style
computing device with a 16 bit instruction"*.

### How the two-level opcodes are actually decoded

`0x1F`'s handler does it in five instructions and removes all doubt:

```
01F78:  MOVLW 0xF0
01F7A:  SUBWF 0x3D8, W        ; is the operand's high byte >= 0xF0?
01F7C:  BNC 0x01F8E           ; no: a different form
01F7E:  MOVFF 0x3D7, 0x3D3    ; low byte  -> parameter
01F82:  MOVLW 0x0F
01F84:  ANDWF 0x3D8, W        ; low nibble of the high byte
01F88:  MOVWF 0x3D2           ;            -> sub-opcode
```

So an operand of `0xFn__` means **sub-opcode `n` with parameter `__`**, unpacked
into `0x3D2` and `0x3D3`. `0x3F`'s handler then branches on `0x3D2`, which is
why it appeared to test "another operand byte" - it is testing the sub-opcode
this decode produced.

`0x0F` does the same on the low byte instead, and `0x70` runs a chain of
`XORLW` comparisons against the same nibble. The families differ in which byte
carries the sub-opcode, which is presumably what the original developer meant
by the bit pattern changing radically between subsystems.

That fully accounts for the values that looked like negative numbers: an
operand of `0xFFFA` is sub-opcode 15, parameter 250.

### The second level is large

Counting the comparison chains, there are **24 cases dispatching on `0x3D7`**
and **35 on `0x3D8`**, against 23 on the opcode byte itself. Both second-level
chains are organised the same way: a run of range tests on the high nibble
(`0xF0`, `0xE0`, `0xD0` and downwards, as far as `0x60`), and equality tests
below that.

So the instruction set is roughly three times larger than the opcode byte
suggests, and most of it is still unread. Several of these cases call routines
already identified from the other direction - `0x3D8` case `0xF4` calls the
section 14 search that `0x72` uses, and cases `0xF3` and `0xF7` call the routine
`0x7B` uses - so the families overlap in what they can do rather than
partitioning it.

This is the obvious place to keep going, and it needs no hardware: it is all in
an image any owner can dump.

### There is arithmetic in here

Three handlers share a preamble at `0x024E4`:

```c
save_accumulator();                              // 0x0333:0x0334 -> 0x0717:0x0718
DAT_000b = DAT_03d7;  DAT_000c = DAT_03d8;       // the operand
DAT_0006 = DAT_0333;  DAT_0007 = DAT_0334;       // the accumulator
```

and then:

- **`0x78` multiplies.** `0x07BC0` is a 16 by 16 bit multiply, four byte
  products summed with carries into `0x0004`-`0x0007`. There is no mistaking it
  for anything else.
- **`0x77` divides.** `0x017D0` is sixteen iterations of shift the dividend,
  compare against the divisor, subtract and set a bit. Restoring division,
  written out longhand.
None of these three occurs in the 525 config, so none of it was visible from
our own data. They do occur in arch 8, where `0x77` appears 19 times, `0x79`
four times and `0x78` once, which is what a second architecture is for.

**`0x74` looks like digit entry**, on weaker evidence. `0x0718E` shifts a
24-bit value left four bits, ORs a nibble into the bottom, increments a counter
and compares it against a limit, finishing when the limit is reached. That is
what accumulating typed digits looks like, and a remote that lets you key in a
channel number needs exactly it. Recorded as a reading, not a finding.

**`0x80` reads section 13**, through `0x04B20`, which is the same seek and index
shape as the others.

**`0x3F` dispatches on another operand byte**, at `0x03D2`, and branches on
whether it is 0 or 1 - more evidence for the two-level encoding above, though
this one uses a different byte again.

### Reproducing the decompilation

Ghidra imports the program memory image as a raw binary. No project file is
included here; it is a few seconds of work:

```
analyzeHeadless <proj> harmony -import mcu.bin \
    -processor PIC-18:LE:24:PIC-18 -loader BinaryLoader -loader-baseAddr 0x0
```

It finds 588 functions and the decompiler handles this code well. The whole
interpreter, dispatch and all handlers, is one function spanning `0x01C86` to
`0x02401`.

Its output was checked against the routines already read by hand before any of
it was believed, and it agreed - which is the only reason the rest of this
section is written as fact.

### Which section each opcode reaches

`tools/pic18dis.py --sections` follows each handler through the call graph and
reports the section numbers it seeks to. Five opcodes come out:

| opcode | section | offset added | what the section is |
|---|---|---|---|
| `0x7E` | 6 | +3 | index of the 114-record array |
| `0x7F` | 10 | +2 | index of the action lists |
| `0x72` | 14 | not seen | 11 pointers |
| `0x73` | 11 | not seen | 22 pointers |
| `0x80` | 13 | not seen | 24 pointers |

Only the first two also show the indexing arithmetic, which is why only those
two are called confirmed. For the other three the section is identified but how
the operand is used in it is not, and one of them argues against the obvious
reading: `0x72`'s operands run from 13 to 1,805 while section 14 holds 11
pointers, so whatever it does there, it is not a plain index. `0x80` never
occurs in any config we have.

Two cautions are worth repeating here because both were mistakes first. A
linear walk has to stop at an unconditional branch, since handlers sit end to
end and each ends with a `BRA` back to the loop; without that, every opcode
appears to use every section. And following calls into shared utilities reaches
most of the firmware in three steps and produces a full table that means
nothing. A result from this tool that is not sparse is wrong.

### Asking the same question with a graph tool

`pic18dis.py --graph out.json` writes the call graph in the format
[graphify](https://github.com/safishamsi/graphify) reads, which turns these into
ordinary queries and, more usefully, makes the route explicit:

```
graphify path "opcode 0x72" "section 14" --graph out.json
  opcode 0x72 --handled_by--> routine 0x01E00 --calls--> routine 0x07AF0 --seeks--> section 14
```

Two things to know before trusting an answer from it.

**A path containing a backwards hop is not a call route.** `path` treats the
graph as undirected whatever the `directed` flag says, so it will happily route
"A calls C" and "B calls C" into a claim that A reaches B. Those hops are drawn
`<--` and any path containing one should be thrown away.

**Check the route in the listing.** The first version of the emitter attributed
every call between one entry and the next to that entry, and reported a
confident three-hop route from opcode `0x07` to section 6 that does not exist -
`0x07`'s handler is a long second-level dispatch and had swallowed calls
belonging to other paths. Blocks now stop at unconditional transfers and the
false route is gone, but the general point stands: this is a way of generating
a hypothesis quickly, not a way of confirming one. Everything it suggests still
has to be read.

Used that way it earns its keep. It reproduced all five hand-derived opcode to
section results exactly, and the one extra answer it offered was false, which is
about the ratio to expect.

### What the handlers are is still open

Each case is six bytes of test followed by a handler that shuffles the operand
into some routine's arguments and calls it, so the table above is a list of
23 addresses that each mean one opcode. Naming them is reading those routines,
which has not been done yet.

> A caution, recorded because it nearly went into this document as a finding.
> An earlier pass claimed `0x7E` was confirmed as "send IR", on the strength of
> its handler calling a routine that bit-bangs `PORTC` bit 2 with delays. The
> routine is real and that is what it does, but it is at `0x6B6C` and the
> handler calls `0x5B6C`. The two got confused because that pass disassembled
> an `.EZUp` as though it started at address zero, which put every absolute
> address 0x1000 out. The claim is withdrawn.
>
> The lesson is the same one the round-trip test keeps teaching. The reasoning
> was fine and the disassembler was right; what was wrong was an unexamined
> assumption about where the bytes lived, and nothing in the working would have
> caught it. Check the base address against something known before trusting any
> address derived from it.
>
> `0x7E` did turn out to be about IR, and the section above says so on much
> better evidence. That does not retrospectively justify the first attempt. A
> guess that lands is still a guess, and the way to tell the difference is
> whether anything would have contradicted it.

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

### Arch 8 decompiles too

The decompiler handles both architectures, and all four arch 8 samples round-trip
byte-identical, as does the resize test. What differs is only in the header:

| | arch 9 | arch 8 |
|---|---|---|
| magic | `AHCM` | `TPTP` |
| end-of-header marker | `CMAH` | **`WLWL`** |
| end marker | `MCHA` | `DKDK` |
| section pointers | 19, the last one null | 20, **two of them null** |

Two things there are worth flagging because guessing would get them wrong. Arch 9
mirrors its magic to make its markers and arch 8 does not - `WLWL` has nothing to
do with `TPTP`. And arch 8's section table contains a **null pointer in the
middle**, at slot 8, meaning that subsystem is absent; reading the table until the
first zero, which is the obvious implementation, silently truncates it from 20
entries to 8.

Counting the slots, since an earlier version of this table got it wrong. Both
architectures end on a null: slot 18 on arch 9, slot 19 on arch 8. So arch 8 has
**two** nulls, slot 8 and the trailing one, and this document previously said one
because it was counting the middle null and ignoring the trailing one. Where other
people's notes say 19 slots for arch 9 and this one says 18, that is the same
difference and not a disagreement about the file.

### The same container goes on to arch 12 and 14

Established by @dannybloe over a thirteen sample corpus and checked here against
the arch 8 and arch 9 files, which he does not have. All four architectures use
this container, with a different four letter cookie each:

| arch | cookie | end marker | marker after the pointer table |
|---|---|---|---|
| 8 | `TPTP` | `DKDK` | `WLWL` |
| 9 | `AHCM` | `MCHA` | `CMAH` |
| 12, 14 | `GSPM` | `PTYY` | `LWJL` |

The pointer table is one table with per architecture insertions, so **section
numbers carry across by index**. Arch 9 and arch 14 are the base layout at 19
slots; arch 8 inserts a null at slot 8 to make 20; arch 12 inserts that same null
plus a real section at slot 18 to make 21. So section 10 here is raw slot 11 on
arch 8 and arch 12.

Checked from this side rather than taken on trust: raw slot 11 of `Update.EZHex`
holds a `u16` count of 1318, which is the number of action lists the decompiler
reads out of that file by an unrelated route, and `2 + 3 * 1318` is exactly the gap
to raw slot 12.

Two further consequences worth having:

- `base` is recoverable from the file as `end_addr - (end marker - cookie)`, which
  gives `0x020000` on both architectures here. concordance lists arch 9's
  `config_base` as `0x820000`; bit 23 looks like a flag rather than an address bit.
- the `u32` at `+8` is **not** an architecture id. Arch 9 and arch 14 both carry
  `0x1400`. The architecture is stated outright by section slot 1, a seven byte
  record of `<u8 arch> <u8 arch> <u16 version> 00 00 00`. The 525 reads
  `09 09 16 0d 00 00 00`, so arch 9, version word 3350. That matters for a config
  read off a remote over USB, which arrives with no XML header to consult.

What the two share, decoded and verified by round trip on both: the container, the
blob header shape, `config_base`, 24-bit pointers and their tables, the
`0xFEED ... 0xBEEF` name table, key tables, the record array with its headers and
trailers, and the `16 <u24>` references.

What arch 8 does not appear to have: block headers, and therefore no LCD bitmaps
found by way of them.

Only about 2% of an arch 8 config decodes, against 27% for the 525, but that is
mostly because the files are six times larger rather than because less is
understood.

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

### What the target is

The original developer described the compiler's last stage as converting every
action *"to 16 bit commands (bit pattern changed radically depending upon the
action subsystem)"*, with the config built from menus that carry `bindings`
mapping a button to an action, alongside `irDevices`, `stateVariables`,
`actionLists` and `sounds`
([discussion #5](https://github.com/trelowney/harmony-decompiler/discussions/5)).

That reframes these tables. A key table is a **menu's binding table**, and the
`u16` is not a plain ordinal.

**It is an index into the array of action lists in section 10** - see
[§4i](#4i-section-10-indexes-action-lists--solved), which is where the evidence
for that sits. A key binding runs a list of instructions, so the question that
remains is what the instructions do, not what the number means.

The observations that led there, kept because they are still the shape of the
data anyone will meet:

| where | what the targets look like |
|---|---|
| `0x0000FB`, 51 entries | 0-46 in sequence, plus 95, 155, 179, 311 |
| `0x001BB9`, 51 entries | **79 on every single entry** |
| `0x00E4D8` / `0x00E586` / `0x00E60F` | scattered, 77-391, different in each table |
| last entry of each of those three | 382, 388, 391 - **ascending across the three tables** |

A table where every key carries the same target is every key running the same
action list, so list 79 is a good candidate for "do nothing". The three
ascending values at the end of the per-menu tables, all on code `0xAF`, are
three different lists allocated one per menu.

### Virtual events

Not every code in a key table is a physical key. Five codes appear that do not
fit `0x80 | (row << 3) | column`: `0x01`, `0x02`, `0x05`, `0x06` and `0x17`. The
original developer confirmed these are **system virtual events** rather than
buttons, describing them as *"'tricky' things that allowed the remote to look
like it was smart"* ([discussion
#6](https://github.com/trelowney/harmony-decompiler/discussions/6)).

-> **Do not put them in the matrix.** Earlier revisions of this document treated
`0x06` as an unexplained 51st entry; it is not an anomaly, it is a different kind
of thing.

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

## 5g. Key codes are keyboard-matrix addresses - CONFIRMED FROM THE FIRMWARE

```
code = 0x80 | (row << 3) | column        row 0-7, column 1-8
```

Harmony 525 matrix (`.` = not wired):

```
      c1   c2   c3   c4   c5   c6   c7   c8
  r0  81   82   83   84   85   86   87    .
  r1  89   8A   8B   8C   8D   8E   8F    .
  r2  91   92   93   94   95   96   97    .
  r3  99   9A   9B   9C   9D   9E   9F    .
  r4  A1   A2   A3   A4   A5   A6   A7    .
  r5  A9   AA   AB   AC   AD   AE   AF    .
  r6  B1   B2   B3   B4   B5   B6   B7    .
  r7  B9    .    .    .    .    .    .    .
```

**8 rows x 7 columns**, 50 occupied positions, zero collisions. Column 8 is
unwired. Arch 8 (720/785/88x) fills column 8 and has gaps elsewhere - a
different wiring, the same scheme. That is why codes agree across models.

This section used to say "column 0-7, column 0 unwired". Same bits, and the
config alone cannot choose between the two readings: no 525 code has
`code & 7 == 0`, and running both conventions over the arch 8 samples produces
equally ragged grids, so that data does not discriminate either. The firmware
settles it for the 525 - the scan returns a column number that is a bit index
**plus one** - and 5f's evidence that the encoding is shared then carries it to
arch 8. See 5h.

The 51st entry of the main table is code `0x06` (target 155). It does not fit the
matrix; it is not a physical key but a virtual event.

## 5h. Bit 7 is an event type, not part of the coordinate - SOLVED

The keypad scanner lives at `0x0701C` in the 525's program memory, reached from
`0x070DA` -> `0x070FA` -> `0x070C4`. Each level has exactly one caller.

It is **not** a conventional row/column scan. There is a single sense line,
`PORTB` bit 7, and both axes are driven at once, so the scanner does a binary
search: drive a group of lines, ask "did anything in this group close", halve.
Six probes instead of 64.

- **Columns**, `0x0715A`: `MOVWF LATD`, active high, masks `0x0F 0x03 0x01 0x02
  0x0C 0x04 0x08 0xF0 0x30 0x10 0x20 0xC0 0x40 0x80`, returns bit index **+ 1**,
  so 1 to 8.
- **Rows**, `0x07156`: `MOVWF LATD`, then a pulse on `LATE` bit 0 latches the
  value into an external register and `LATD` is released to `0xFF`. Active low,
  masks `0xFE 0xFD 0xFB 0xF7 0xEF 0xDF 0xBF 0x7F` and their group parents.
  Returns `row << 3` directly, so 0, 8, 16 ... 56.

`ADDWF` at `0x070BE` sums the two:

```
scancode = (row << 3) + column          0 means nothing is pressed
```

The event byte is built at `0x07160`, which ORs a flag into the stored scancode
and pushes the result into the queue at `0x019DC`:

```
07160  MOVLB 0x2
07162  IORWF 0xDE, W          ; flag | scancode
07164  MOVLB 0x3
07166  MOVWF 0xC3
07168  GOTO  0x019DC          ; event queue
```

Four call sites supply three different flags:

| flag | call site | meaning |
|---|---|---|
| `0x80` | `0x0712E`, right after the new code is stored | **press** |
| `0x40` | `0x0711C` and `0x07150`, when a previous code exists and the current one differs or is gone | **release** |
| `0xC0` | `0x070F2`, gated on the flag at `0x1C6` | third event, most likely **repeat** |

So the `0x81`..`0xB9` codes in key tables are **press events**. Bit 7 is not part
of the matrix address at all. Three things follow, and all three check out
against the 525 config:

- `0x80` cannot occur, because scancode 0 means "no key". It does not occur.
- No code in any of the five key tables has bit 6 set, so **the config binds
  presses only**, never releases or repeats.
- Column 8 would give `code & 7 == 0`. No such code exists, which is why the 525
  matrix is 7 columns wide.

### The queue has eight producers

`0x019DC` is the single entry point to the event queue. Eight sites push to it:

| site | code pushed |
|---|---|
| `0x07160` | `0x80`/`0x40`/`0xC0` \| scancode - the keypad |
| `0x0549E` | `0x17`, gated on `INTCON3` bit 1, an external interrupt |
| `0x0558C` | `0x0F` or `0x10`, chosen by a comparison against `0x168` |
| `0x0559A` | `0x18 + n`, a range rather than one value |
| `0x05736` | `0x28` |
| `0x05754` | `0x26` or `0x27`, chosen by `PORTB` bit 5 |
| `0x03028` | a variable, from `0x706` |
| `0x01A00` | a variable, from `0x3C4` |

That explains where the non-matrix codes in key tables come from. The 525 config
uses `0x01 0x02 0x05 0x06 0x17`; they share one namespace with the keypad events
and simply are not generated by the keypad. `0x17` is the external interrupt.
The other four are not yet attributed.

### What this still does not give you

The physical labels. The firmware knows the electrical matrix; it has no idea
what is printed on a key. Unlike arch 14, the 525 has no hardwired key
combination probing fixed intersections, so there is no anchor to be had that
way either. The chain is now:

```
physical key -> ??? -> scancode -> event code -> action list -> device:command
```

and only the first arrow is missing. Two ways to close it, both cheaper than
before:

1. **Ask an owner.** With action lists decoded (5i), each key resolves to a
   device and a command number. Somebody who knows what their own remote does
   only has to name a few keys; the rest falls out of the structure.
2. **Two pin numbers per key.** Every key is the intersection of one `LATD` pin
   and one latch output, so buzzing out an opened 525 is a short job rather than
   a blind 50-key survey.

## 5i. What an activity key table entry does - SOLVED

Following `code -> target -> action list` for the three activity tables gives
almost the same two-instruction shape every time:

```
0x8A -> action list #210:   0x7D:550   0x7C:513
```

Both operands split into two bytes:

```
0x7D  <device> : <command>       send this command
0x7C  <device> : <flag>          flag is 1 in 178 of 203 cases
```

Checked across **all 487 action lists**, not only the ones that suited the
hypothesis: for both opcodes the high byte is only ever 0, 1, 2 or 3, and this
config declares exactly three devices (`TV_Panasonic`, `Amplifier_Genius`,
`XBOX_360`). The two opcodes then differ in their low byte exactly as the
reading predicts: `0x7D` spreads evenly, the way a command index would, while
`0x7C` is `1` in 87% of cases, the way a flag would. That asymmetry is the real
evidence; the high-byte range on its own would be much weaker.

In this sample, key table 3 drives device 1 and tables 2 and 4 drive device 2,
so tables are per activity and an activity is bound to a device. Tables 0 and 1
are fallbacks: 47 of the 51 entries in table 0 run the same action list.

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
- **glenharris** (2026-08-03, this repository's discussions) - a brain dump of
  how the config was built, written up before a fortnight away. The parts that
  changed what is in this document: the compiler's last stage turns every action
  into a **16-bit command whose bit layout depends on the subsystem** (#5), key
  tables therefore hold encoded actions rather than indices; codes that do not fit
  the matrix are **system virtual events** (#6); the header's pointer table points
  at **per-subsystem data, some of it holding relative rather than absolute
  pointers** (#1); and the firmware is Microchip PIC, built with the Microchip and
  CCS compilers from C, so Ghidra should get somewhere on it (#7).

  He also sketched the shape of the source format - menus with items and
  bindings, `irDevices` with per-command carrier frequency and pulse timings,
  `stateVariables` with enter and exit bindings, `actionLists`, `sounds` - and
  the limits the compiler enforced, such as fewer than 16 devices and state
  variables. None of that is verified here yet, so it is recorded as testimony
  rather than as finding, but it is testimony from the person who wrote the
  original toolchain.

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
| `actions.py` | opcode census over the action lists, with what constrains each operand |
| `pic18dis.py` | PIC18 disassembler; `--dispatch` finds the interpreter and its opcode table |
| `read_flash.py` | read a region of a connected remote's flash, `--check` first (read-only) |
| `export_hexpat.py` | write an ImHex pattern annotating a specific config |
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
