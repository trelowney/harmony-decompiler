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
indexed by section 6, see §4b and §4d. The first of those records starts with a
rooted tagged binding list (§4e).

### A span is where a subsystem's table lives, not a fence around its data

> **Qualified 2026-08-24**, by @glenharris in
> [pull request #30](https://github.com/trelowney/harmony-decompiler/pull/30#issuecomment-5401221693),
> describing how the compiler wrote these files.

Reading each section table entry as a span, and taking everything inside that
span to belong to that subsystem, is right most of the time and is how most of
this document was written. It is a habit of the compiler, not a rule of the
format. What the compiler actually did:

- each subsystem was asked to binarize itself, in order, and it laid its
  structures into whatever config space it could find;
- a subsystem writes its own pointer table first, then binarizes its children.
  A child that an earlier subsystem already wrote is not written again, it is
  pointed at, so one structure can be reachable from two subsystems and belong
  to neither span exclusively;
- a child not yet written gets a three-byte forward reference, an unresolved
  pointer, and goes on a pending list. Once that child is binarized and its
  address is known, the compiler goes back and fills the reference in.

So a subsystem can own data that sits below its own table as well as above it,
and a span can contain bytes that belong to something else. The spans line up
with the subsystems because the subsystems ran in order, which is a different
statement from the spans being boundaries.

### What the 525 measures, and section 4's spare bytes named

Section 4's own records, read as four-byte entries, give a count of 30 and stop
125 bytes into a span of 2,551. What the other 2,426 bytes are is now settled,
and they are not unknown: this decompiler has been reading them all along, as
43 infrared symbol blocks, 5 symbol tables and 4 groups. That is 1,680 plus 134
plus 612 bytes, which is 2,426 exactly, with nothing left over. Infrared data
belongs to section 5's subsystem, and it is sitting in section 4's span.

Sharing is real, and common. Taking every reference this decompiler resolves on
the 525, 6,544 regions and 4,476 edges reaching 1,716 distinct targets, **156
targets are reached more than once**, and 16 of those are reached from a span
other than their own. The three most referenced live in section 17's span and
are reached 472, 384 and 224 times from the record array below the section
table. An infrared symbol table in section 4's span is reached 206 times from
the same place.

The firmware agrees, from the other side. It reaches a config through exactly 16
`seek to section N` call sites, every one of them a literal, covering sections 2
to 16 with 13 used twice, and no path writes that section number from a
variable. **Sections 0, 1 and 17 are never sought at all.** Whatever lives in
section 17 is therefore reached only by pointers from elsewhere, which is what
the reference count above shows from the config side. A span whose table the
firmware never consults is as clear a case as this format offers of a span that
is not a boundary.

> An earlier version of this subsection said no measured case of sharing existed.
> That was written after looking only at section 14, where there genuinely is
> none: 22 items and 22 distinct targets on the 525, 3,810 and 3,810 on the 650.
> Absence along one route is not absence.

What this does not affect: the round trip, which works on bytes and never asks
which subsystem a byte belongs to. What it does affect is how a new structure
gets argued for. Lying inside span N is not evidence of belonging to subsystem
N. The evidence is a pointer that reaches it, and which table that pointer came
out of.

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

Sections **1, 2, 3, 4, 8, 16 and 17** contain no recognisable pointer table of
their own. Later semantic readers account for their public-525 contents; the
remaining opaque bytes are all below the section table in the record array.

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

## 4c. Section 8 looks like bytecode - CONFIRMED, and closed in 4k

Kept as written because it is where the reading started; section 4k finishes it
and section 8 now closes to the byte. One sentence below did not survive: the
`0x9E, 0x9F, 0xA6, 0xA7` that "appear further on with the same shape" are not
instructions at all. The leading action list ends after 34 bytes, and everything
after it is the mode-page binding lists. All 216 entries in those lists carry a
tag of `flags | code`, and across the whole config the codes take exactly four
values: 30, 31, 38 and 39, which is where `0x9E`, `0x9F`, `0xA6` and `0xA7` come
from. `tools/verify_525_semantics.py` prints the set.

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

The pointer lands on the entry at the **end** of a mode record. Its u24 back
pointer names the tagged list where that mode starts, then a u16 page count and
one page-record address per page follow. The shape of a mode entry and a page
record is
[@dannybloe](https://github.com/dannybloe)'s, from
[harmony-explorations](https://github.com/dannybloe/harmony-explorations); it is
reproduced here because the rooted screen walker independently lands on the same
records:

```
mode entry:  00  u24 tagged-list-start  u16 pages  u24 page[pages]
page record: u24 binding-list           u24 screen-program
```

There are 114 modes and 135 page records. The material between a mode's tagged
list and entry is primarily its page screen programs and page records, not an
unknown keyboard-matrix payload.

The back pointer also supplies an exact root for the physical binding list at
the start of every mode. Each narrow or wide list closes from its own count; its
end is independently one of the screen-program roots named by a page record or
section 11. All **114 lists close**, occupying **2,413 bytes**. Changing a back
pointer, a count, or the following program root makes the reader reject the
whole interpretation.

Two mode entries are followed by packed runs of the same counted lists:
`0x001B3C-0x001CBF` (45 lists, 387 bytes) and
`0x00E10D-0x00E68F` (98 lists, 1,410 bytes). Their objects close individually;
the runs also land exactly on roots supplied by other readers. The 143 objects
are one copy for each of the 135 pages plus all eight section-9 lists. Every
copy agrees structurally with its page list. The only permitted operand change,
opcode `0x7F`, must select an action list with an identical instruction
signature. A changed count, section-9 root, page pairing, action-list meaning or
independent upper root makes the complete pool reading fail closed.

The physical-list relation is not arch-9-only evidence. Danny Bloemendaal's
reader applied to the local arch-8 `Update.EZHex` and arch-14
`Harmony_650.EZHex` samples finds respectively 103/103 and 265/265 physical
lists ending at rooted screen programs; their section-9 analogues also close
(9/9 and 10/10). The byte layouts differ, so this is an outside witness for the
root relation rather than a grammar copied from the 525.

### Two more byte-pattern readings withdrawn

The previous release of this decompiler emitted 1,072 regions of kind
`block_header` and 113 of kind `record_trailer`. Reading the screen programs
from their stated roots supersedes both, and the current decompiler emits
**zero of either**:

```sh
python tools/roundtrip.py --all      # counts every region kind it emits
```

The apparent trailer is a screen program's `00` end opcode with the next page
record, six bytes, sitting immediately behind it. The apparent block header is
two screen instructions, proved in 4f below. Both byte patterns were real. The
boundaries drawn around them were not, and a shape scan cannot tell the
difference, because the shape is all it has.

> This is the third one. An earlier pass had already withdrawn 124 regions of
> kind `reference`, which matched `16 <u24>` inside an opcode 4 whose y
> coordinate happened to be `0x16`. Same failure, same cause. What finally
> separates a real structure from a coincidence here is not a better pattern but
> a **root**: something the firmware is known to start reading from.

## 4e. ROOTED TAGGED BINDING LISTS - SOLVED

The first mode entry points back to a narrow tagged list starting at `0x0000FA`.
After its one-byte count, entries start at `0x0000FB`:

```
<u8 key code> <u16 target> <0x7F>
```

**51 entries**, and the count is explicitly declared by byte `0x33` = 51 at the
list root. The list ends exactly at `0x0001C7`, the rooted screen program named
by the mode's page. No key code repeats. This is not a shape scan: the mode entry
names the start, the count closes the object, and a separate reader names its
successor.

Key codes fall in the range `0x81-0xB9` (plus one exception, `0x06`) and form
obvious groups: `0x81-0x8F`, `0x91-0x9F`, `0xA1-0xAF`, `0xB1-0xB9`.
Targets are mostly a contiguous run 0-46, with the exceptions 95, 155, 179, 311.

-> The main table holds **51 entries: 50 physical keys + 1 virtual** (code `0x06`,
see §5g). What each code physically *means* is **still unknown** - sniffing it
over USB was tried and does not work (§5d).

### The same binding tags in the wide shape

One list in the first packed pool starts at `0x001BB7` in the wide form, with a
five-byte entry starting at `0x001BB9`:

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
| entry offset | `0x0000FB` | `0x001BB9` |
| entries | 51 | 51 |
| entry width | 4 B | 5 B |
| physical codes | 50, identical set and order | 50, identical set and order |
| virtual code | `0x06` | `0x17` |
| targets | 0-46 plus four exceptions | all 79 |

A table with every key pointing at one target looks like a default or a catch-all
rather than a working mapping. Whether the flag byte is what distinguishes the two
shapes, or whether the wider form means something else entirely, is unknown.

It went unnoticed because the detector strode four bytes at a time, and a
five-byte table read that way dissolves into noise. The current reader does not
detect either by stride: it reaches them through mode, page and section-9 roots.

## 4f. The alleged block header is screen opcodes 22 + 3 - CORRECTED

Earlier revisions called this a twelve-byte keyboard-matrix block header:

```
16 <row> 03 00 <row*8> 00 <row*8> 60 08 <u24 address>
```

The bytes are real; the boundary is not. `0x16` is screen opcode 22 carrying a
one-byte row, and `0x03` is screen opcode 3 with its nine operands. The two
`row * 8` values are y coordinates, and `60 08` is `96, 8`: the width and height
of one display band.

A scan of the whole blob finds 1,080 of these runs. **Every one** is also
reached from a stated screen root, as opcode 22 immediately followed by opcode
3. Not most of them. The old decompiler emitted 1,072 rather than 1,080 only
because it looked inside record bodies and eight of them lie outside.

So the keyboard-matrix reading is gone. Both domains number things 0 to 7 and
both multiply by eight, which felt like corroboration and was coincidence.

Rooted traversal now emits every pointer-free instruction, not only the opcode-3
and opcode-4 references. The public 525 has **2,762** such instructions spanning
**4,510 formerly opaque bytes**: opcodes 0, 5, 16, 17, 22 and 23. Opcode 5 keeps
its terminated glyph run as a separate writable region. Pointer-bearing control
opcodes 18, 19 and 20 remain raw until their embedded targets have a writer.

### What the nine operands are, and which way round

Rooted traversal finds **1,114** opcode-3 draws. They come in exactly two
shapes:

| operands | count | image |
|---|---:|---|
| `(0, row*8, 0, row*8, 96, 8)` for row 0-7 | 1,080 | three page backgrounds |
| `(0, 12, 0, 0, 96, 1)` | 34 | the all-ink bitmap |

The first four operands are two coordinate pairs, and the 1,080 cannot say which
pair is the destination, because in every one of them the two are equal. The 34
can, and they settle it. Here is one, in context, from the Devices menu at
`0x009372`:

```
op22 row=1                              select the band y=8..15
op3  (0, 8, 0, 8, 96, 8)  <background>  its slice of the page background
op4  x=0 y=0                            the title, whose descender reaches in
op3  (0, 12, 0, 0, 96, 1) <all ink>     <- this one
op16 font=0
op5  x=0 y=13                           first menu entry
op5  x=81 y=13
op23 transfer                           push the band to the panel
```

The draw sits inside the band that covers y=8 to y=15, between a title that ends
at y=10 and text at y=13. A one-pixel line at **y=12** is a rule under the
heading. The other reading puts it at y=0, in a band that was transferred two
instructions earlier and can no longer change. So:

```
03 <dest x> <dest y> <src x> <src y> <width> <height> <u24 image>
```

Destination first. `tools/render_525_screens.py` had these the other way round
until this was noticed; the 1,080 background strips hid it perfectly, because
swapping two equal numbers changes nothing. All 34 rules were being drawn at the
top edge of the screen instead of under the heading.

## 4g. The alleged record trailer is a page record - CORRECTED

The old seven-byte shape was:

```
00 <u24 into section 8> <u24 back into this record>
```

The zero is the end opcode of the screen program in front of it. The six bytes
behind it are one page record, which is a binding-list pointer and a
screen-program pointer and nothing else. 113 of the 114 mode records appeared to
finish this way because of how the compiler laid them out, not because there is
a trailer grammar. Reading the page table instead recovers all 135 page records
and leaves no `record_trailer` regions at all.

The address that looked like "a pointer into section 8, so each record carries
its own program" was the binding-list pointer of the following page. The
conclusion happened to be close to true anyway, which is the uncomfortable part:
a wrong structure can support a right-sounding sentence for weeks.

## 4h. Section 17 is LCD bitmaps - SOLVED

All 1,114 rooted opcode-3 picture draws point into section 17, across **four
distinct targets** spaced 773 bytes apart. Section 17 is 3,096 bytes:
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

All four are referenced, and the split is not what an earlier revision of this
document assumed:

| address | draws | pixels set | what it is |
|---|---:|---:|---|
| `0x01267C` | 224 | 38 | dotted rule and divider |
| `0x012981` | 384 | 38 | dotted rule and divider |
| `0x012C86` | 34 | 6,144 of 6,144 | every pixel ink |
| `0x012F8B` | 472 | 0 | every pixel paper |

The all-ink one is **not erased flash**, which is what this document previously
called it. It is a source of ink: all 34 of its draws copy a 96x1 slice of it to
y=12, which is the rule under a menu heading, and they are the only 34 draws in
the config that do not follow a row select. Nothing is ever copied from it that
is not a solid run. The all-paper one, drawn 472 times, is the plain background
for pages that want no furniture at all.

Reproduce the table with:

```sh
python tools/render_525_screens.py --out screens/   # and then look at them
python tools/verify_525_semantics.py                # counts every draw
python tools/show_bitmaps.py                        # prints the four as text
```

> **What these are for, added later.** Section 4l reads the screen programs, and
> three of the four are *page backgrounds*. Opcode 3 draws them a 96x8 strip at a
> time, eight strips to a page, and the text is drawn on top. So the dotted rule
> and the divider are not furniture sitting beside the labels; they are the layer
> the labels sit on. An earlier note in the project's working files described them
> as separators and took their emptiness of words as a puzzle. There was no
> puzzle: words are never in a bitmap here, they are glyph runs.

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

### All sixteen seek call sites, and what each does next - MEASURED

The sixteen were counted long before they were read. This is the map, and the
enumeration was re-run here independently of the report that produced it:
**sixteen direct calls to `0x066A8`, every argument a literal, sections exactly
2 to 16 with 13 twice, none of 0, 1 or 17.**

| section | seek at | in routine | what reads it |
|---:|---|---|---|
| 2 | `0x01024` | `0x01010` | reads a leading `u16`, follows the next `u24`, iterates the pointed data |
| 3 | `0x0427C` | `0x04274` | seeks and returns: a cursor-positioning entry point, its two callers do the reading |
| 4 | `0x05DB0` | `0x05DA0` | `u24` and `u16` header, then a linear scan of `u8`/`u24` entries, no index helper |
| 5 | `0x05032` | `0x0502A` | two RAM-indexed `0x066F4` selections, a `u8` discriminator dispatched on 3, 2 and 5 |
| 6 | `0x05BBE` | `0x05B6C` | opcode `0x7E`: record `[operand]`, `k=3` |
| 7 | `0x04494` | `0x04482` | indexes by a `u8` read **before** the seek, `k=0`, copies the `u24` to RAM without following it |
| 8 | `0x0240A` | `0x02402` | `u8` count immediately, then a real bounds check on RAM `0x3DA`, `k=0` |
| 9 | `0x07A3A` | `0x07A1A` | indexes by RAM `0x2DB`, `k=1`, follows, then a per-record `u8` count |
| 10 | `0x07EFC` | `0x07EF4` | opcode `0x7F`: `k=2` by the 16-bit operand |
| 11 | `0x0472C` | `0x04724` | opcode `0x73`, from `0x01DF4`: `k=2` by RAM `0x2C3` |
| 12 | `0x05B16` | `0x05B10` | seeks and tail-`GOTO`s the byte reader: the whole routine returns the section's `u8` count |
| 13 | `0x047BA` | `0x0479A` | four `u16` header reads, then scans by runtime index `0x703`, `k=8` |
| 13 | `0x04B26` | `0x04B20` | opcode `0x80`: `k=8` by RAM `0x1AF` |
| 14 | `0x07AF8` | `0x07AF0` | opcode `0x72`: `k=1`, then searches records against both operand bytes |
| 15 | `0x053E4` | `0x053DE` | the parameter block, groups 2 and 3 only |
| 16 | `0x06CDC` | `0x06CCE` | indexes RAM `0x2CF`, `k=1`, follows, reads several record scalars |

Seven of the sixteen were already identified: 6, 10, 11, 13 twice, 14 and 15.
The other nine had no firmware-side reader named, and now have one each. They are
marked as shapes rather than names on purpose: "routine `0x07A1A` indexes section
9 by RAM `0x2DB`" is what the instructions say, and calling section 9 a
particular subsystem on that basis would be the fitting this document keeps
having to withdraw.

`0x05DA0` is worth one note. Nothing `CALL`s it; `0x05E96` tail-enters it with a
`GOTO`. So it is reachable, unlike the configuration-bit routine of 5r, which
nothing reaches at all.

#### The firmware trusts the counts, and that is a fact about writing

Across all sixteen, **not one reader validates a declared count or length against
a literal.** The only bounds test in the set is section 8's, and it is dynamic
rather than declared: `0x0240E` reads the `u8` count into `0x014`, `0x02416`
loads the RAM index `0x3DA`, and `0x0241A` subtracts and branches away if the
index is not below it. That protects the firmware from its own index. Nothing
protects it from the file.

This generalises what section 15 showed on one routine. @dannybloe's arch 12 and
arch 14 images enforce a group's length against a number the build expects, and
fall back to compiled constants when it disagrees. **Arch 9 does not do that
anywhere.** A config that states a wrong count is believed, so on this
architecture the check has to be in the writer.

#### Four of the eight unnamed readers are named by an opcode - MEASURED

Six of the fifteen sections above came back with a name and nine with only a
shape. Every one of the six was named the same way: the reader's index turned
out to come from an opcode operand whose meaning was already known. So the
question for the rest is not what the section looks like. It is **which
instruction last writes the RAM location the reader indexes with**, and that has
an answer in the image.

Tracing all eight, section 2 having been named separately as the writeable
flash, **four reach an opcode operand and four do not**:

| section | where the index comes from | the instructions that say so |
|---:|---|---|
| 7 | action opcode `0x10`, exclusively | `0x06576` at `0x04482` reads the byte after the opcode, `MOVWF 0x700` at `0x04488`; dispatch test `0x0467C`, handler `0x046AC` |
| 9 | main opcode `0x1F`, secondaries `0xFF` and `0xE8`, or an ordinary stream byte | `0x3D7` to `0x3D0` at `0x02054` to `0x340` at `0x01C04`, and `0x3D7` to `0x341` at `0x02228`; in the loop a marker `0xFE` selects `0x340` and `0xFC` selects `0x341`, otherwise `0x708`, loaded from `INDF0` at `0x01B42` |
| 8 | main opcode `0x3F`, and also firmware literals | `MOVFF 0x3D7,0x3DB` at `0x02040`, then `MOVFF 0x3DB,0x3DA` at `0x0249E` or `0x024C8`; a separate path writes `0x04` or `0x01` at `0x04FB0` |
| 16 | main opcode `0x1F`, secondaries `0xF3` and `0xF5` | `MOVFF 0x3D7,0x2CF` at `0x02148`, call at `0x0214C`, dispatch test `0x01F6A` |
| 3 | its two callers, neither of which indexes | `0x03C74` advances by 10 and copies a `u24`; `0x04072` advances by 2 and computes from the next three bytes |
| 4 | a firmware event key | `MOVFF 0x3EF,0x3F1` at `0x05E8E` and `MOVFF 0x3F0,0x3F2` at `0x05E92`; the producers write `0x0000`, `0x0019`, `0x001A` and `0x001B` |
| 12 | its callers | the reader tail-returns the leading `u8`; `0x057FE` stores it at `0x704` and `0x0585E` at `0x702`, and neither reads it again |
| 5 | not traceable | `0x712` has 13 direct writers and `0x713` has 8. The promising pair at `0x0438C` and `0x04390`, inside opcode `0x02`'s handler, saves `TBLPTR` **after** its operands are consumed: a return cursor, not an operand |

Section 5 is recorded as untraced rather than assigned. This document has
carried four readings that were internally perfect and wrong, and every one of
them came from a shape that looked right.

#### And a name read out of the firmware has to survive the file

If section 7 is indexed by opcode `0x10`'s first operand byte, then every `0x10`
operand in every config has to be a legal index into section 7's array. That is
evidence of a different kind from the trace that produced the claim, which is
the point of collecting it.

The control comes first, because a harness that cannot reproduce a known answer
says nothing about an unknown one. Opcode `0x7E` against section 6 gives
operands 1 to 113 against 114 records, which is the number
[opcode 0x7E](#opcode-0x7e-selects-a-record---confirmed-the-same-way) already established.

| opcode | section | uses | index values | array | out of range |
|---|---:|---:|---|---:|---:|
| `0x7E`, control | 6 | 134 | `0x01` to `0x71` | 114 | **0** |
| `0x10` | 7 | 244 | `0x00` to `0x04` | 5 | **0** |
| `0x1F`, `0xFF` and `0xE8` | 9 | 11 | 0, 3, 4, 5, 6, 7 | 8 | **0** |
| `0x3F`, `0xC0` to `0xCF` | 8 | 0 | none emitted | 11 | **0** |
| `0x1F`, `0xF3` and `0xF5` | 16 | 0 | none emitted | 0 | **0** |

**Zero out of range, and that is two confirmations rather than four.** Sections
7 and 9 are exercised with indices that vary, which is working content rather
than scaffolding. Sections 8 and 16 are never emitted at all: the firmware path
is there and this compiler never took it, and section 16's own array is empty so
no legal index could have been emitted. Those two rows are honest
non-occurrence, and they are not evidence for the name.

Section 9's eight entries with indices 0 and 3 to 7 are the binding list table
of [4p](#4p-an-action-is-three-bytes-and-section-9-is-the-key-binding-chain---solved), reached
from the other end. The reader was found from the seek site without knowing what
it was; the opcodes that feed it were read out of the dispatcher; and the array
they index is the one that section already describes.

#### The arch 9 corpus is one file

The tables above have one row because there is one row to have. Every arch 9
container reachable here, the public sample, its `.bin` twin and the offline
backup, is the same 78,486-byte blob, `sha256 bba8f7f0efd1...`. They are three
representations of one observation.

So **every claim in this document that holds "across arch 9 configs" holds
across one config.** The two rows above that read zero out of range read zero
out of one. That is not true of arch 8, where four containers exist and a count
that is constant across them means something, and it is why a check spanning
architectures is worth more here than one that does not.

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
[§4i](#4i-section-10-indexes-action-lists---solved): 109 lists a key binding
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

> Finished in 4o, from the firmware. The last clause above is wrong: **none** of
> the operand is an index. The whole 16-bit value is a key and the routine
> searches for it.

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

> Two of these are answered further down and the table is left as it was, because
> what it records is what this tool reported. Section 4k reads `0x80` as the base
> of a family: `0x80 | n` writes state variable *n*, and the sample uses `0x8E`
> through `0x96`. Opcode `0x80` itself is absent for the ordinary reason that it
> would name variable 0. Section 4l names section 11: it is the screen-program
> table, so `0x73` runs one.

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

## 4k. Three more opcodes, and section 8 closing to the byte - SOLVED

`tools/verify_525_semantics.py` asserts everything in this subsection against the
sample on every run, so these numbers are not a snapshot of one afternoon.

### Section 8 is a leading action list plus every page's binding list

The section runs `0x0319F8` to `0x031E36`, 1,086 bytes, and it is exactly two
things back to back:

| | bytes |
|---|---:|
| one ordinary action list, 11 instructions | 34 |
| the 135 mode-page binding lists, packed | 1,052 |
| | **1,086** |

The 135 page-list addresses are unique, contiguous, and the last one ends on the
section boundary, so there is no padding and no unclaimed record. The leading list
is `0x7E:0, 0x7F:47..50, 0x7E:6, 0x7F:184, 0x7E:8, 0x7F:184, 0x7E:10, 0x7F:184`,
which is startup behaviour, but calling the whole section "startup" is too narrow
for what is in it.

Those 135 lists hold 216 tagged entries between them and every tag is a press
event whose scan code is 30, 31, 38 or 39. This is page navigation, not the
50-button keypad census.

### `0x75` sounds a tone

The handler at `0x01DC4` copies the two operand bytes to `0x1FA`/`0x1F9` and calls
`0x056D8`, which shifts the low byte twice into a delay, takes the high byte as a
loop count, and calls `0x05798` twice per iteration. `0x05798` is `BTG LATA, 2`
followed by the delay helper, so two calls are one square-wave period.

```
0x75  { high byte = cycles, low byte = half-period }
```

The unit of the low byte depends on the clock calibration and is not claimed.

### `0x80 | n` writes state variable `n`

The dispatcher at `0x01C9A` clears bit 7, keeps the index, copies the operand to
`0x1A9`/`0x1AA` and calls into the section 13 machinery already identified. The
sample closes it from the other side: 24 state variables, 23 of them one byte
wide, 86 writes across the reachable lists using opcodes `0x8E` through `0x96`, no
index out of range, and **no write to a narrow variable with a non-zero high
operand byte**. That last one is the check worth keeping, because it is the one
that would fail if the reading were wrong.

### `0x7C` is `QueueDelay`, per device - CORRECTED

An earlier revision of this section concluded that `0x7C` carries a quantity
*rather than* a delay. That was wrong, and it is worth leaving the reasoning
visible rather than quietly deleting it.

[@glenharris](https://github.com/glenharris), who designed this firmware,
explained it in
[discussion #14](https://github.com/trelowney/harmony-decompiler/discussions/14).
The remote has one queue holding commands and delays for every device, and it
behaves as if each device had its own. A delay at the head of one device's
subqueue holds that device up while commands for other devices keep going. That
is what buys a pause after a command, or a guaranteed silence before the next
one, without stalling the whole remote.

The measurements were right; only the inference from them was wrong. Over all
203 uses in the sample's action lists the high byte is always a valid IR group
index, 9, 68, 62 and 64 uses across the four groups, and the low byte is
distributed `{0: 17, 1: 178, 5: 5, 15: 2, 95: 1}`. What that distribution rules
out is milliseconds, not a delay: eight bits cannot hold a useful millisecond
range, so the value is encoded. Reading 88% ones and a ceiling of 95 as "this is
a count of something" was a guess dressed as a conclusion. The exact unit is
still open, and those five values are now the evidence for working it out rather
than an argument against the name.

## 4l. Where the menu text lives - SOLVED

Searching a config for its own menu labels finds nothing. Not in ASCII, not in
UTF-16, not with bit 7 set. For a while this document treated that as evidence
that the text was somewhere else entirely, perhaps in the firmware. It is not.
**The text is in the config, drawn glyph by glyph, and the glyph numbers are
local to the font that draws them.** There is nothing to search for.

The format's designer said as much in discussion #1 - that menus are a stream of
render instructions and text should be reached through a command naming a font and
a string. This is that, made executable.

### Section 11, and a screen program per page

Section 11 is a `u16` count and 22 program pointers. In this sample all 22 are
wrappers: `opcode 17 <u16 operand> <u8 opcode>` then `opcode 0`, that is, queue one
action instruction and stop. The pages you actually see are reached through the
pointer in each section 6 mode page record, not through these.

All 114 modes hold 135 pages between them and 135 distinct program pointers. Every
one decodes to its terminator with no unknown opcode and no overrun:

| opcode | operand bytes | uses | what it does |
|---:|---:|---:|---|
| 0 | 0 | 135 | end |
| 3 | 9 | 1,114 | draw a rectangle of a picture at a screen position |
| 4 | 5 | 1,053 | x, y, then a `u24` naming a glyph string |
| 5 | *string* | 179 | x, y, then the glyph string inline, `0x00` terminated |
| 16 | 1 | 244 | select font set *n* from section 7 |
| 20 | 3 | - | jump |
| 22 | 1 | 1,080 | select display row 0..7 |
| 23 | 0 | 1,080 | transfer the selected row to the panel |

Opcodes 18 and 19 are switches on a state variable and end a linear path.

### The framing, read out of the firmware

Every page is exactly eight row blocks:

```
22(row)  3(the row's 96x8 background strip)  [font and text draws]  23(commit)
```

Ghidra on `mcu.bin` closes both ends of that. Opcode 22's handler at `0x046D6`
reads one byte into `0xD9` and calls `0x038EC`, which computes `0xC0 = row`,
`0xC1 = row * 8`, `0xC2 = row * 8 + 7`: an 8-pixel window, nothing more. Opcode
23's handler at `0x046E8` drives a control line low, loads width `0x60`, calls
the pixel transfer at `0x03898`, and drives it high again. So 22 selects and 23
commits, on this architecture.

> **Two corrections to that paragraph, both from
> [@dannybloe](https://github.com/dannybloe)**, who checked these four addresses
> against a separate 525 image he read over USB rather than taking them on
> trust. First, opcode 23 brackets its transfer with **`LATE` bit 2 as well as
> `LATA` bit 5**, and restores both; an earlier revision here named only
> `LATA.5`, which is not enough to drive the panel by hand. Second, `0x03898`
> emits `0xB0 | page`, a page-address command. That settles what a "row" is: not
> a menu line and not a touch region, but one of the panel's eight 8-pixel
> pages. Eight of them make the 525's 96x64 screen.

> **A third correction, also his, to what that one command implies.** An earlier
> revision here read `0xB0 | page` as identifying an **SSD1306**. It does not:
> several controller families share that command, so one byte cannot name a
> part. Danny followed the panel's whole bring-up sequence instead, and it comes
> out **ST7565 / UC1701 class**.
>
> That sequence is in this repository's own image too, at `0x0357A..0x03600`: a
> run of `MOVLW` values fed to the one-byte command writer at `0x03C12`.
>
> ```
> C0  common output normal        A2  LCD bias
> 89  RAM address control         25  resistor ratio
> 81  set contrast, plus a value  2F  power control, all three stages
> 24  resistor ratio              F8  booster ratio, plus a value
> E2  internal reset              40  display start line 0
> AE  display off                 AF  display on
> A4  all points normal           A5  all points on
> ```
>
> Every one of those is an ST7565 or UC1701 command, `0x89` sitting in the
> `0x88..0x8F` RAM-address-control band that UC1701 defines and the SSD1306 has
> nothing at. **The SSD1306's own mandatory bring-up is missing**: no `0xA8`
> multiplex ratio, no `0xD5` clock divide, no `0x8D` charge pump. `MOVLW 0x8D`
> does not occur **anywhere in the 32 KiB image**, which is a stronger statement
> than not finding it in one routine. Naming the exact part would still mean
> getting the panel out of the case; the family is as far as the firmware goes.

The operands agree from the data side without the firmware: the opcode 3 that
follows every opcode 22 begins `00, 8*row, 00, 8*row, 96, 8`, in all 1,080.

**This is arch 9 only.** On arch 12 opcode 22 is a call and opcode 23 is its
return, which is a different instruction wearing the same number. Do not carry
these meanings across.

### Section 7 is the fonts

A `u16` count, then a pointer per font set. Five of them here. Each set is:

```
+0x00  u8   glyph height in pixels
+0x01  u8   first glyph code
+0x02  u8   glyph count
+0x03  u24  glyph[count]      NULL where the config never draws that code
```

A glyph is one byte of width, then one row at a time until a `0x00` appears where
a row leader would be. A row is `0x20 | n`, *n* being how many command bytes
follow, and each command is `kind << 4 | (count - 1)`:

| kind | meaning |
|---:|---|
| `0x5` | *count* literal pixels, two bits each, big endian |
| `0x6` | a run of *count* background pixels |
| `0xA` | a run of *count* ink pixels |

Pixel value 2 is paper and 1 is ink. **This encoding is
[@dannybloe](https://github.com/dannybloe)'s finding**, published 7 August 2026;
the renderer here implements it rather than having worked it out.

### It closes, and then it reads

Walking all 135 programs while tracking the font opcode 16 last selected resolves
1,053 external strings and 179 inline ones, 9,018 glyph references in total, with
**zero out of range and zero landing on a NULL glyph**. That is the closure. What
makes it worth having is the next step: `tools/render_525_screens.py` draws all
135 pages as BMPs, and the sample's device list comes back as `XBOX 360`,
`X96 Box`, `TV Panasonic`, `Amplifier Genius`.

Which also settles a smaller thing this document had wrong by implication. `X96
Box` was missing from the state-variable names, and that was never evidence the
device was missing. Its name only ever existed as the glyph run that draws it.

A glyph number means nothing outside its own font set, so a decompiled config must
keep the raw numbers even once the characters are known. Adding text to a config
means building a set and numbering it, not looking a character up.

## 4m. The checksum the remote actually checks - SOLVED

The `.EZHex` XML carries a `CHECKSUM` tag, and section 1 describes it. That is the
one the host software checks. **There is a second one, inside the blob, and it is
the one the remote checks.**

```
the u16 immediately before the four-byte end marker
seed 0x4321
XOR successive little-endian u16 words
excluding the stored word itself and the marker
```

The 525 firmware loads `0x21, 0x43` at `0x04E8A`, XORs each fetched word at
`0x04EF8`, and compares both stored bytes at `0x04F54`. The algorithm reproduces
the stored word in every sample here:

| sample | stored |
|---|---|
| 525 `config.EZHex` | `0xD145` |
| arch 8 `Update-1` | `0xDDF6` |
| arch 8 `Update-2` | `0xC59D` |
| arch 8 `Update-3` | `0xBB10` |
| arch 8 `Update` | `0xCF6F` |

This is worth being blunt about, because it is the most consequential error this
project has made. Those two bytes used to pass through as ordinary section 17
residue. Any edited config generated before this was found would have carried a
stale word, the remote's own validator would have rejected it, and the round-trip
test could never have said so: a round trip compares against the original, and the
original is always self-consistent. **The oracle was structurally incapable of
seeing this class of bug.**

What did see it was somebody else's parser. `@dannybloe`'s reader reported the
trailer as invalid on the first edited artifacts produced here; the seed and the
range were then found in the firmware and confirmed against all five samples.
`compile_blob()` now recomputes the word before the outer XOR.

## 4n. Arch-9 class-5 IR pointer graph - SOLVED structurally

Base slot 5 begins with a one-byte device count followed by one `u24` pointer
per infrared group. On this 525, one group is one device. Each group is:

```text
+0  u8   zero
+1  u16  command count
+3  u24  record address[command count]
```

The record address lands on the class byte, seven bytes into a variable header:

```text
-7  u8   zero
-6  u24  carrier period in ns
-3  u24  carrier on-time in ns
+0  u8   class = 5
+1  u24  pointer back to the header start
+4  u8   group count, 1..16
+5  u24  body pointer[3 * group count], NULL slots significant
```

Class 5 then forms a four-level pointer graph:

```text
body:         u24 symbol_table; u16 n; u8 index[n]
symbol table: u8 count; u24 symbol[count]
symbol block: u16 count; u16 duration[count]; u16 zero
```

**None of the layout below is this project's.** The header, the body, the symbol
table, the symbol block and the firmware reading behind them were published by
[@dannybloe](https://github.com/dannybloe) in
[harmony-explorations](https://github.com/dannybloe/harmony-explorations) and
verified there against firmware. It is written out here because the rest of this
section depends on it, and because a reader should be able to check the
relocation work without leaving the page. What is added here is that closure,
and nothing about the format itself. The public 525 contains:

| layer | count |
|---|---:|
| IR groups | 4 |
| record headers | 200 |
| class-5 bodies | 380 |
| symbol tables | 5 |
| symbol blocks | 43 |

Bodies, tables and symbols are shared between records, so each is emitted once
and every reference to it is symbolic. That took the resize proof from 3,609 to
**5,015 recomputed pointers**, and the structurally decoded share of the 525
from 41.5% to **76.5%**.

### The failure that this closes, in detail

Before this, every one of those pointers was opaque. Lengthen anything in the
config and the symbol tables move, but the bodies keep the old absolute
addresses, so all 200 IR commands decode as garbage. Everything else still
passes: both checksums recompute, all 135 screens render pixel-identical, group
and mode counts close, and an independent high-level reader accepts the file.
Nothing in the file complains, because nothing in the file is wrong except the
meaning.

Two generated artifacts in this project's working files were built and checked
before this was found, and both are kept as the record of it rather than
deleted. What exposed them was expanding an *original*, untouched IR record
through an independent reader after the edit, which is now a standing check:

```sh
python tools/roundtrip.py --resize
# +13 B absorbed, 5015 pointers relinked, structure unchanged,
# 135 screens pixel-identical, 200 IR records exact
```

The last clause is the new one. It expands all 200 records before and after the
shift and compares carrier, slot population including NULL slots, and every
duration word.

> This is the same shape of mistake as the trailer checksum in 4m, and it was
> caught the same way: not by our tests, which compare a file against a version
> of itself, but by a reader written by somebody else against the same format.
> Two for two. Where a third is hiding, the honest answer is that we do not
> know, which is why the opaque share of the file is still worth counting.

### Writing one, offline

`tools/class5_ir_encoder.py` builds a self-contained relocatable record from
already-normalised duration words. It packs literally, one symbol per unique
complete stream, which is simple to audit and is certainly not what Logitech's
compiler does. `tools/verify_525_class5_encoder.py` repacks the six learned X96
NEC signals out of the public sample and requires an exact decode afterwards:

```sh
python tools/verify_525_class5_encoder.py
# PASS: 6 X96 golden vectors, 12 refusal rails
# command 09: NEC 01 FE 4E B1, 472 bytes
```

`tools/clone_525_device.py --place-x96-record-9` goes one step further and puts
a generated record into a config at a computed address, where an independent
reader expands it to the same NEC frame as the original.

What none of that shows is that a remote would accept any of it. It also does
not say what the three header slots mean for a signal captured from a real
remote, or whether the dictionary compression Logitech uses is an optimisation
or a requirement.

> That last question is now partly answered. A generated config **has** been
> written to a real 525 and read back byte for byte, and the original was
> restored afterwards. What that did and did not prove is section 5o. It does
> not make any of this safe to run on your remote.

## 4o. Opcode 0x72 searches section 14, it does not index it - SOLVED

Section 4j left `0x72` half open. Its handler seeks section 14, and section 14
holds 11 pointers, but `0x72`'s operands in the 525 sample are 13, 525, 1293 and
1805. The note there guessed that part of the operand was an index and the rest
was matched. The firmware says none of it is an index.

The handler is at `0x01E00` and it does almost nothing itself:

```text
01E00  RCALL 0x02548        fetch the operand
01E02  MOVFF 0xFF3, 0x2D3   operand low byte
01E06  MOVFF 0xFF4, 0x2D4   operand high byte
01E0A  MOVFF 0x3D8, 0x2D2   a context byte from the dispatcher
01E0E  CALL  0x07AF0        the worker
```

`0x07AF0` selects the section by number, the same way the other handlers do,
then searches:

```text
07AF4  MOVLW 0x0E           14
07AF6  MOVWF 0x158          the section the reader will open
07AF8  CALL  0x066A8
07AFC  MOVFF 0x2D2, 0x15C   the context byte becomes a reader parameter
07B16  CALL  0x06576        read one byte: how many records follow
07B1A  MOVWF 0x31
07B1E  loop:
07B24    RCALL 0x07BA2      read a u16 into 0x733/0x734
07B28    MOVF  0x2D3, W  /  XORWF 0x33, W  /  BNZ next
07B32    MOVF  0x2D4, W  /  XORWF 0x34, W  /  BNZ next
07B3A    found = 1
07B40  next:
07B40    RCALL 0x07BB0      advance, decrement the counter
07B42    BRA loop
```

Both operand bytes are compared, so the key is the **whole 16-bit operand**. If
that pass finds nothing, a second one at `0x07B48` walks the same table again
comparing the operand against consecutive values as a lower and an upper bound,
so an entry can be a range as well as an exact key.

The record stride falls out of the two helpers. `0x07BA2` points the destination
at `0x0733` and jumps to `0x0658E`, which is a two-byte reader; `0x07BB0` loads 3
into the reader's skip count and calls `0x06678`. **Two bytes read plus three
skipped is five bytes per record.**

That is worth stating separately because it agrees with the file. Walking
section 14 in the 525 sample as `u8 count` followed by five-byte records parses
all 11 entries exactly, with counts

```text
1, 4, 1, 1, 4, 1, 1, 1, 4, 3, 1
```

and every record's three skipped bytes are a `u24` pointer into one shared array
that starts at `0x0F2CC` and steps by five. Two derivations that share no input,
the same stride. The owner's sample has four devices and three activities, which
is a tempting reading of the three fours and the three, but it is only a
coincidence of counts until something joins them, so it is left as an
observation.

What this does not answer is what `0x72` is *for*. It resolves a key to a record
and the records point somewhere; naming that is still open. Section 5o has a
concrete reason to care.

> **Answered 2026-08-24, in 5p.** They point at render streams. Each item in a
> section 14 record is a `u16` key and a `u24` pointer, and every one of those
> pointers is a member of section 11's own list of render streams: 22 of 22 on
> this sample, and not one miss in twelve configs across arch 9 and arch 14. So
> `0x72` takes a key, finds the record, and gets back a screen to draw. The
> reading of the record above is one byte out of phase; 5p has the corrected
> layout, and it is @glenharris's, from PR #30.

## 4p. An action is three bytes, and section 9 is the key binding chain - SOLVED

Everything a config does to a key press goes through one three-byte instruction.
Until now this file described the opcode half of it (4i, 4j) and treated the
operand as an index into something. On a whole family of opcodes the operand
*is* the instruction, and that is what this section is.

Read out of a 525 firmware image. Addresses are into the image as
`tools/pic18dis.py` disassembles it; no firmware is redistributed here, and
anyone with a remote can dump their own.

### The instruction

`0x01AF2` reads three bytes into `0x72A..0x72C`, and the interpreter runs them
out of `0x3D7`, `0x3D8`, `0x3D9`:

```
action:  u8 operand_lo   u8 operand_hi   u8 opcode
```

In a tagged list the same three bytes are preceded by the key tag, which is
where the `<tag> <u16 operand> <opcode>` shape already used throughout this file
comes from. It is not a separate encoding; a binding is a key plus an action.

The first split is on the opcode alone:

| opcode | dispatches on | at |
|---|---|---|
| below `0x0F` | - | `0x02346` |
| `0x0F` to `0x1E` | `operand_lo` | `0x02246` |
| `0x1F` to `0x3E` | `operand_hi` | `0x02048` |
| `0x3F` and up | `operand_hi` | `0x01F78` |

For opcodes `0x1F` to `0x3E` the high byte of the operand selects the action and
the low byte is its argument:

| `operand_hi` | what it does | at |
|---|---|---|
| `0xFF` | set variable `0x3D0`, later copied into `0x340` | `0x02054` |
| `0xFE` | **push `operand_lo` onto the key context stack** | `0x02062`, `0x0186A` |
| `0xFD` | **remove `operand_lo` from the key context stack** | `0x02072`, `0x01880` |
| `0xFB` | set variable `0x332` | `0x0208C` |
| `0xE8` | set variable `0x341` | `0x02228` |

One more is handled before the dispatcher gets a look at it. When a program
stream produces opcode exactly `0x1F` with `operand_hi` equal to `0xFC`, the
reader at `0x01BB4` treats `operand_lo` as a **key code to resolve through the
chain** rather than as an action, and jumps into the lookup below.

### The key context stack

RAM `0x0337` holds a depth and `0x0338` upwards holds that many bytes. The
lookup at `0x01B10` walks it from the top down and takes the first match:

| entry | means |
|---|---|
| `0xFC` | search the section 9 list named by variable `0x341` |
| `0xFE` | search the section 9 list named by variable `0x340` |
| `0xFD` | run an event instead of searching |
| anything else | search **section 9 list number N**, N being the byte itself |

Nothing is found once the stack is exhausted. Cleared at `0x0185A`, pushed at
`0x0187C`, removed at `0x018A4`, and those are the only three sites.

### Section 9 is the table of key binding lists

Section 9 is a `u8` count followed by that many `u24` pointers, and the seek at
`0x07A36` reaches element k at `1 + 3k`. In the public 525 sample the count is
8. Each pointer is the head of an ordinary tagged list of bindings.

So a key press resolves against a *stack of binding lists*, and the config
decides what is on that stack. In the 525 sample one program does the whole
setup, five pushes in a row at `0x02E737`:

```
01 fe 1f    push 1        section 9 list 1
fe fe 1f    push 0xFE     variable 0x340
fd fe 1f    push 0xFD     an event
fc fe 1f    push 0xFC     variable 0x341
02 fe 1f    push 2        section 9 list 2
```

pushed bottom to top, so searched in the reverse order, with list 1 as the last
resort. That program is one entry of section 10. There is exactly one push of
list 1 in the file, and exactly one removal of it, `01 fd 21` at `0x0222DD`.

### Why this is the interesting section

This is the mechanism behind every key on the remote, not a detail about menus.
It is also the first thing here that says what a config *decides* rather than
what it stores: the same key press means different things depending on a stack
the config builds and unwinds as you move around. Anything that wants to add a
button has to get onto that stack.

## 4q. What turns a page, and why the fifth device page was never drawn - SOLVED

Section 5o wrote a config with a second page on the `Devices` menu, the write
verified byte for byte, and the page was never shown. This is why.

### The firmware reads the page array exactly as this file describes it

- section 6, `u24` count then element k at `3 + 3k`, seeked at `0x05BBE`, gives
  the mode entry pointer;
- page k of that mode is read at mode entry `+ 6 + 3k`, at `0x05BF2`, with the
  page number held in `0x3DD:0x3DE`;
- the page count is the `u16` at mode entry `+ 4`, read by the routine at
  `0x05C22`.

None of that looks at what kind of mode it is. A second page is drawable.

### The page count is used in only two places

`0x05C88` turns a page: it increments or decrements `0x3DD:0x3DE`, compares
against the count, and wraps. And the screen-program value provider at `0x01C1A`
hands the count out as value 2, which is what a program prints as `1 OF n`. A
mode whose program never asks for value 2 shows no page footer, whatever its
page count says.

### Turning a page is an action, and it has to be bound to a key

There is no hardwired page key. The dispatcher at `0x022CC` turns a page when it
sees an action with an opcode in `0x0F` to `0x1E` and `operand_lo` in the range
`0xA0` to `0xAF`; low nibble zero goes back, anything else goes forward.

**In the whole public 525 sample that action exists exactly twice:**

```
0x021BAB   tag 0x96   operand 0xFFA1   opcode 0x0F    forward
0x021BB3   tag 0x97   operand 0xFFA0   opcode 0x0F    back
```

Both are inside section 9 **list 1**, the seven-entry list running `0x021B9A` to
`0x021BB7`. Tags `0x96` and `0x97` are the two page keys. Nowhere else in the
file can a page be turned.

That list is the bottom of the stack, so it only answers a key that nothing
above it claimed. The modes that do page leave `0x96` and `0x97` out of their
own binding lists entirely; several single-page menus (42, 88, 103, 109) bind
them to opcode `0x00`, which is how a key is switched off.

### What this means for a fifth device

`Devices` is mode 45: a one-entry physical list, tag `0xAF`, opcode `0x72`,
operand `0x050D`, and one page whose binding list at `0x031CE6` holds four
entries with opcode `0x7F` and consecutive operands `0x01D5` to `0x01D8`, tagged
with the four soft keys `0x9E`, `0x9F`, `0xA7`, `0xA6`. That binding list is the
device list; four keys, four devices.

So a fifth device on arch 9 is not "add a page". At minimum the page keys have
to be reachable while that mode is up, the second page needs its own binding
list carrying the fifth device, and the menu screen program has to ask for value
2 if the footer is wanted. What is *not* established is which of those the
compiler actually does, because no five-device arch 9 config exists in this
repository to look at. That is what
[discussion #33](https://github.com/trelowney/harmony-decompiler/discussions/33)
asks for, and it remains the cheap way to settle this.

**Do not write a guess at it to a remote.** The last one was internally perfect
and wrong, which is the whole point of 5o.

### One thing found on the way

The firmware looks for a config in two places. `0x0668A` builds the base from
bit 4 of `0x109`: set means `0x020000`, clear means `0x018000`. `0x04DF0`
validates `AHCM` and `CMAH` at whichever is selected and sets bit 1 or bit 2 for
the two of them; `0x04C68` falls back to the first if the second does not
validate. The config a remote runs is the one at `0x020000`. What is meant to
live in the 32 KiB at `0x018000` is not known here.

## 4r. The 180 bytes no reader claimed, named - MEASURED

Rooted coverage of the public 525 leaves **180 bytes in seven runs** that no
reader claimed. They are not one problem. Three of the seven were already
described in this document and simply had no reader written, which is the same
shape of gap section 8 turned out to be; the other four had no description at
all.

Calling them unreached would be the same error again. Five of the seven runs are
named directly by the section table, and the sixth is pointed at by section 15.
Nothing was hiding: the bytes were addressed all along and no code read them,
which is a different fault from a structure being unknown and needs saying
differently.

```
sec  offset            bytes  what it is
 1   0x00F48E              7  protocol and skin
 2   0x00F495              8  the remote's own writeable flash
 3   0x00F49D             14  the build time
 4   0x00F4AB            125  section 4's own numbered table
14   0x012654             21  five value lists, section 15 points at them
16   0x012679              1  an empty pointer table
17   0x01267A + 0x013290   4  two leading zeros, and the trailer checksum
```

`tools/verify_small_sections.py` checks all of it against every public sample,
and `--negative` breaks each field in turn and demands the check notice. All 15
containers pass; all 14 mutations are caught. The two 890 containers are
skipped by name, because their base is not a whole number of pages and their
sections are not laid out this way (5j).

### Section 1 states the protocol and the skin

```
<u8 protocol> <u8 protocol> <u8 skin> <u8 0x0D> <u24 0>
```

The protocol byte is stored twice. The oracle is outside the blob: the `.EZHex`
XML states `PROTOCOL` and `SKIN` in text, and section 1 has to agree with both.
It does on **15 of 15** containers across three protocols - `08 08 0f` against
PROTOCOL 8 / SKIN 15, `09 09 16` against 9 / 22, `0e 0e 48` against 14 / 72.
`0x0D` and the three zero bytes are the same in every sample and are not
explained.

### Section 2 states the remote's own writeable flash

```
<u16 record count> <u24 first address> <u24 past-the-end address>
```

[@glenharris](https://github.com/glenharris) read this one on the 525 in PR #30:
8,192 records at `0x070000` to `0x080000`. It generalises, and it carries its
own check - **the span the two addresses bound is the record count times eight,
on 15 of 15 containers**, with no stored record size anywhere for that to be
read back from:

| protocol | records | flash | bytes each |
|---|---:|---|---:|
| 8 | 15,360 | `0x1E0000..0x1FE000` | 8 |
| 9 | 8,192 | `0x070000..0x080000` | 8 |
| 14 | 16,384 | `0x1E0000..0x200000` | 8 |

#### What the 525's firmware does with it, and what it does not

Section 2's reader is `0x01010`, seeking at `0x01024`. Counted over
`0x01010..0x0108A`: **one `u16` read at `0x01032`, one pointer follow at
`0x01036`, no copy-without-following at all.**

Three things the firmware settles that the config side could only assume:

* **The count is a loop bound.** It lands in `0x2C8:0x2C9`, `0x0103A` tests it
  against zero and exits, and `0x01072` decrements the same pair after each item.
* **The stride really is eight.** `0x0104A` is a literal `MOVLW 0x08` for the
  inner byte counter. Until now the 8 came from Glen's reading and the arithmetic
  agreeing; now an instruction says it.
* **The writeable flash is reached the same way the config is.** `0x01036` puts
  the first address straight into `TBLPTR` through `0x06560`, and the items are
  then read with the ordinary advancing byte reader `0x06576`. So `0x070000` is
  not a separate device or a separate route; it is the same SPI cursor.

It also scans for erased space: each item is tested byte by byte against `0xFF`
at `0x0105C`, and an all-`FF` item ends the scan after `0x0106C` restores that
item's address. So the count bounds the search and an erased record stops it.

**And one thing it does not do: it never reads the third field.** There is no
second follow and no copy, so bytes 5 to 7 of the section are untouched by this
image. Their value is what a past-the-end address would be, but it is also
exactly `first + count * 8`, so the agreement `verify_small_sections.py` checks
is a relation among three stored numbers rather than confirmation that anything
consumes the third. Nothing here contradicts Glen; the firmware simply does not
corroborate that field, and it would be honest for a future writer to know that
it can only get that field wrong silently.

Section 2 also confirms the general result above specifically: its count is
compared with zero and never with a literal.

### Section 3 is the build time, and the weekday is what makes it checkable

Section 3 holds the eleven-byte framed record
[@dannybloe](https://github.com/dannybloe) published, and 5l already read it -
under the name **base slot 3**, without noticing that the slot is a section and
that its span is the record plus three zero bytes.

```
<u16 0xADDF> <sec> <min> <hour> <day> <weekday> <month from 0> <year from 2000>
<u16 0xEFBF> <u24 0>
```

The field order matters and is easy to get wrong, because a wrong one still
decodes to a plausible date. Reading byte 6 as the month and byte 7 as an
unknown gives valid calendar values on all 15 samples and is still wrong. What
catches it is the weekday: it is a separate field, and it has to be the weekday
the rest of the record falls on. **15 of 15**, from 2018 to 2025.

```
df ad 2c 14 14 15 01 00 18 bf ef    2024-01-21 20:20:44, a Sunday
```

Four of @kkong42's arch 8 configs decode to 2025-05-14 between 21:25 and 21:46,
which is what a set of spares saved in one sitting looks like, and `Update-1`,
`-2` and `-3` are 17:43, 18:16 and 18:41 on one evening in the order their names
give.

### Section 4's own table carries two numbers in 125 bytes

```
<u8 first> <u16 0> <u16 count>
then count x  <u8 index> <u8 first + index> <u16 0>
```

Thirty records on every container, four bytes each, ending exactly 125 bytes
into the span - the rest of which belongs to section 5's infrared subsystem
(3). The second column is always the first value plus the index, so the table
states nothing the header does not: the whole 125 bytes carry `first` and `30`.
`first` is 4 on protocol 8, 11 on protocol 9 and 14 on protocol 14.

The obvious reading - that these are the config's live state variable addresses
(5c) - is measurable and false: the name table uses indices outside
`first..first+29` on 13 of 15 containers, up to 72 on the 650 against a range
that ends at 43. This check is marked DESCRIBES rather than PASS in the tool,
because it reads back its own declared count and so cannot fail the way the
others can.

**What the numbering counts is settled, and the answer is @dannybloe's.** His
`docs/config-format.md` calls this the **firmware event map** and reads the same
bytes under different names: our `<u8 first> <u16 0>` is his `u24 fallback`, and
our `<u8 index> <u8 first + index> <u16 0>` is his `{u8 key; u24 value}`. Field
for field the same structure, confirmed by him on 15 configs across four
architectures. The firmware raises an event by loading a literal key and looking
it up here. **5x names all thirty and finds five of the loads**: the values are
modes 11 to 40 on the 525, they render as the remote's error and status screens,
and four of the thirty events have a raise site in this image.

**The values name records in section 6**, the same numbering space opcode `0x7E`
indexes. Half of that was already here and measured independently: 4's "opcode
`0x7E` selects a record". Joining the halves gives a check with two numbers that
can disagree, over every config the decompiler reads:

| config | records in section 6 | largest `0x7E` operand | event map range |
|---|---:|---:|---|
| 525 | 114 | 113 | 11..40 |
| 880 Bedroom, 885 Bedroom, 880 Spare-1 | 130 | 129 | 4..33 |
| 885 LivingRoom | 281 | 280 | 4..33 |
| Update | 103 | 102 | 4..33 |
| Update-1 | 125 | 124 | 4..33 |
| Update-2, Update-3 | 154 | 153 | 4..33 |

**The largest operand plus one equals the record count on nine of nine**, and the
thirty values fall inside the array on nine of nine. So each of the thirty
firmware events selects a record the way `0x7E` does, and `first` is the base of
a reserved block of thirty inside that space.

His reading also refuses on arch 10, which is a third confirmation of 5u from a
direction that was not looking for one: section index 4 reads a count of 5,416 on
the 890 and 5,916 on the 895 rather than thirty, because index 4 is not base
slot 4 there.

**What the thirty events are is still not established**, by him or here.

**The 525's firmware reads it exactly this way, and corrects two words of the
above.** The reader at `0x05DA0` takes a `u24` into `0x731`, then a `u16` record
count into `0x734`; each iteration reads a `u8` key into `0x73A` and a `u24`
value into `0x737`, compares the key against the firmware event key at `0x3F1`,
and on a match hands the value to `0x05B6C`, which seeks section 6 and indexes
it. A miss decrements the count and continues. So the structure, the key, the
value and the destination are all confirmed from the instruction side.

Two things in that are corrections rather than confirmation:

- **the firmware holds no constant thirty.** It loops on the `u16` count read out
  of the file. Thirty is a fact about every container measured, not a number this
  image would enforce, so a config declaring some other count is believed, the
  same as everywhere else on this architecture;
- **only two of the value's three bytes are used.** `0x05E06` and `0x05E0A` pass
  `0x737` and `0x738` into the index operand; `0x739` is not read on this path.
  An event therefore selects a record by a 16-bit index, and the third byte does
  something else or nothing. Every value measured fits in two bytes, so no
  container here can tell those apart.


### Section 15 points into section 14, and its targets tile

Section 15 is a pointer table whose targets are inside **section 14's** span,
which is the plainest case in the file of a span that is not a fence (3). Each
target reads as

```
<u8 count> <u16 value>[count]
```

and the check is that consecutive targets abut with no gap and no overlap and
the last one ends exactly where section 15 itself begins. The lengths come from
the data and the boundaries come from the section table, so the two can
disagree. They do not: **5 lists and 8 values on the 525, 9 lists and 47 values
on the 650**, both tiling to the byte. On arch 8 section 15 is a different
shape and the tool reports N/A rather than forcing it.

The 525's five lists are `[60]`, `[80]`, `[600, 602, 766, 769]`, `[1800]` and
`[0]`.

#### Two of the five are named, from the 525's own firmware - MEASURED

@dannybloe calls this slot the **parameter block** and read it from the Harmony
One and Harmony 600/700 images, twelve containers. His stated limit is that no
arch 8 or arch 9 firmware exists on his bench, so those are outside his claim.
This is that gap, filled from the 525 image, and it is the one contribution to
his account of this format available here that is not a re-measurement of his.

**One call site.** `0x053E0` loads the literal `0x0F` and `0x053E4` calls the
seek routine `0x066A8`, and it is the only one of the image's sixteen literal
seek calls that passes 15. The loader is `0x053DE`.

**Two of the five groups are reached**, by offsets 6 and 9 into the pointer
array, which at three bytes an entry are groups 2 and 3. The loader `0x053DE` has
exactly two callers in the whole image, `0x0560E` with offset 6 and `0x05532`
with offset 9, so **groups 0, 1 and 4 are never asked for**.

> That is a different shape from the configuration-bit routine of 5r, and the
> two were being described with the same words. `0x00172` has **no reference
> anywhere in the image**: the code exists and nothing can reach it. `0x053DE`
> runs perfectly well; there is simply no third caller. So the compiler emits
> `[60]`, `[80]` and `[0]` into a config whose firmware has no instruction that
> reads them. On one arch 9 sample, which is all there is.

| group | values | what the firmware does with them |
|---:|---|---|
| 0 | `[60]` | not reached from section 15 |
| 1 | `[80]` | not reached from section 15 |
| 2 | `[600, 602, 766, 769]` | two hysteresis pairs turning an ADC reading into a three-band value |
| 3 | `[1800]` | reload for the countdown that gates the next ADC scan |
| 4 | `[0]` | not reached from section 15 |

Group 2's chain: `0x05406` clears `ADCON0` and sets `ADON`, which selects
channel 0; `0x05418` sets `GO/DONE` and `0x0541A` waits on it; `0x05420` and
`0x05424` take `ADRESL` and `ADRESH`. Eight samples are accumulated from
`0x055C2` and divided by eight by three right-rotates from `0x055E2`. The
comparisons at `0x0578E` and `0x05794` test that mean against each value in
turn, and `0x0561E` consumes them in pairs, consulting the previous band inside
the narrow interval between each pair. That is hysteresis: `600/602` and
`766/769` give bands 0, 1 and 2.

**What the voltage on AN0 actually is, is not established, so no battery, light
or cradle label is claimed here.** Nor is 1800 turned into thirty of anything;
the countdown's quantum was not read. **5y narrows it without naming it**: AN0 is
the only analog channel the image ever selects and RA0 is the only input on its
port, so whatever the three bands mean, there is no second reading to be confused
with this one.

#### Arch 9 does not enforce the length rule, and that is a difference

His load-bearing rule elsewhere is that a group is read only when its declared
length matches what the build expects, and otherwise the subsystem falls back to
constants compiled into the firmware. **On arch 9 the loader does not check.**

The callers do compute a demand: group 3's caller writes `1` to `0x1EF` at
`0x05528` before writing the offset `9` to `0x1EE` and calling. The loader reads
the group's `u8` entry count at `0x053FC` and the very next instruction,
`0x05400`, is `RETLW 0x01`. There is no comparison between them, and `0x053DE`
never reads `0x1EF` at all. The outer count is read at `0x053E8` and stored
without a bounds test before indexing either.

So the demands exist, and on this config they agree, `4` for group 2 and `1` for
group 3 against lengths of 4 and 1. Nothing enforces them. An editor that
changed a group's declared length on arch 9 would be believed.

Verified here by disassembling `0x053DE` to `0x05404`, `0x05406` to `0x05428`
and `0x05528` to `0x0554E` independently of the report that produced the
reading. The control is that the same procedure reproduces the recorded
`0x80` to section 13 path through `0x04B20`, and it does.

### Section 16 is a pointer table that arch 9 and arch 14 leave empty

`<u8 count> <u24 address>[count]`, filling the span exactly. Arch 8 holds nine
addresses in 28 bytes; the 525 and the 650 hold a single `00`. A one-byte
section is an empty table, not a stray byte.

### Section 17's two leading zeros, and the two at the end

The last two bytes of section 17's span are the checksum the remote itself
checks (4m), which is understood and recomputed on compile but still emitted
raw. The two at the start of the span, before the first bitmap, are `00 00` on
15 of 15 containers. They read as an empty count under either width, but no
sample has a non-zero one, so neither the width nor the meaning is established.
This is marked DESCRIBES.

**The leading two are named, and the answer is @dannybloe's.** His
`docs/config-format.md` calls this slot the **touch screen hit map**, read from
the Harmony One's own firmware and confirmed on both One configs. It is a page
array, each page an area array, each area twelve bytes of rectangle plus the key
code a hit reports. **The Harmony One is the only remote in either corpus with a
touch panel**, so on every other architecture the slot is empty, and empty here
is two zero bytes rather than one because the pointer lands two bytes in front of
the picture bank that follows, which is the same bias the bank walk starts from.

That answers both halves of the question above: the width is a `u8` page count,
the second byte is the bias, and no sample here has a non-zero one because no
remote here has a touch panel. He confirmed it on thirteen containers; it holds
on nine of nine here.

**One thing to add, because it holds where his slot numbering does not.** He
speaks of base slot 17, which needs a mapping. Measured against the raw table,
the hit map is simply **the last present entry**, whatever its index:

| arch | entries | last index | bytes there |
|---:|---:|---:|---|
| 9, the 525 | 18 | 17 | `00 00` |
| 8, four configs | 19 | 18 | `00 00` |
| 14, three configs | 18 | 17 | `00 00` |
| **10, the 890 and the 895** | 21 | **20** | `00 00` |

So this holds on arch 10 as well, where 5u shows the slots are shifted and his
own mapping is deliberately refused. It is one anchor at the far end of that
table that comes out of the file rather than out of a guess, and it costs
nothing: the claim is only that the last entry is empty.

## 4s. Relocation, and the guard a census cannot be - SOLVED, with a stated reach

A config can change length now, in this repository, on arch 9. The design is
[@dannybloe](https://github.com/dannybloe)'s `relocate.ts` from
`harmony-explorations` at `edb1349e`: shift the bytes, rewrite every stated
address off a pointer census, restamp `end_addr` first and the trailer checksum
last. `tools/pointer_census.py`, `tools/relocate_arch9.py` and
`tools/verify_arch9_relocation.py`.

### The floor is derived here, not inherited

Danny's Harmony One floor sits past a key table, because that firmware reads the
table at a fixed distance after the marker. **Arch 9 does not work that way.**
The 525 loads section 6, indexes `operand * 3 + 3` and follows the u24 it finds,
so a record's address is stated by the file (4b, 4d). What forces the arch-9
floor is only the fixed container head: the magic, `end_addr`, the 18 stated
section addresses and the `CMAH` marker. The floor is the first byte after that
marker, `0x5F` in the public 525, and `0x5E` is refused.

### The census check, and where it stops

The census is a closed inventory of the reader-backed address fields - 12 holder
classes on the 525 - and the verifier omits each class in turn and requires the
check to fail. All 12 are caught. It also prints, per class, how many of the
tested insertion offsets actually have pointers of that class above them, so a
class that is silently no longer exercised shows up as a number rather than as
a pass.

**But a census cannot say where the gap went.** A gap in the wrong place leaves
every stated address correct, so every part of that check still passes:

```
insert 32 bytes at the start of section 9
  census: every address rewritten, trailer recomputed, end_addr bumped
  reality: section 8's last list ends exactly on that boundary (4k), so the
           32 zero bytes land inside section 8's span and its walker stops on
           them - 135 tagged lists gone, nothing to point at them wrong
```

This is the fault this document has recorded four times under different names,
arriving a fifth: **an oracle that reads the same field as the thing it checks
is not an oracle.** The census is what relocation rewrites.

### The guard is the decompiler, because it is a second reader

Relocation inserts zero bytes. It adds no structure. So nothing the decompiler
recognised before a relocation may stop being recognised after it, and that
holds no matter which grammar recognised it. Growth is expected and allowed - a
gap is more opaque bytes, and one more section-level chunk when it lands on a
boundary. Loss is refused, and named:

```
insertion at 69632 costs the decompiler structure it read before:
font_glyph 160 -> 0; 69632 is inside the font_glyph at 69626
```

All cases the verifier checks are found in the file rather than written down as
numbers: one byte into the first glyph above the floor; the start of the section
following the one that holds the tagged lists; and insertions inside a rooted
mode binding, a packed page-list copy and a rooted screen instruction.

### What the guard cannot see

Its reach is exactly the decompiler's coverage, and no further. Rooted record
lists and complete pointer-free screen instructions move **8,001 bytes** out of
opaque. **512 bytes of the public 525 remain opaque, 0.65% of the file**, and a
gap opened inside any of them can still cost no recognised structure. They are:

- 130 bytes at `0x001EB9`, between an action list and a class-5 body;
- 286 bytes at `0x00EFDA`, mixed action-like data before the raw-pointer run;
- 73 bytes in 22 one-, two- and five-byte scalar runs between raw pointers at
  `0x00F0FB-0x00F183`; and
- 23 bytes at `0x00F33A`, between a terminal screen instruction and an action
  list.

None has both an exact reader-supplied root and a self-closing grammar, so naming
them would lower the evidence standard. The guard remains a floor on
correctness that rises as coverage rises, not proof that an allowed insertion
point is safe.

The census check carries its own blind spot, already stated by the verifier:
changing a key-table `u16` to another valid action-list index passes every part
of this and makes a button do the wrong thing.

### A second blind spot, looked for on purpose and measured

@dannybloe hit a failure of this shape in his own code on 25 August: a table
dropped out of his relocation census while his reader kept reading it and every
test passed. His census is a separate recogniser, so it can drift from the
reader. Ours is derived from the reader, so his exact failure cannot happen here.
The version that can is that `survives_relocation` counts **regions by kind** and
not the address fields inside them, so a region could survive and lose a field.

**It is demonstrable and no insertion offset triggers it.** Nulling one
`ir_record_header` address without removing its containing region leaves
`_kind_counts` unchanged and `survives_relocation` empty, while the census sees
614 fields become 613. So the hole is real. Sweeping 307 offsets at stride 256
with a three-byte insertion, 147 are refused by the guard, 32 by the field
rewriter and 128 accepted; of the 128, **none of the 60 whose census could be
counted lost a field**.

### The census cannot be used as a second guard, and here is why

The other 68 accepted offsets could not be counted because the census refuses
their output outright, always with `find_references found an unrepresented field`
at one address. That looks like the guard passing something broken. It is not.

`find_references` scans for a byte equal to `REF_OPCODE`, `0x16`, followed by a
`u24` that lands inside the file. On the untouched 525, the address at `0x601B`
is `0x026013`, so its low byte is `0x13` and the scanner walks past. Relocation
correctly rewrites it to `0x026016`, and **the low byte becomes `0x16`, which is
the opcode the scanner is looking for**. It then reads the next three bytes, the
top two of that same address plus the first byte of the next field, gets
`0x020260`, finds that in range, and reports a field one byte into a real one.
`0x601B` and `0x601F` are both in the census; their straddle is not, and the
coverage assertion has no tolerance for it.

So this is a false alarm generated by relocation doing its job. **The
recommendation that would have followed from the other reading - make
`survives_relocation` require a successful census of its output - is wrong**, and
would refuse 68 of 128 sound insertion points. If a post-relocation census is
ever wanted, `find_references` has to skip bytes that lie inside an address field
it already knows about, rather than the guard being tightened.

`codex-work/tooling/sweep_relocation_guard.py` and
`measure_census_refusal_after_relocation.py`, neither in this repository because
both import `pointer_census` internals rather than a public interface.

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
`0xFEED ... 0xBEEF` name table, key tables, and the record array with its
headers.

> This paragraph used to end "and trailers, and the `16 <u24>` references", and
> to note that arch 8 has no block headers and therefore no bitmaps reachable
> through them. All three of those structures have since been withdrawn on arch 9
> as well; see 4d, 4f and 4g. Nothing that was shared has stopped being shared,
> but two of the things listed as shared were never structures on either side.

About 4% of an arch 8 config decodes, against more than 99% emitted non-opaque
for the 525. Part of that is that the files are six times larger, and part of it
is that most detailed work has used one arch 9 config. The gap is a statement
about where the effort went, not about the architectures.

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

### The word reply says `C2` when it means `C3`, and the firmware says why

Worth writing down, because a fresh implementation gets this wrong and the
symptom is a value of zero rather than an error. The reply to `B3` is
`C2 <kind> <hi> <lo>`: four bytes under a header whose low nibble claims two.
libconcord has carried the warning since 2007 - *"the 880 responds with C2
rather than C3"* - and reads `(rsp[2] << 8) | rsp[3]` anyway. `tools/hid_query.py`
inherited that and has been correct since its first commit.

The 525's own firmware closes it. The `0xB0` handler sets state 10 at `0x03090`
and takes the selector into `0x27E`; state 10 runs the executor at `0x03412`.
That executor emits `0xC2`, echoes
the selector, clears its two result bytes, and **only `kind=01` enters a body**:

```
03414:  MOVLW 0xC2               the header, hardcoded
03426:  MOVF 0x7E, W             the selector
03428:  XORLW 0x01
0342A:  BNZ 0x03458              anything but STATE skips the body
0344C:  CALL 0x04B72             the accessor
03450:  MOVFF PRODL, 0x710
03454:  MOVFF PRODH, 0x711
03458:  MOVFF 0x711, ...         high byte out first
0345E:  MOVFF 0x710, ...         then the low byte
```

So a decoder that trusts the length nibble reads the high byte as the value and
throws the low byte away. On this remote the high byte is always zero and every
value observed lives in the low one, which is exactly how a working reader and
a broken one produce the same shape of output. The byte-return path clears
`PRODH` deliberately, so the zero is not padding either.

That also settles the negative side: `kind=07` never reaches the body, which is
why arbitrary data RAM cannot be read on this architecture no matter what
address is asked for. Confirmed on hardware at `0x02DE`, the keypad scan-code
candidate, which answers `C2 07 00 00`.

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

**Why, and it is worth knowing.** The three results above are black box. Danny
Bloemendaal reached the same negative on a Harmony 600 and then read the reason
out of the firmware: in USB mode the keypad handler never runs, so no scan code
is ever computed. What the part does instead is park every row line low at once
and enable interrupt on change on the column port, so a key wakes it without a
scan. His section 48. Credit is his; it is quoted here because it explains our
result rather than repeating it.

On arch 14 that parked state leaks a quarter of the answer, because a press
still pulls its own column down and the column is readable: `(code - 1) mod 4`.
That does not carry to the 525, whose scanner has a single sense line on `PORTB`
bit 7 rather than a column port, so there is nothing equivalent to observe. See
5h.

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
[§4i](#4i-section-10-indexes-action-lists---solved), which is where the evidence
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

## 5f. Key codes are shared across architectures, buttons are not

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

-> **What is shared is the ordering and the event namespace, not the buttons.**
The table order is canonical, Logitech's standard key ordering, and 41 codes
appear in both architectures as an ordered subsequence.

**This section used to end with the opposite conclusion.** It said the code to
physical key assignment is shared across models, and that obtaining the mapping
for one model in this family would carry most codes over to the 525. Both
sentences were wrong, and section 5n is the measurement that refutes them: the
880/885 mapping now exists, and it does not transfer. The 525 has printed digits
3, 5, 6 and 9, but its scan set contains none of 58, 61, 59 and 62, which are
what those buttons are on an 880. Equal numeric codes are electrical coordinates
local to one board, and the three architectures do not even share a matrix
shape. See 5g and 5n.

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
unwired.

**This is the 525 only. Do not carry it to another architecture.** An earlier
revision of this section read arch 8 as the same 8-wide grid with column 8
filled in, and offered that as the reason codes agree across models. Arch 8 is
not 8 wide. Its scanner is 4 inputs by 16 lines and its scan code is
`(line - 1) * 4 + input`, measured on the board and read out of the firmware in
5n. Arch 14 is 4 by 14 on the same scheme. Three architectures, three matrix
shapes, and the codes agree because the numbering and the ordering are shared,
not because the boards are.

This section used to say "column 0-7, column 0 unwired". Same bits, and the
config alone cannot choose between the two readings: no 525 code has
`code & 7 == 0`, and running both conventions over the arch 8 samples produces
equally ragged grids, so that data does not discriminate either. The firmware
settles it for the 525 - the scan returns a column number that is a bit index
**plus one**. See 5h.

That reading is for the 525 alone. This paragraph used to carry it on to arch 8
through 5f's claim that the encoding is shared, and that inference is void:
arch 8 is settled by its own firmware, differently, in 5n.

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
hypothesis: for both opcodes the high byte is only ever 0, 1, 2 or 3, and the
state-variable names in this config mention three devices (`TV_Panasonic`,
`Amplifier_Genius`, `XBOX_360`). The two opcodes then differ in their low byte
exactly as the
reading predicts: `0x7D` spreads evenly, the way a command index would, while
`0x7C` is `1` in 87% of cases, the way a flag would. That asymmetry is the real
evidence; the high-byte range on its own would be much weaker.

In this sample, key table 3 drives device 1 and tables 2 and 4 drive device 2,
so tables are per activity and an activity is bound to a device. Tables 0 and 1
are fallbacks: 47 of the 51 entries in table 0 run the same action list.

> **Three names, four devices.** The high byte runs 0 to 3 and there are three
> names, and this document once treated that as three devices plus a spare. It is
> four devices. The fourth is `X96 Box` and it has no name in the state-variable
> tree at all, because a device the user built by learning codes onto a database
> profile gets its name written only into the pictures of the menus that show it.
> Section 4l renders those menus and reads it off the screen. The four groups are
> Amplifier Genius (mode 73, 8 records), TV Panasonic (78, 67), XBOX 360 (113, 61)
> and X96 Box (111, 64), and `0x7D` reaches every command index in every group as
> a closed range.
>
> This is the general shape of the mistake worth remembering: a count that matches
> is not a count that closes. Three names and a high byte of 0..3 agreed with each
> other and were both consistent with the wrong answer.

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

## 5j. `config_base` is not always `0x20000` - two more protocols

Samples uploaded to this repository's issues on 3 and 10 August 2026 by
[@psolyca](https://github.com/psolyca) and
[@kkong42](https://github.com/kkong42) carry two protocols nothing here had
seen. Both were rejected outright by the decompiler, and in both cases for the
same shallow reason.

| protocol | remote | magic | end marker | `config_base` |
|---:|---|---|---|---|
| 8 | 720, 785, 88x | `TPTP` | `DKDK` | `0x20000` |
| 9 | 525 | `AHCM` | `MCHA` | `0x20000` |
| 10 | 890 | `TPTP` | `DKDK` | **`0x30000`** |
| 14 | 650 | `GSPM` | `PTYY` | **`0x30000`** |

The base is stated nowhere in the file. What gives it away is that the `u32` at
offset 4 is the address of the end marker: subtract where the marker actually
sits and the base falls out. On protocol 10 and 14 the answer is `0x30000`, and
with that one change both files stop looking exotic. The 890 in particular has
arch 8's magic *and* arch 8's end marker, and was being rejected only because
the marker is not at the end of the file: 702 bytes of zero padding follow it.

The trailer checksum from 4m then verifies on both, with the same seed and the
same algorithm found in 525 firmware:

| file | stored | recomputed |
|---|---|---|
| 650, protocol 14 | `0x4045` | `0x4045` |
| 890, protocol 10 | `0x5AC7` | `0x5AC7` |

Which is the strongest evidence so far that 4m is a property of the format and
not of one remote. It has now been checked on protocols 8, 9, 10 and 14, and it
holds on **sixteen of the seventeen configs in this repository**, from four
different owners, having been read out of exactly one firmware image. The
seventeenth is the suspect 890 dump described below.

**None of this is support for those protocols.** The decompiler still refuses
them, and the interesting question of what is inside is untouched. It is only
the envelope. But it moves both from "unknown architecture" to "known container,
different base", which is a much shorter distance than it looked.

> One of the two 890 dumps does not verify: its header says the config ends at
> `0x90BBD`, the marker is at `0x90F1D`, and the checksum does not reproduce. Its
> sibling is exactly self-consistent under the same reading. Either this reading
> is incomplete in a way that spares one file, or that dump is damaged. A second
> read of the same remote would say which, and has been asked for.
>
> **It was damaged, and the damage has a shape.** See 5k.

## 5k. The 890 read path duplicates 54-byte blocks - SOLVED

[@kkong42](https://github.com/kkong42) re-read both of his Harmony 890s and
uploaded the results to issue #28. That second read answers the question above
and then some.

The healthy unit reads the same twice: the payload is byte-for-byte what it was,
and the only difference is that the tail after `DKDK` came back as 108 zero bytes
instead of 702. **Padding after the end marker is not a stable file length**, so
two dumps of one config can differ in size and both be right.

The other unit failed both times, and comparing the two failures is what gives
it away. Align the two dumps against each other and they agree everywhere except
at twelve points, and at every one of them they resynchronise after an exact
multiple of 54 bytes. Nothing in between is corrupted; whole blocks are in the
wrong place.

They are duplicates. **At each divergence the 54 bytes are an exact copy of the
54 bytes in front of them**, so the reader has occasionally delivered the same
chunk twice. Remove them and both dumps repair:

| dump | bytes too many | duplicate blocks | after removal |
|---|---:|---:|---|
| first read | 864 | 16 | 396,225 bytes |
| second read | 108 | 2 | 396,225 bytes |

Both repairs land on **the same SHA-256**, `0aacc332796db449...`, with `DKDK` at
`0x60BBD`, exactly where that file's own header said it would be, and a trailer
checksum of `0x5DE1`, exactly the value stored in the file. Two independent bad
reads converging on one blob that satisfies two constraints it was not fitted to
is not a coincidence: that is the remote's real config, and it was never
damaged. The reader is.

`tools/repair_890_dump.py` does this. It refuses unless the result reproduces
both the stated end address and the stored checksum, so it cannot quietly hand
back a plausible-looking blob.

Where 54 comes from is not known. It is not one of libconcord's response
payload sizes (1, 2, 3, 4, 5, 6, 14, 30, 62), so it is not one dropped USB
report. The 890 is also the model Concordance reads twice, 1,665 KiB then
1,664 KiB, before writing about 390 KiB, and it is the model whose firmware read
libconcord does not implement at all. Something about that remote's read path is
different, and this is a measurement of it rather than an explanation.

## 5l. Arch 8 stores the build time twice, and that is the whole diff

Two of @kkong42's H880 configs share a trailer checksum and are not the same
file (4m). They differ in exactly four bytes:

```
0x0011A1  22 -> 30      0x01E237  22 -> 30
0x0011A8  19 -> 1F      0x01E238  19 -> 1F
```

The second pair is inside base slot 3, whose eleven-byte framed record
[@dannybloe](https://github.com/dannybloe) placed and published: cookie `0xADDF`,
then second, minute, hour, day, weekday, month, year offset, then `0xEFBF`.
Reading it gives the answer:

```
DF AD 22 19 15 0E 04 04 19 BF EF    2025-05-14 21:25:34, Wednesday
DF AD 30 1F 15 0E 04 04 19 BF EF    2025-05-14 21:31:48, Wednesday
```

Six minutes apart. **Nothing was changed between them**: no device, no activity,
no delay, no ordering. They are the same configuration compiled twice, and the
XOR checksum cancels because one logical edit lands twice at the same word
parity.

The first pair, 118 kB earlier, is the same two values again - `22 00` and
`19 00` as little-endian words, each followed by `3B 00`, which is 59 - and it
sits in a region this decompiler still passes through as opaque. So the seconds
and minutes are stored twice, in two different shapes, and only one of the two
is understood.

## 5m. The 525 can learn infrared, and it has now done it - CONFIRMED

This one starts with a correction of the obvious kind: **the protocol is not new
and it is not ours.** libconcord has had it since 2007.

```c
#define COMMAND_START_IRCAP  0x70
#define COMMAND_STOP_IRCAP   0x80
#define RESPONSE_IRCAP_DATA  0x90
```

`CRemote::LearnIR` writes `0x70`, reads `0x90` reports until the signal has been
quiet long enough, writes `0x80`, and drains to `RESPONSE_DONE`. Its inner loop
already recovers the carrier frequency and the mark/space list, and
`concordance --learn-ir` exposes the whole thing, gated behind a Logitech job
file listing the key names to learn. All of that is © Phil Dibowitz and Kevin
Timmerman.

What nobody knew is whether the **525** implements any of it. It does, and the
path is all in this repository's firmware image:

| address | what it does |
|---|---|
| `0x02F56` | command `0x70` sets state 5, clears the buffers at `0x0500` and `0x0542`, sets the toggle at `0x0584` |
| `0x060BE` | the producer. Gates on state 5, takes the buffer the toggle is not on, and writes status, length, `0x90`, a sequence that steps by `0x10`, then big-endian `u16` samples |
| `0x015A2` | the transport. Zero-fills a 64-byte report, repeats the real length in byte 63, points EP1 IN at buffer + 2 and arms it |
| `0x030F8` | command `0x80` moves to state 6; states 6 and 7 share the `F0 70` acknowledgement |

Two things follow that matter more than the addresses.

**The reports are pushed, not answered.** Nothing polls. The producer fills a
buffer whenever the capture hardware has samples, and the transport arms an
interrupt-in report. A host that writes `0x70` and then waits for one reply per
request reads one report and concludes the remote is broken. Danny Bloemendaal
([@dannybloe](https://github.com/dannybloe)) described exactly this asynchronous
push mechanism on architectures 12 and 14 in his section 98. This is the same
mechanism on architecture 9 with different buffer addresses, and his description
is what made it recognisable here.

**The samples are timings, and this repository already knows what to do with
them.** 4n's duration words are microseconds with bit 15 as the mark flag, and
the carrier is stored per infrared group. A learned signal and a stored one are
the same kind of object, which makes this the missing first link in the button
map: press a key on the original remote, capture it, match it against the 200
expanded class-5 records, and the command index names the key.

> **Nothing here has been sent to a remote.** `0x70` and `0x80` do not touch
> flash - they are runtime state - but they are still writes, and this project's
> tools refuse writes on purpose. `tools/hid_query.py`'s allowlist does not
> contain them, and this section is not a reason to add them. It is a map of
> what is possible, not an instruction.

### It has since been run, and the loop closes

`0x70` was sent to a 525 on 2026-08-21, outside this repository's tools and
their allowlist, which is unchanged. A Panasonic remote was pointed at the 525
and its `1` key pressed. The remote returned **600 durations at 36,802 Hz,
decoding to six identical 48-bit Kaseikyo frames**, wire bytes

```text
40 04 01 00 08 09
```

The half of that this repository can check without any hardware is the other
end, and it matches. Expanding the owner's own config:

- group 1 commands **61 and 64** both carry that signal, stored at 36,401 Hz;
- mode 78, the TV Panasonic device, binds event `0xAB`, scan 43, to a `0x7D`
  send of group 1 command 61;
- scan 43 is the printed `1`.

So the remote heard a signal, and the signal it heard is the one its own config
already stores for the key that sends it, reached through a binding nobody
consulted while capturing. Capture, decode, and the stored map close on each
other.

Two details are worth keeping rather than smoothing over. The captured carrier
is 36,802 Hz and the stored one is 36,401 Hz, about 1.1% apart, so a matcher has
to treat carrier as approximate. And the six frames arrived as complete repeats,
not as a short repeat burst, which is the same thing the Samsung handset in 5o
turned out to do.

**What this means practically: naming the keys of an unknown remote does not
need a LearnIR, an account, or any write to flash.** It needs two remotes, since
a Harmony cannot hear its own transmission. `tools/ir_keymap_oracle.py` already
plans and matches against the stored records; this is the input side it was
written for, and `tools/harmony-ir-learner/` is the capture side.

### How good a receiver is it, next to a real one

Good enough. The same Samsung key was captured twice, once with a LearnIR V2 and
once through a 525, and both compared against the record a generated config
holds for it:

| | LearnIR V2 | via the 525 | stored |
|---|---:|---:|---:|
| carrier | 38,000 Hz | 38,237 Hz | 38,001 Hz |
| header mark / space | 4474 / 4474 | 4472 / 4478 | 4474 / 4474 |
| frame plus gap | 108,508 us | 108,494 us | 108,504 us |

Both decode to `07 07 02 FD`. Across the frame's 64 bit cells the largest
disagreement between the 525 and the LearnIR is **21 us**, on cells nominally
560 us long, which is under 4%. The stored record and the LearnIR agree exactly.

The one thing the 525 is not good at is carrier. It reads 0.6% high here and
1.1% high on the Panasonic above, both times in the same direction. Match on
timings and treat carrier as a hint.

## 5n. The arch 8 keypad is 4 by 16, and all 55 keys are fixed - MEASURED

The 880 and the 885 do not use the 525's matrix shape. Their keypad is
**4 inputs by 16 lines**, and the scan code is

```
scan = (line - 1) * 4 + input        input 1-4, line 1-16, 0 means no key
```

which gives 63 usable positions and is why the tables hold codes 1 to 63.

**This exists because @kkong42 opened two remotes and buzzed out the board.**
He had an 880 and an 885 that had been robbed for parts over the years, and he
recorded both pads of every key position, first as a 63 by 63 continuity table
and then, when the first one turned out to cover only one of the two pads, as
two nets per key. Discussion 6, comments `17981708` and `17992392`. Nothing
below is derivable from the files alone.

Reproduce with the samples in this repository:

```
python tools/verify_arch8_key_matrix.py
```

### The three layers, and how much each one settles

| layer | what it gives |
|---|---|
| board | 55 populated cells of 4 x 16, 8 unpopulated, `K19` and `K60` on the 885 only |
| configs | 53 codes on the 880 and 55 on the 885, the two extra being exactly 19 and 60 |
| firmware | a `PORTB<4:7>` selector returning 1 to 4, and the combiner above |

The firmware layer is optional and the images are not in this repository. Pass
`--firmware-dir` at your own copies to run it. Both models carry the routine
twice, in the application image and in the safe mode image, and it is the same
code with its two variables at different offsets: `0x890A`/`0x8C26` in both
application images, `0x4A0C`/`0x4D06` and `0x4A1A`/`0x4D14` in the safe mode
ones. Searching for literal bytes finds only the safe mode pair, which is the
copy that does *not* generate the codes a config holds, so the search is written
as a template with the two file numbers as holes.

### What is proved, and what is only argued

Occupancy leaves **11,520** electrical relabellings, because five fully
populated lines can be permuted without changing which cells are occupied. So
most of the table below is not proved.

Four positions are. Of the 24 ways to assign the pad letters to inputs only two
survive both code sets, and both put **C on input 3 and D on input 4**; net 5
can only be line 5 and net 15 only line 15. Every surviving relabelling
therefore agrees on these:

| PCB | scan | button |
|---|---:|---|
| K19 | 19 | Green, 885 only |
| K20 | 20 | screen arrow down, Red on the 885 |
| K59 | 59 | 6 |
| K60 | 60 | Yellow, 885 only |

The two colour keys were **predicted before the board was measured**, on the
grounds that the 885 binds two codes the 880 does not and has two keys the 880
does not. That prediction is now a consequence rather than a coincidence.

`tools/verify_arch8_key_matrix.py` gets no further than that on its own. It
picks one relabelling out of 11,520 because it agrees with Logitech's K
numbering on 49 of 55 positions, which is a design argument and not a traced
wire. Its six disagreements are exactly the non-sequential net 13/14 routing
@kkong42 flagged himself: `K51`-`K54` land on 53-56 and `K55`, `K56` on 51, 52.

### What closes the other 51, and what it leans on

@kkong42 then measured the operational half. He read three devices off two real
remotes and wrote down, per device, which buttons were bound and which were
blank. Issues #18 and #20, and discussion 6. A config binds scan codes to
functions, so a button he names on a remote pins the scan code that sends it,
and a button he reports blank pins one that is not bound. That is a join, not an
inference from numbering, and it eliminates the way occupancy cannot:

```
python tools/verify_arch8_standard_bindings.py --explorations DIR --manual PDF

  11520 occupancy-compatible PCB relabellings -> 4     his three device inventories
  4 -> 2                                               DVL-909 custom position 8, K44
  2 -> 1; all 55/55 physical keys fixed                the manual's global roles
```

**Three qualifications belong with that, not buried under it.** Each constraint
was removed in turn and the check re-run, because a check earns belief by being
able to fail.

- **Two of the five constraints are load bearing and three are redundant.** K44
  from the DVL-909 LCD positions and the HR-S6855 blank pattern each break it.
  Removing Help from the manual's global roles, or one of the three numeric
  keys, or one key from the DVL-909 blank list, does not. Over-constraining a
  search does not make its answer wrong, but the narrative in which Logitech's
  three documented roles eliminate the last candidate is stronger than the code
  supports: Activities and OFF do it alone.
- **One transcription slip would pass silently.** Three of his values were
  altered to a plausible neighbour: reading DVL-909 blank key 20 as 21 fails the
  check, putting LCD position 8 on K43 rather than K44 fails it, and reading
  **DVL-909 bound key 17 as 16 is accepted with nothing said.** The map is not
  defended at that point.
- **The final assertion pins six answers directly**, `{51: 53, 52: 54, 53: 55,
  54: 56, 55: 51, 56: 52}`, which makes that script partly a regression test on
  a result rather than purely a derivation of it. The intermediate counts,
  11,520 to 4 to 2 to 1, are the part that carries weight, because none of them
  names an answer.

All three were told to @kkong42 rather than kept here,
`discussions/6#discussioncomment-18154128`.

### What runs on a bare clone, and what cannot

| script | needs | what it settles |
|---|---|---|
| `verify_arch8_key_matrix.py` | nothing | 11,520 relabellings, the four proved keys, one picked by designator agreement |
| `verify_arch8_human_oracles.py` | a `dannybloe/harmony-explorations` checkout | the screen inventories behind the join |
| `verify_arch8_standard_bindings.py` | that checkout **and** Logitech's H880 user guide PDF | the join that fixes all 55 |

Both refuse by name when a path is missing rather than failing obscurely. The
manual is not redistributed here, for the reason `BUTTON-LAYOUT.md` gives about
the 525 one, so **the check that closes all 55 keys cannot run on a bare clone**
and no merge can change that. It runs for anyone who fetches the manual.

### The same scheme on arch 14, from the other side

Danny Bloemendaal read the arch 14 scanner out of the firmware as **4 by 14**,
`row * 4 + column`, 1 to 56, and separately pressed all 54 buttons of a Harmony
600 while watching it over USB. His sections 13, 48 and 133. His per column
census came out `[14, 14, 13, 13]`; the 885's per input census here is
`[14, 14, 14, 13]`. Two architectures, two people, two methods, one scheme.

The two methods turn out to be complementary rather than redundant, which is
worth stating because it says what to do next. A live remote parks all of its
row lines low and watches the column port for an interrupt, so a press reports
its column and nothing else: `(code - 1) mod 4`, one quarter of the answer, and
no amount of care gets further. Opening the case and buzzing the pads gets the
half that method structurally cannot. @kkong42's first table happened to be the
same quarter Danny can already read; his second one is the part nobody could
have got any other way.

### Do not carry the numbers to another model

Scan codes are electrical coordinates local to one board. The 525 has printed
digits 3, 5, 6 and 9 and does not contain 58, 61, 59 or 62, which is what those
buttons are here. It has Replay, 0, Previous and Enter while several of their
arch 8 values are absent from it entirely. Section 5f used to say the assignment
carries across models and it does not.

### The full table

One relabelling of 11,520, as above. The four rows marked with a dagger are the
proved ones.

| scan | PCB | printed button |
|---:|---|---|
| 1 | K1 | Activities |
| 2 | K2 | Power |
| 3 | K3 | Help |
| 4 | - | not populated |
| 5 | K5 | Custom 1, top left |
| 6 | K6 | Custom 3, second left |
| 7 | K7 | Custom 5, third left |
| 8 | K8 | Custom 7, bottom left |
| 9 | - | not populated |
| 10 | K10 | Device |
| 11 | K11 | Mute |
| 12 | K12 | screen arrow left |
| 13 | K13 | Volume + |
| 14 | K14 | D-pad left |
| 15 | K15 | Volume - |
| 16 | K16 | D-pad up |
| 17 | K17 | Menu |
| 18 | K18 | Exit |
| 19 | K19 &dagger; | Green, 885 only |
| 20 | K20 &dagger; | screen arrow down, Red on the 885 |
| 21 | K21 | Stop |
| 22 | K22 | Record |
| 23 | K23 | Rewind |
| 24 | K24 | Replay |
| 25 | K25 | 1 |
| 26 | K26 | 4 |
| 27 | K27 | 8 |
| 28 | - | not populated |
| 29 | K29 | 7 |
| 30 | - | not populated |
| 31 | K31 | Clear |
| 32 | K32 | 0 |
| 33 | K33 | D-pad OK |
| 34 | K34 | D-pad down |
| 35 | K35 | D-pad right |
| 36 | K36 | Channel - |
| 37 | K37 | Channel + |
| 38 | K38 | Media |
| 39 | - | not populated |
| 40 | K40 | Previous channel |
| 41 | K41 | Glow |
| 42 | - | not populated |
| 43 | K43 | screen arrow right |
| 44 | K44 | Custom 8, bottom right |
| 45 | K45 | Custom 2, top right |
| 46 | K46 | Custom 4, second right |
| 47 | - | not populated |
| 48 | K48 | Custom 6, third right |
| 49 | - | not populated |
| 50 | K50 | Guide |
| 51 | K55 | Info |
| 52 | K56 | screen arrow up, Blue on the 885 |
| 53 | K51 | Skip |
| 54 | K52 | Forward |
| 55 | K53 | Pause |
| 56 | K54 | Play |
| 57 | K57 | 2 |
| 58 | K58 | 3 |
| 59 | K59 &dagger; | 6 |
| 60 | K60 &dagger; | Yellow, 885 only |
| 61 | K61 | 5 |
| 62 | K62 | 9 |
| 63 | K63 | Enter |

## 5o. A generated config on real hardware - WRITTEN, AND HALF OF IT WORKED

On 2026-08-22 a config built by these tools was written to a real Harmony 525,
read back, and then replaced with the original. This section is what that
bought, because the interesting half is the part that failed.

### What was written

A fifth device was added to a 525 that had four: a new infrared group of 39
class-5 records, a new mode, 28 physical bindings and 11 LCD bindings, plus a
second page on the `Devices` menu to reach it from. The config grew from 78,486
to 94,712 bytes.

The device was a Samsung TV whose original remote had been replaced by a
generic RM-D613, so there was a real handset to check against. Two independent
public sources, an IRDB profile and an unrelated LIRC capture of the same
remote, agree on all 39 function numbers and names and on Samsung32 device 7,
subdevice 7. Four passive captures from the actual handset then confirmed the
protocol on hardware.

### What the file got right

None of this is what failed, so it is worth separating out.

**The infrared is exact.** Against the four measured frames, the generated
records match the real remote's timings **to the microsecond across all 64
bit cells**, on all four. Only the trailing mark and the final gap differ, by
less than the receiver quantises. Carrier 38,001 Hz against a measured 38 kHz,
frame period 108,504 us against a measured 108,484 to 108,524.

**The slot convention is Logitech's, not invented.** The arch-9 database
template in this file uses a 500 ms lead-in with an empty held stream for a
one-shot, and a 50 ms lead-in with a populated held stream for a repeatable
key. The generated records follow it: 30 one-shots and 9 repeatable. What
differs is the held body, a complete Samsung frame instead of an NEC short
repeat burst, and that is because the remote's own held capture shows seven
complete frames rather than repeats.

**The button map closes.** 28 physical bindings and 11 soft keys across three
LCD pages reach all 39 commands, each exactly once, with none left over. The
new mode's physical list has the same 43 entries and the same event set as a
device mode Logitech's compiler produced, including encoding a dead key as
opcode `0x00` with operand 0. Its three LCD pages render with the same
`1 OF 3` footer convention as every other multi-page device menu.

The honest limit on all of that: because the device could not be reached, **not
one of those buttons was ever pressed on the remote.** The infrared is verified
against a real handset and inside the file. It has never been verified coming
out of the 525.

### What the write proved

Concordance 1.5, `--no-web --write-config`, on architecture 9:

- it erased exactly the two sectors the arithmetic predicted, `0x820000` and
  `0x830000`, and nothing else;
- it wrote 94,712 bytes in 31 chunks and 1,506 data reports, and its own
  readback compared all of them byte for byte;
- the remote rebooted, reported a valid config, and its own accounting moved
  from 19% to 24% of 384 KiB;
- **a fresh dump taken afterwards was byte-identical to the file that went in.**

So the container, the trailer checksum, the relocation of every pointer, the
200 original infrared records and the 135 original screens all survive a real
write. Round-trip equality against hardware is now measured, not argued.

### What it did not prove, and this is the useful part

**The fifth device was invisible.** The `Devices` menu still showed four, and
the paging arrows did nothing. The second page was in the file and no eye ever
saw it.

The cause is a difference nobody had looked for. `Devices` is not a paged menu.
Its mode has a one-entry physical list holding `0x72` (section 4o) rather than
the 43-entry list a device mode has or the empty list an activity has. Grep the
sample for every mode with a `0x72` handler and there are twenty of them:

```text
45, 47, 51, 53, 54, 55, 56, 69, 72, 82, 83, 84, 90, 91, 92, 94, 96, 100, 108, 112
```

**Every one has exactly one page.** The only multi-page modes in the file are
the four device modes and two activities. Adding a second page to a `0x72` menu
produces a page the firmware never renders.

### Why every check passed anyway

This is the point of writing it down. The file was checked hard before it went
anywhere:

- exact round trip, 94,712 bytes identical, through the unmodified decompiler;
- both container checksums valid;
- all 200 original infrared records expanded and compared word for word;
- all 135 original screens rendered and compared pixel for pixel;
- an independent third-party reader accepted every new record;
- the new mode's physical list has the same 43 entries and the same event set as
  a mode Logitech's own compiler produced.

None of that could have caught it, because **the renderer and the semantic
verifier both read the page array, and the page array was correct.** It is the
same shape of mistake as the class-5 pointers in 4n and the trailer checksum in
4m, and it was found the same way both of those were: by an oracle that was not
one of ours. This time the oracle was the remote.

> **Corrected.** This paragraph first went on to say that the firmware does not
> read the page array for this kind of mode. It does, and 4q reads the routine
> that does it. What was missing is the key that asks for the next page, which
> lives somewhere else entirely. The page array here was right and unreachable,
> which is worse than wrong.

The check that would have caught it is small and is now the standing question
for any new mode: *does any mode in this file with the same handler shape have
more than one page?* Here the answer was no, twenty times over. That question is
still worth asking, but 4q is what the answer meant, and 5p shows that other
architectures do page a menu of this shape.

### Recovery, as executed rather than as argued

The original was restored with

```sh
concordance --no-web --write-config config.EZHex
```

and a dump taken afterwards is byte-identical to the backup. No `--force`, no
safe mode, no network. That command had been written down for nine days as a
plan; it is now a procedure that has been run.

Two things about it are worth knowing before anyone needs it:

- an invalid config does not block recovery. `GetIdentity` succeeds anyway, the
  CLI prints `WARNING: Invalid config found` and carries on, so a remote whose
  config was interrupted mid-write can still be re-identified and rewritten;
- **exit code 0 is not acceptance.** `reset_remote` re-identifies after the
  reboot and deliberately swallows `LC_ERROR_INVALID_CONFIG`, because that is a
  legitimate state after a firmware update. The gate that does hold is the
  readback: on a mismatch the run stops *before* the reset, leaving the old
  config running. Judge acceptance on the remote's screen.

## 5p. Counting the devices in any config, and what arch 14 does that arch 9 does not

> **Read 5v with this.** Section 5 is the **infrared group array**, and the count
> below is a group count that happens to equal the device count because the
> compiler writes one group per device. Everything measured here stands; what it
> is called does not.

Section 5o ends on one question: how does a config say there are five devices
rather than four. Nobody here has a five-device arch 9 config, but there are
five-device configs on other architectures already sitting in this project's
issues, and they turn out to answer part of it.

### Two ways to count devices, and both work across architectures

**Section 5.** Its first byte is the device count, followed by one `u24` per
device. Checked against ground truth on the 525 sample (4) and on arch 14: a
650 and a Harmony 600 at 4, and the eight Harmony 700 configs uploaded to
[issue #9](https://github.com/trelowney/harmony-decompiler/issues/9), which are
one account's own history and run **4, then 5, then 6**. Arch 8 configs answer
too, but nothing here has checked those numbers against a known setup.

**The state tree.** The `0xFEED ... 0xBEEF` tree of section 5c has the same node
layout everywhere: tag `0xA7`, a `u16` length, a `u16` parent id, a `u16` own id,
then the name. One reader gets a 525, a 650, a 700, a 600 and a Harmony One with
no change. On arch 12 and arch 14 the tree carries four variables per device
whose names end in that device id (`PowerOnDelay_7270811_65278` and friends), so
the distinct ids are the devices, and the tree also names their types (`TV`,
`Bluray`, `Receiver`). Arch 8 and arch 9 configs do not carry those variables at
all; there the tree is silent, which is a difference between architectures
rather than a failure.

`tools/count_devices.py` does both and says when they disagree.

A third base fell out of this: the protocol 12 Harmony One puts its blob at
**`0x40000`**, where arch 8 and arch 9 use `0x20000` and arch 14 uses `0x30000`.
Section 5j lists the first two; this is the third. The recovery rule in 5j still
gives it without being told, because the header states the address of its own
end.

### Section 14 is a table of render stream alternatives - CORRECTED

> **Corrected 2026-08-24.** What stood here described two records with a flag
> byte of 1 forming a ring of devices, each item `11 <u16> 7F 00` with the last
> pointing back at the first. That was read one byte out of phase, and the
> phase error made a ring out of something that is not one. The structure below
> comes from @glenharris's [PR #30](https://github.com/trelowney/harmony-decompiler/pull/30),
> and unlike the old reading it makes a claim that can fail: it says the last
> three bytes of every item are a pointer into section 11's own list of render
> streams. It does not fail once in twelve configs.

A section 14 record is:

```
u8  flag          2 in every record of every config here
??  count         u8 on arch 9, u16 on arch 14
    item[count]   u16 key, then a u24 pointer to a render stream
u8  trailing      0 in every record of every config here
```

The old size formulas, `3 + 5 * count` on arch 9 and `4 + 5 * count` on arch 14,
give the same totals and are not wrong about length. They were wrong about where
the items begin, which is the part that decides what the bytes mean.

The check that settles it: take section 11, which is a `u16` count followed by
that many `u24` pointers, and treat it as the set of legal render streams. Then
ask how many section 14 items point at a member of that set.

| config | devices | s14 records | items | items landing on a listed render stream |
|---|---|---|---|---|
| 525, arch 9 | 4 | 11 | 22 | **22** |
| 600, arch 14 | 4 | 29 | 3,806 | **3,806** |
| 650, arch 14 | 4 | 29 | 3,810 | **3,810** |
| 700 r2670 | 4 | 29 | 3,806 | **3,806** |
| 700 r2672, r2673 | 5 | 33 | 4,760 | **4,760** |
| 700 r2865 and five later | 6 | 37 | 5,706 | **5,706** |

Read one byte later, the way this file had it, the count is right but the
pointers are nonsense: 0 of 22 on the 525, 0 of 1,762 on the 650. There is no
middle ground, which is what a phase error looks like.

So section 14 answers "given this key, which render stream do I draw", and
section 4o's `0x72`, which searches section 14 for a whole 16-bit operand,
is searching these keys, and the record it lands on hands back a screen to
draw.

### What arch 14 has per device, and arch 9 does not

The per-device structure survives the correction, but it is not a ring and it is
not the records the old text pointed at. On arch 14, section 14 carries **two
records of 21 items and two records of 451 items for every device**, and nothing
else in it scales:

| config | devices | records of 21 | records of 451 | total records |
|---|---|---|---|---|
| 600 | 4 | 8 | 8 | 29 |
| 650 | 4 | 8 | 8 | 29 |
| 700 r2670 | 4 | 8 | 8 | 29 |
| 700 r2672, r2673 | 5 | 10 | 10 | 33 |
| 700 r2865 and later | 6 | 12 | 12 | 37 |

Which is where `4 * devices + 13` comes from, and now with the four named: two
21-item records and two 451-item records each.

**Arch 9 has neither.** The 525's eleven records are seven of one item, one of
three and three of four, with no 21 and no 451 anywhere. What it has instead is
in 4q: the `Devices` menu page carries a binding list of four `0x7F` entries with
consecutive operands, tagged with the four soft keys. Four keys on the screen,
four devices. The arch 14 recipe does not transplant.

### The page and item counts have been checked against a human - CONFIRMED

Everything above and in 4q reads page counts out of a file. On 2026-08-24
@psolyca, whose 650 is the sample used here, wrote down what the remote itself
shows: for each device and activity, how many pages it has and how many commands
are on them. Nobody had read that off the bytes, which makes it the first
outside check this file has had on the page reader.

| what the owner counted | pages | commands | mode in the file |
|---|---|---|---|
| TV Samsung | 6 | 21 | 83 |
| Recepteur AV Sony | 5 | 19 | 114 |
| Xbox 360 | 5 | 18 | 63 |
| Enregistreur numerique | 2 | 7 | 205 |
| activity "Console" | 4 | 13 | 104 |
| activity "Regarder la TNT" | 2 | 5 | 71 |
| activity "TV par internet" | 2 | 8 | 105, 106 or 193 |

The 650 has nine modes with more than one page. Six of the owner's seven
page/command pairs pick exactly one of them, with no mode used twice; only the
2-and-8 pair has more than one candidate. The state tree names the same four
devices he does, as `TV_Samsung_Power_2`, `Recepteur_AV_Sony_Power_2`,
`Xbox_360_Power_2` and `Enregistreur_numerique_Bouygues_Telecom_Power_2`.

One thing worth having from it: mode 193 is a **one-entry `0x72` menu with two
pages of four items each, each page with its own binding list and its own screen
program**. That is the shape 5o built for a fifth device and could not get the
remote to draw. Logitech's compiler ships it on a working remote, and it gives
every page a screen program of its own rather than sharing one.

### A paged `0x72` menu is a thing the real compiler builds

This matters because 5o built one and it did not work. Grouping every mode by
the shape of its physical list, the way `tools/check_525_mode_pages.py` does:

| config | one-entry `0x72` menus | pages they have |
|---|---|---|
| 525 (arch 9) | 14 | all 1 |
| 700, 4 devices | 2 | 1 and 2 |
| 700, 5 devices | 2 | 1 and 3 |
| 650 | 2 | 1 and 2 |
| 600 | 2 | 1 and 2 |

So the shape is legal and Logitech ships it. The 525 sample simply never uses
it, which is exactly why the oracle in `check_525_mode_pages.py` refused it and
why that refusal was the right call on the evidence available: one config is not
a survey. It says the file has no precedent, not that the firmware cannot.

The reason the 525 write failed is in 4q, and it is not the page array.

### What would still settle it

An arch 9 config with five or more devices, from a 525, 520, 510 or the Xbox 360
remote, which is what
[discussion #33](https://github.com/trelowney/harmony-decompiler/discussions/33)
asks for. Failing that, a before-and-after pair from any remote whose official
software still runs, dumped once, one device added, dumped again. The 700
history is nearly that already, but its revisions differ by more than one edit.

> **The pair arrived, twice over.** @psolyca did precisely this on his 650 on 25
> August: three dumps at 4, 5 and 6 devices, one addition apart, the first of them
> byte identical to the sample already here. Measured in 5t. It is protocol 14, so
> the arch 9 question above is still open and discussion #33 still stands.

## 5s. The base is recovered from the file, and two readers were wrong because it was not - MEASURED

5j worked out in August that `config_base` is `0x30000` on protocols 10 and 14
and said how to recover it: the `u32` at offset 4 is the address of the end
marker, so subtract where the marker sits. It then said "none of this is support
for those protocols" and stopped. **The document knew and the code did not**,
which is the same shape as section 8 being unread rather than unknown.

[@Rtas-17](https://github.com/Rtas-17) closed that gap in
[PR #31](https://github.com/trelowney/harmony-decompiler/pull/31) on 18 August
and withdrew it himself six days later while his own work was moving. His
diagnosis has since been checked against a file he never saw.

### What the constant cost

`CONFIG_BASE` was a module constant in `hconfig.py`, `0x20000`, read in about
thirty pointer computations. A protocol 10 config was therefore parsed with every
address `0x10000` too low. That does not fail loudly. It fails by recognising
almost nothing and passing the rest through as raw bytes, so the file still
rebuilds and the round trip differs in **one byte**: the end address the compiler
restamps.

Measured on [@kkong42](https://github.com/kkong42)'s Harmony 895, uploaded to
issue #34 on 25 August, a week after the PR was withdrawn:

| base | regions read | round trip |
|---|---:|---|
| `0x20000`, assumed | 36 | differs at the end address |
| `0x30000`, recovered from the file | **1,412** | **identical** |

The second reading of the two claims holds on that file as well: the end marker
is not the last thing in the blob. Of his five reads, three are byte identical
and carry no padding, one carries 54 zero bytes and one carries 216. So a file's
length says nothing about where its config ends, and the amount of padding is not
a property of the remote or the protocol.

Across every sample this repository holds, the change takes the round trip from
**14 of 14** to **19 of 20**. Nothing that already passed changed, in bytes or in
coverage. The one that still fails is `H890-Bedroom-2`, and it fails because it is
the damaged dump of 5k: its duplicated 54-byte blocks move the end marker, so the
recovered base comes out `0x2FCA0` rather than `0x30000`. **The detection reports
the damage instead of hiding it**, which is the right way round.

@dannybloe reached the same recovery independently and then found its limit: his
section 117 calls `end_addr - offset_of_end_marker` circular, because the file
states both, and anchors the base on the clock record instead. That works on the
damaged 890 as well, where this does not. Worth taking if arch 10 is picked up.

### The same bug in a second reader, and what it was telling us

`count_devices.py` derived the base its own way, `u32(blob, 4) - (len(blob) - 4)`,
which is the file's length and not the marker's offset. So it called four reads
damaged. Two of them were clean and had only picked up trailing zeros:
`H890-Bedroom-1` and `H895-Read-3`. With the padding stripped first, the
page-boundary complaint fires on exactly the two dumps that are independently
known to be damaged, and `H895-Read-1` is one of them: `repair_890_dump.py`
removes 7 duplicated blocks from it and the result is **byte identical to
`H895-Read-2`**, a separate read of the same config. That tool was built on 890s
and had never seen an 895.

### Section 5 is a device array on three architectures and is not one on arch 10

Fixing the base exposed the reader underneath it. `count_devices.py` printed
"section 5 says 9" for the 895. kkong42 had written down what his remote holds,
and it is **six** devices.

He is right and the tool was wrong. Section pointer 5 does not reach a device
array on arch 10 at all:

| file | count byte | its u24s that land inside the file |
|---|---:|---|
| 525, arch 9 | 4 | 4 of 4 |
| 880, arch 8 | 4 | 4 of 4 |
| 885, arch 8 | 7 | 7 of 7 |
| 650, arch 14 | 4 | 4 of 4 |
| **890, arch 10** | 9 | **2 of 9** |
| **895, arch 10** | 9 | **1 of 9** |

And the thirty bytes at that address are **byte for byte the same in both arch 10
files**, which are two different remotes of @kkong42's, his 890 of issue #27 and
his 895 of issue #34, carrying two different configurations:

```
09 00 00 1e 00 00 09 00 00 01 0a 00 00 02 0b 00 00 03 0c 00 00 04 0d 00 00 05 0e 00
```

Read as four-byte records from offset 3 it is nine entries pairing 0 to 8 with 9
to 17, which is a fixed mapping table and not a pointer array. What it maps is
not known here and is not guessed at.

So the reader now believes the count only when the array it heads reads as an
array: every u24 has to be an address inside this file. Arch 8, 9 and 14 give N of
N and are unaffected; arch 10 is refused by name. **The refusal is not a rule
about arch 10.** It is the array failing to be one, and it would have caught this
without kkong42 telling us the number. He is why anybody looked.

> This is the fourth time an internally consistent reading of this format has been
> wrong, after the trailer checksum in 4m, the class 5 pointers in 4n and the page
> array in 5o. It is also the second time a human inventory caught something no
> self-check would, after [@psolyca](https://github.com/psolyca)'s page counts in
> 5p.

### The section table never bounded the whole file, on any architecture

Worth stating with a number, because a reader meeting the section table for the
first time will assume its spans tile the config. They do not, and they never did:

| file | blob | below the lowest section pointer |
|---|---:|---:|
| 525, arch 9 | 78,486 | 62,299 (79.4%) |
| 880, arch 8 | 393,040 | 123,041 (31.3%) |
| 885, arch 8 | 529,924 | 250,225 (47.2%) |
| 890, arch 10 | 396,927 | 124,962 (31.5%) |
| 895, arch 10 | 342,753 | 49,766 (14.5%) |
| 650, arch 14 | 845,133 | 275,224 (32.6%) |

On the 525 that low region is 79% of the file and the decompiler reads 99.12% of
the whole thing, so it is neither unreachable nor unread: it is reached by
pointers held *inside* sections rather than by the section table. This is 4's
"a span is where a subsystem's table sits, not a fence around its data", measured
on five architectures instead of argued on one.

It matters for the next section, where a device's bytes turn out to land there.

## 5t. What one added device costs, on three configs that differ by one - MEASURED

5p ended by saying what would settle the device question: "a before-and-after
pair from any remote whose official software still runs, dumped once, one device
added, dumped again". [@psolyca](https://github.com/psolyca) did exactly that on
his Harmony 650 and posted the three files to issue #8 on 25 August. He dumped
the config, added `TV LG`, dumped, added
`Enregistreur numérique Bouygues Telecom (2)`, dumped again.

**The first of the three is byte for byte `samples/harmony650/Harmony_650.EZHex`**,
the file 5p already read pages and modes out of and checked against his own hand
written inventory. So this is not three strangers' files. It is a config this
project understands, plus one device, plus one more.

`count_devices.py` reads 4, 5 and 6, with section 5 and the state tree agreeing
on every one, which is the count arriving from two structures rather than one.

### It does not unblock the 525

These are protocol 14. The open question in 5o is what an **arch 9** compiler does
for a fifth device, and nothing here answers it. Discussion #33 stays open and
nothing goes onto the remote.

### What it costs, and where it goes

| | dump 1 to dump 2 | dump 2 to dump 3 |
|---|---:|---:|
| whole blob grows by | 46,638 | 41,555 |
| the section spans grow by | 9,700 | 11,114 |
| the rest | **36,938** | **30,441** |

The rest is not unaccounted. It is the low region of 5s, and the arithmetic is
exact: in both steps the leftover equals, to the byte, how far the lowest section
pointer moved forward. So **roughly four fifths of a device lands below the
section table's reach**, and the fifth that does not is spread across the tables:

| section | dump 1 | +1 device | +2 devices |
|---:|---:|---:|---:|
| 0 | 1,629 | +358 | +396 |
| 4 | 845 | +219 | +129 |
| 5 | 13 | +3 | +3 |
| 6 | 100,685 | +1,914 | +3,736 |
| 8 | 2,289 | +387 | +304 |
| 9 | 1,935 | +288 | +288 |
| 10 | 15,989 | +3,654 | +3,381 |
| 11 | 11,447 | +2,838 | +2,838 |
| 13 | 227 | +27 | +27 |
| 14 | 191 | +12 | +12 |
| 1, 2, 3, 7, 12, 15, 16, 17 | | +0 | +0 |

Nothing shrinks. Eight sections do not move at all, and section 5 gains exactly
three bytes per device, which is the one u24 its array of 5p gains. Sections 9,
11, 13 and 14 gain the *same* number of bytes for both devices, so what they hold
is per device and fixed size. Sections 0, 4, 6, 8 and 10 gain different amounts
for the two, so what they hold depends on the device.

All eighteen section pointers move in both steps, so none of them anchors
anything: the file is rebuilt, not patched.

### The name, and how it is spelt

Neither device name appears in the file as typed. Both appear once, with spaces
replaced by underscores, in **Latin-1** and not UTF-8: the `é` is a single `0xE9`.
Both land inside **section 0**, whose span grows by 358 and then 396 bytes, and
the same section holds both, which was the check: two devices added the same way
by the same compiler have to put their names in the same place. The name is not
where the bulk of the bytes went; those are in the low region.

That is the grammar @dannybloe describes for the state tree, where a device node
is `<label>_Power_2` and the underscore is the separator, which is why his
composer refuses a label that contains one.

### What this does not say

The per-section growth is measured; what is *in* those bytes is not. In
particular section 6 gains 1,914 and then 3,736 bytes and this does not claim to
know what they are. `codex-work/tooling/measure_arch14_device_delta.py` produces
the table and carries three negative controls: the same file against itself, the
padding-only pair `H895-Read-2` against `H895-Read-3`, and an in-memory
single-byte pointer mutation that has to make the arithmetic fail. It is not in
this repository because it reads containers the decompiler refuses, and adding
`GSPM` to `ARCHITECTURES` would claim support that does not exist.

## 5u. The section table is a different length on every architecture, and arch 10 carries a zip - MEASURED

[@glenharris](https://github.com/glenharris)'s ImHex pattern, merged in PR #30,
says in a comment what no section of this document said: arch 9 has 18 sections
and arch 8 has 20 slots. It also names the trap, which this project had fallen
into and climbed out of without writing down why:

> A null in the section table is not padding and not the end of the table. It
> means that subsystem is absent, and arch 8 has one in the middle. Reading until
> the first zero silently truncates a 20-entry table to 8.

`hconfig.py` reads the table correctly today, from `0x0C` up to the head marker
with only trailing zeros stripped, and its comment explains the null. The
document is what was behind, so this is that gap closed rather than a new
reading.

### Measured, on nine configs

The table starts at `0x0C` and the head marker follows it. Entries are the slots
up to and including the last non-zero one; the rest are padding.

| arch | cookie | head marker | at | entries | interior null |
|---:|---|---|---:|---:|---|
| 9 | `AHCM` | `CMAH` | `0x5B` | 18 | none |
| 8 | `TPTP` | `WLWL` | `0x5F` | **19** | index 8 |
| 10 | `TPTP` | `WLWL` | `0x67` | **21** | none |
| 14 | `GSPM` | `LWJL` | `0x5B` | 18 | none |

Arch 8 and arch 10 share a cookie, an end marker and a head marker and differ in
the length of the table between them. So **the cookie does not determine the
layout** and neither does the marker: the length has to be read.

Counting the padding slot as an entry gives Glen's 20 for arch 8, which is the
same measurement described the other way round.

**The four counts in that table are two short each, and the "padding" is not
padding.** The correction is below and it is arithmetic: the table starts at
`0x0B`, not `0x0C`. The addresses are unaffected. Read the counts as 20, 21,
23 and 20.

### Correction: the table starts at `0x0B`, not `0x0C`, and the counts above are two short - MEASURED

The lengths in the table above are undercounts, and the argument that settles it
is arithmetic anyone can repeat.

[@dannybloe](https://github.com/dannybloe) reads the entries as
`{u8 spare; u24 address}` starting at `0x0B`. This document reads them as `u32`
starting at `0x0C`. **Both give byte-for-byte the same addresses**, because a
`u32` whose top byte is zero and a `u24` are the same number, which is why
nothing ever broke and why the disagreement went unnoticed.

They do not give the same *count*, and only one of them tiles:

| arch | marker at | from `0x0C` | from `0x0B` |
|---:|---:|---|---|
| 9 | `0x5B` | 79 bytes, **19.75 entries** | 80 bytes, **20** |
| 8 | `0x5F` | 83 bytes, **20.75 entries** | 84 bytes, **21** |
| 10 | `0x67` | 91 bytes, **22.75 entries** | 92 bytes, **23** |
| 14 | `0x5B` | 79 bytes, **19.75 entries** | 80 bytes, **20** |

Measured on every container in `samples/`: fifteen arch 8 and arch 10 files, the
525 and the 650. **Four architectures, and reading from `0x0C` leaves three
quarters of an entry on all four.** A table cannot end three bytes into its last
slot, so the start is wrong by one byte, and reading from `0x0B` leaves nothing
over anywhere.

**And this project's own firmware reading says the same thing and has since it
was written.** The seek routine `0x066A8` computes `4 * N + 11` before it seeks,
recorded above in [all sixteen seek call sites](#all-sixteen-seek-call-sites-and-what-each-does-next---measured).
Eleven is `0x0B`. Entry N begins at `0x0B + 4N`, which is Danny's framing
exactly, so the 525's own firmware settles a disagreement about a file format
that neither reading of the bytes could settle alone.

What this changes:

- **the entry counts are 20, 21, 23 and 20**, not 18, 19, 21 and 18. Arch 8 and
  arch 10 still differ by two, as before, so the finding that the cookie does not
  determine the layout is untouched;
- **there is no padding between the table and the head marker.** The seven zero
  bytes described above as padding on arch 10, and the equivalent run on the
  other three, are the last two entries and they are NULL. Glen's rule applies to
  them as it applies to the interior null on arch 8: a NULL is an absent
  subsystem, not the end of the table;
- `hconfig.py` is unaffected in what it resolves, because it takes the addresses
  and the addresses are the same. It reports a short count.

### The mapping was checked against our own copies, row by row - MEASURED

@dannybloe determined arch 10's slot mapping on 2026-08-26, his findings 178 to
183, and it is his. It was reproduced here on this project's copies of the two
arch 10 remotes, using only what each row of his own table claims, with six
values measured independently first as controls.

**Eleven of his seventeen present rows confirm, four are not checkable, and two
were contradicted by the reading corrected above.** The two are base slots 18 and
19 at raw 21 and 22, which do not exist in a 21-entry table and do exist, NULL,
in a 23-entry one. **The four not checkable are exactly the four he places by
order between anchors rather than by their contents**, which is his own
distinction holding up on a corpus he did not use.

One row is worth naming because it is a count reached from both ends: raw slot 6
on the 890 holds four infrared groups of 79, 83, 64 and 74 records. That is
**300**, the number of records he locates by a different route entirely, each one
stating its own address.

`codex-work/tooling/verify_dannys_arch10_mapping.py` is the row-by-row verifier.
It is not in this repository: it needs the arch 10 containers, and one of the two
is not public.

### Most of what follows about arch 10 is @dannybloe's, and was already published

Written after the fact, which is the point. Everything in this subsection and the
next was measured here on 26 August and only then checked against
[dannybloe/harmony-explorations](https://github.com/dannybloe/harmony-explorations),
where two of the three claims already sat, in `docs/config-format.md` and in his
sections 115, 117 and 122. His versions are stronger than the ones re-derived
here and are cited in place below. The rule about checking whose a finding is
before claiming it is in this project's own notes; the rule was there and an
index of his work was not. There is one now,
`codex-work/tooling/index_danny.py`.

### The subsystems are not at the same indices on arch 10

The record array index of 4b is a pointer table with a three-byte head, `u16`
count then a zero byte, and in every config here there is **exactly one** table
of that shape reachable from the section table. That makes it a fingerprint:

| arch | the one head-3 table is at index |
|---:|---:|
| 9 | 6 |
| 8 | 6 |
| 14 | 6 |
| **10** | **9** |

Three more entries and the fingerprint three places further along. So arch 10 is
not arch 8 with extras bolted on the end; subsystems are inserted before index 6
and everything above them shifts.

**@dannybloe had this, and went further.** He counts 23 slots on arch 10 rather
than 21 entries because he reads the table from `0x0B` as `{u8 spare; u24
address}` rather than from `0x0C` as `u32`, which is the same bytes and the
better reading, since it explains why the high byte is always zero. And he did
not stop at "something shifted": he scored **all 1,330 ways of placing three
insertions** by asking seventeen of his readers to parse the result. The best
mapping reaches 34 of 47 with an eight-way tie, where arch 8, 9 and 14 each score
47 uniquely, and five of his readers are satisfied by no mapping at all. So it is
**not a relabelling**: the name tree, the log area, the mode records, the font
sets and the value maps differ in form on arch 10, not merely in position. He
gates every section reader off rather than guessing a mapping, and writes: a
guessed mapping turns twenty refusals into twenty plausible wrong answers.

### Which does not find the device array, and that was the test

The obvious next step fails, and it is worth writing down that it fails.

If arch 10 were arch 9 shifted by three, the device array of 5p would sit at
index 8 rather than index 5. @kkong42 states six devices for his 895 in issue
#34, so there is a number to hit. Searching every entry of the table, in all
three head shapes 4b knows, for an array whose targets all land inside the file:

* the 525 gives one entry matching its known 4, at index 5, head 1;
* the 650 and its two successors match their known 4, 5 and 6 at index 5, head 1, and index 5 is the only entry that is right on all three of them;
* the 895 gives **no entry at all whose count is 6**, at any index, in any head shape.

The 890 does have a `04` and four in-range `u24`s at index 6, which is the device
array's exact shape, and the 895's index 6 is one byte long and empty. So the two
arch 10 files do not even agree with each other.

Those four are named, and not as devices. @dannybloe's section 183 reads raw
slot 6 as **base slot 5, the infrared group array**: four groups holding 79, 83,
64 and 74 records, 300 in total. His base slot 5 and 5p's device array turn out
to be the same array at the same address on all twelve non-arch-10 containers,
which **5v** measures; the 895's zero is an empty infrared database and not a
device count, and where an arch 10 config states its devices is still unknown.

**Where an arch 10 config states its device count is unknown**, and section
pointer 5 is not it. `count_devices.py` refuses rather than guessing, per 5s.

That it would not be found this way was predictable from his result above, and
this search is a special case of the one he had already run. What it adds is a
third arch 10 model and a stated device count to hit.

### Arch 10 embeds a zip, and the other three do not

Searching the 895 for its device names by ASCII found none, and found this
instead: `PK\x03\x04` at `0x00C328`, a well-formed zip of 270 bytes ending at
`0x00C436`, which is exactly where section 4 begins.

It opens, `testzip()` returns `None`, and it holds one deflated member:

```
MetaData.xml    213 bytes, stored in 132
```

Identical in the 890 and the 895, and it is the HarmonyAssistant schema:

```xml
<MetaData><Class name="HarmonyAssistant" id="0"><Record name="AssistantMenu" id="0">
<Field name="Show" type="boolean"><Variant name="true" id="1"/><Variant name="false" id="0"/>
</Field></Record></Class></MetaData>
```

No `PK` header exists anywhere in an arch 8, arch 9 or arch 14 config here, and
the 895 holds exactly one archive: one local header, one central directory entry,
one end-of-central-directory record, and no other zlib-framed stream in the file.

**This part is new**, and it answers a question @dannybloe wrote down as open. He
records that no `0xFEED` frame validates anywhere in either of his 890 payloads,
and concludes that an arch 10 config is not known to state the names of its
devices and activities at all, where every other architecture does so in base
slot 0. The frame does not validate because on arch 10 that subsystem is
deflated.

So arch 10 stores as a compressed archive what arch 9 stores as the plaintext
name table in its section 0, wrapped in `0xFEED ... 0xBEEF` and described in 4. That is the same
subsystem in two spellings, and it is a reason for two things that looked like
gaps: an arch 10 blob has **no readable device names at all**, and
`count_devices.py`'s state-tree route reports nothing on it.

It does not follow that the device names are in the archive. They are not; those
213 bytes are the schema and nothing else. Where an arch 10 config keeps the
names is open, along with where it keeps the count.

### One arch 10 slot is identified, by a person rather than by a search

@dannybloe keeps every arch 10 reader gated off and is right to, for the reason
above. This is not that gate opening. It is one slot, picked by the head-3
fingerprint rather than by search, checked against something his two 890s do not
have: @kkong42 wrote down what is on every screen of his 895.

`tools/verify_525_semantics.py` reads modes from slot 6 as a `u24` count then
`u24` entry addresses, each entry carrying its page count at `+4` and its page
addresses at `+6`, and each page's list giving an item count. Pointed at slot 9
instead, with nothing else changed, that reading is put to his inventory the way
5p put the page reader to @psolyca's.

**The controls first**, because without them "it reads" means nothing:

| file | slot | result |
|---|---:|---|
| 525, arch 9 | 6 | reads, 114 modes, which is what `verify_525_semantics` already prints |
| 525, arch 9 | 9 | refuses: a count of 1,805,832 |
| 895, arch 10 | 6 | refuses: a count of 0 |
| 895, arch 10 | 9 | reads, 169 modes |
| 890, arch 10 | 9 | reads, 137 modes |

The wrong slot fails on both files and the right one works on both, so a clean
read is worth something here. 169 and 137 are also exactly the entry counts the
head-3 fingerprint gave for those two files, independently.

**Then the check.** Of his eleven screens, taken as a (pages, total items) pair,
four pick exactly one mode and **no mode is claimed by two screens**:

| screen | pages, items | modes matching |
|---|---|---:|
| Activity 1, Watch a DVD | 1, 5 | 1, at `0x033C02` |
| Activity 5, Listen to Radio | 1, 8 | 1, at `0x032DFA` |
| Device 1, AV Receiver | 1, 7 | 1, at `0x0331FA` |
| **Device 4, Freesat** | **3, 20** | **1, at `0x0353EC`** |
| the other seven | 1, 1 / 1, 3 / 1, 4 | 2 to 6 each |

The seven that do not resolve are screens carrying one, three or four buttons,
and this config has several modes of each of those shapes. That is the reading
being unable to tell two identical things apart, not the reading being wrong.

**The Freesat is the one that carries the weight, and it carries more than the
table shows.** Only two modes in the file have three pages at all. His device 4
has twenty buttons over three pages, and he wrote out which are on which:

```
page 1   PgUp PgDown List Audio Bookmark BookmarkList Media Opt+       8
page 2   PwrToggle Return Schedule Sleep Slow Source Subtitle Teletext 8
page 3   TvPortal TvRadio VFormat Wide, then four blanks               4
```

Mode `0x0353EC` reads **8, 8, 4**. Not the total, the distribution. The other
three-page mode reads 8, 8, 8.

So slot 9 of an arch 10 section table is the mode array, and `0x0353EC` is
@kkong42's Freesat device screen. **That is one slot known, not a mapping**, and
his warning about guessed mappings is unaffected: nothing else here is ungated,
and the seven ambiguous rows are reported as ambiguous rather than assigned.

`codex-work/tooling/arch10_modes_vs_inventory.py`. It is not in this repository
because it reads a container the decompiler refuses.

### The nine pairs at section 5 are the firmware event map, read one field late

That reading is withdrawn. The thirty bytes were not a fixed head on something
variable and the pairs were not records; they were the *middle* of a structure
this document already describes, entered five bytes after its start.

[@dannybloe](https://github.com/dannybloe)'s arch 10 slot mapping, his
`findings.md` section 183, puts **base slot 4 at raw slot 5** and notes that the
895's raw slot 5 is exactly 125 bytes, which is base slot 4's own size. Base
slot 4 is the firmware event map of 4r:

```
+0x00  u24  fallback
+0x03  u16  count, thirty
+0x05  { u8 key; u24 value }[30]
```

`3 + 2 + 30 * 4 = 125`. Pointed at raw slot 5 the event map reader accepts both
arch 10 files, and refuses at raw slots 4 and 6 on both:

| file | slot 4 | slot 5 | slot 6 |
|---|---|---|---|
| H890-Bedroom-1 | count is 5,416 | **reads, 125 B** | count is 44,804 |
| H895-Read-2 | count is 5,916 | **reads, 125 B** | count is 0 |

Keys 0 to 29, values 9 to 38, fallback 9, so `N = 9` on arch 10 against 11 on
the 525, 4 on arch 8 and 14 on arch 14. Entering the same 125 bytes at `+0x05`
instead of `+0x00` gives key 0 against value 9, key 1 against value 10, and so on
until the thirty bytes run out at key 8 against value 17. That is exactly what
was reported as nine pairs.

**All 125 bytes are byte identical in the 890 and the 895**, sha256
`94155a53...`, at two different addresses. The earlier claim of thirty identical
bytes understated it by 95, because it stopped where the misreading stopped. On
the 890 the *span* is 1,037 bytes and the event map is the first 125 of them; the
other 912 belong to whatever the compiler wrote next, which is 4k's rule that a
span is where a subsystem's table sits and not a fence around it.

This closes one of the four rows @dannybloe placed **by order** rather than by
content, and it is his mapping that made the reading possible.

### The two rows his mapping was contradicted on were our off-by-one

`codex-work/tooling/VERIFY-DANNYS-ARCH10-MAPPING-REPORT-2026-08-26.md` returned
11 confirmed, 2 contradicted and 4 not checkable. Both contradictions were his
NULL entries at raw 21 and 22, refused because the harness read a 21 entry arch
10 table. 5u's own correction later the same day says the table starts at `0x0B`
and holds **23** entries, and under that framing both rows read:

```
890   entry 20  spare 00  addr 0x0598C1     895   entry 20  spare 00  addr 0x04727B
      entry 21  spare 00  addr 0x000000           entry 21  spare 00  addr 0x000000
      entry 22  spare 00  addr 0x000000           entry 22  spare 00  addr 0x000000
   0x67  'WLWL'                                0x67  'WLWL'
```

The seven zero bytes that report called padding are two NULL table entries and
one spare byte, and `WLWL` begins at `0x67`, exactly where a 23 entry table ends.
Row 6 to 9 is also no longer placed by order alone, because 5t read raw slot 9 as
the mode array from @kkong42's own page counts. **Fifteen confirmed, none
contradicted, two not checkable**, those two being 13 to 16 and 14 to 17.

## 5v. The device array is the infrared group array, and it has been one array all along - MEASURED

5p calls section 5 the **device array**. [@dannybloe](https://github.com/dannybloe)'s
`findings.md` section 183 calls base slot 5 the **infrared group array**. On arch
8, 9 and 14 that is the same slot index, so either a device is an infrared group
or one of the two names is wrong. The 895 is the case that looked like it would
part them, because its array is empty and its owner can see six devices.

They are the same array. Not two readings of one span, the **same address**:

| container | arch | slot | address | devices | groups | records | class |
|---|---:|---:|---|---:|---:|---:|---:|
| 525 | 9 | 5 | `0x02FEA2` | 4 | 4 | 8+67+61+64 = 200 | 5 |
| 880-Bedroom | 8 | 5 | `0x03E650` | 4 | 4 | 79+83+64+74 = 300 | 1 |
| 880-Spare-1 | 8 | 5 | `0x03E650` | 4 | 4 | 300 | 1 |
| 885-Bedroom | 8 | 5 | `0x03E650` | 4 | 4 | 300 | 1 |
| 885-LivingRoom | 8 | 5 | `0x05D968` | 7 | 7 | 460 | 1 |
| Update | 8 | 5 | `0x038B59` | 3 | 3 | 234 | 1 |
| Update-1 | 8 | 5 | `0x04D351` | 6 | 6 | 397 | 1 |
| Update-2 | 8 | 5 | `0x050A5F` | 7 | 7 | 454 | 1 |
| Update-3 | 8 | 5 | `0x050FED` | 7 | 7 | 462 | 1 |
| 650 | 14 | 5 | `0x073CDF` | 4 | 4 | 236 | 1 |
| 650 +1 device | 14 | 5 | `0x07CF6A` | 5 | 5 | 308 | 1 |
| 650 +2 devices | 14 | 5 | `0x084860` | 6 | 6 | 350 | 1 |
| 890 | 10 | **6** | `0x04EB4F` | refused | 4 | 79+83+64+74 = 300 | 1 |
| 895 | 10 | **6** | `0x03C4C1` | refused | 0 | none | - |

**Twelve of fourteen**, same address and same count, and the group reading is the
stronger of the two: `devices_from_section_5` counts pointers, while this one
also requires every group to open with `u8 zero`, `u16 count` and `u24` record
addresses, and every record to land on a class 1 or class 5 header carrying the
self pointer of 4h. 3,201 records read that way across the corpus and not one
group refused.

So `count_devices.py` has been counting infrared groups and calling them devices,
and it was right because the compiler writes one group per device. It was right
for a reason it did not state, which is a different thing from being right.

### What that costs, and it lands on the write that is blocked

A count that is really a group count can be wrong in two ways, and neither has
been seen or ruled out:

* @glenharris's rule, 4k, is that a child an earlier subsystem already wrote is
  **pointed at rather than written again**, and 156 shared targets were found on
  the 525. Two devices with the same infrared database would then be four groups
  for five devices;
* a device with no infrared codes at all - a device the user added and never
  taught - has nothing for a group to hold.

5t's three 650s add a device and gain a group each time, 4 then 5 then 6, so the
one-to-one held across two additions by a real compiler. That is the evidence
there is, and it is three files from one account.

### The 895's zero is real, and it agrees with him independently

`devices_from_section_5` refuses the 895 and the mapped raw slot 6 states a `u8`
count of **0**. That is not a broken read: @dannybloe's section 181 reads the
infrared database on arch 10 by a route that needs no slot at all, and finds that
**a Harmony 895 has none**. Two unrelated readings, one of a count and one of the
records, saying the same thing.

So the 895 stores no infrared database in this array, its six devices are stated
somewhere this document still cannot name, and **the zero must never be reported
as a device count**. 5s's refusal stands and is now explained rather than only
observed: raw slot 5 on arch 10 is the event map of 5u, and raw slot 6 is this
array.

### Two things that fell out

The 890's four groups hold **79, 83, 64 and 74 records**, which is byte for byte
the split in @kkong42's `880-Bedroom`, `880-Spare-1` and `885-Bedroom`. One room,
four remotes, two architectures, one infrared database.

And the records are **class 1 on arch 8, 10 and 14 and class 5 on arch 9**. The
decompiler counts regions of kind `ir_record_header` and therefore reports 200 on
the 525 and **zero on every arch 8 and arch 10 file**, while refusing all three
arch 14 files on their `GSPM` magic. The class 1 database is read by tooling and
is not in the decompiler's own region kinds.

`codex-work/tooling/slot5_device_or_ir_groups.py` and its report. Three negative
tests: pointing arch 10 at raw slot 5 refuses both roots and moves two of the
four numbers, and widening the count to `u16` or the addresses to `u32` refuses
every non-empty root and takes the agreement from 12 to 0.

## 5w. How much a 525 config may grow, and the ceiling is not the flash - MEASURED

A config on the 525 sits at physical `0x820000` and the remote's writeable
journal begins at `0x870000`. That is not a constant taken from anywhere: the
config states it itself, in the eight byte descriptor `00 20 00 00 07 00 00 08`,
which is `0x070000` to `0x080000` plus the `0x800000` transport offset, the same
region @glenharris's PR #30 reads as section 2's writeable flash.

| | bytes |
|---|---:|
| the public 525 sample | 78,486 |
| `0x820000` to the journal at `0x870000` | 327,680 |
| **room before the journal** | **249,194** |
| the largest config the firmware checksum path has been checked over | 131,074 |
| **room under that** | **52,588** |

The second row is the one that binds and it is not a storage limit. The boot
validator reads `end_addr` dynamically, subtracts the base and **divides the byte
count by two**, so it walks `(end_addr - 2 - 0x020000) / 2` words: 39,240 of them
today. That count stops fitting sixteen bits at `end_addr = 0x040002`, a config
of 131,074 bytes.

**What happens above that is not measured.** The four instruction words of the
validator are pinned and they show the loop; nothing here shows what the loop
does when its word count no longer fits. So 131,074 is the edge of what has been
checked, not a demonstrated failure point, and it should be measured before
anything goes near it.

Against that, one device on arch 9 costs about **19,379 bytes**:
`tools/build_525_samsung_t200hd.py` builds the sample plus a fifth device at
97,865 bytes against 78,486. That is not 5t's 46,638 and 41,555, which were
measured on a 845 KB protocol 14 container carrying its own screens and fonts.

So there is room under the checked ceiling for roughly **two more devices after
the fifth**, and room before the journal for about twelve. **Capacity is not what
blocks the five-device write.** What blocks it is that nobody has an arch 9
config a real compiler wrote for five devices, which is discussion #33.

`codex-work/tooling` and the 2026-08-13 batch; the script is not in this
repository because it reads the firmware image, Logitech's installed Java and the
Concordance binaries, none of which are redistributable.

## 5x. The thirty firmware events are the remote's error screens, and thirty of thirty are named - MEASURED

4r read section 4 as @dannybloe's firmware event map and left the question open:
the values select records in section 6, but nothing said **what raises event k**.
Two halves are answered here, and they are answered very unevenly.

### The thirty modes, read out of the config

The map's fallback is 11 on the 525 and its values are 11 to 40, so it names
modes 11 to 40 of the mode table. All thirty exist, each has exactly one page,
and each page's screen program renders:

| event | mode | screen | | event | mode | screen |
|---:|---:|---|---|---:|---:|---|
| 0 | 11 | Go to Website / to update settings | | 15 | 26 | Battery Fast / Charge |
| 1 | 12 | USB / Initialization | | 16 | 27 | Battery / Trickle Charge |
| 2 | 13 | Bootloader / Locked | | 17 | 28 | Battery Charge / Complete |
| 3 | 14 | Real Time Clock | | 18 | 29 | IR Sending |
| 4 | 15 | IR LEDs / and / Photodiode | | 19 | 30 | Invalid / Configuration |
| 5 | 16 | FLASH Memory / ID | | 20 | 31 | Bootloader / Bad Image |
| 6 | 17 | FLASH Memory / Erase | | 21 | 32 | Bootloader / Upgrade Failed |
| 7 | 18 | FLASH Memory / Write | | 22 | 33 | Battery ADC / Not Calibrated |
| 8 | 19 | FLASH Memory / Clear | | 23 | 34 | LightSense ADC / Not Calibrated |
| 9 | 20 | LCD Module / Failed | | 24 | 35 | Revision ADC / Not Calibrated |
| 10 | 21 | PLL Failure | | 25 | 36 | Application / Terminated |
| 11 | 22 | BootRAM / Truncated | | 26 | 37 | Configuration / Corrupted |
| 12 | 23 | Safe Mode | | 27 | 38 | Missing / License |
| 13 | 24 | Safe Mode / Requested | | 28 | 39 | USB CONNECTED |
| 14 | 25 | Battery Low | | 29 | 40 | Needs to be / setup. / Battery Charging |

**Section 4 is the remote's error and status screen table.** That also answers
what AN0 and the other two ADCs of 5m are for from the other direction: three of
the thirty screens are ADC calibration failures.

The decode was checked without using the glyph alphabet at all, which is the
point: if the text is right, a repeated word must be the **same sequence of glyph
codes**. `FLASH Memory` is code-for-code identical in modes 16, 17, 18 and 19;
`Bootloader` in 13, 31 and 32; and the four Battery screens have first lines of
11, 12, 7 and 14 codes, which is exactly `Battery Low`, `Battery Fast`,
`Battery` and `Battery Charge`.

### What raises them: four of thirty, and that is the honest number

The firmware side is much thinner. Every entry key is read to `0x73A`,
zero-extended through `0x014:0x015`, and compared against RAM `0x3F1:0x3F2`,
which `0x05E8E` copies from `0x3EF:0x3F0` immediately before the tail `GOTO`
into the reader. **Five producers write a literal into `0x3EF:0x3F0`**, and they
supply four distinct events:

| producer | event | screen | chain ends at |
|---|---:|---|---|
| `0x0246E` | 0 | Go to Website | action opcode `0x1F` |
| `0x04F8C` | 0 | Go to Website | main loop `0x04BFA` |
| `0x04DEA` | 25 | Application / Terminated | **caller cannot be determined** |
| `0x04F78` | 26 | Configuration / Corrupted | main loop `0x04BFA` |
| `0x04CB0` | 27 | Missing / License | main loop `0x04BFA` |

Three of the four land on screens the main loop plausibly owns, and the fourth is
an action opcode putting up the unconfigured-remote screen. **The other
twenty-six have no site in this image that a direct-transfer scan can find.**
That is reported as twenty-six unfound rather than twenty-six absent: the one
computed-`PCL` dispatch in the image, `0x01100`, was checked and names no
address in this chain, but a bootloader living outside `mcu.bin` would not be
visible here at all, and eleven of the thirty screens are bootloader, flash and
boot-RAM states.

The fallback is reached concretely: a mismatch decrements the file's own `u16`
count, and when it reaches zero with the match flag `0x736` still clear,
`0x05E28` copies the fallback to the mode operand and calls `0x05B6C`. **The
count is the file's, and 9022e3b already said nothing validates it.**

### Where the thirty sit among the 525's modes, and the rule is not the obvious one

The 525 declares 114 modes and every one of them has at least one screen. Thirty
of the 114 are the block above, `first` to `first + 29`, and **all thirty have
exactly one page**. Every multi-page mode in the file is a user mode.

The obvious rule to draw - that a writer must leave `first..first + 29` alone
because they are the firmware's - is **wrong in one direction and was checked
before being written**. Across all 338 `0x7E` instructions in the config, 78
distinct operands spanning 0 to 113, **exactly one lands inside the block**:

```
action_list at 0x00EBA9, 13 bytes
  0x07  65533
  0x75  18020
  0x7E  25        <- mode 25, "Battery Low"
  0x1F  60161
```

So the config does point into the firmware block, once, at the screen a config
would want: the remote's own low battery display. The accurate rule is that these
thirty modes are **shared, not reserved** - a generated config may branch to one
and must not renumber or overwrite one. Both writers here allocate at mode 114,
above the whole table, so neither is affected.

The block's position is stated by the file, not by the architecture: `first` is 4
on protocol 8, 11 on protocol 9 and 14 on protocol 14, so **the config declares
which of its own modes the firmware owns**.

`codex-work/tooling/trace_event_map.py`. Every quoted instruction word was
re-read out of `mcu.bin` here. Negative controls: the same enumeration returns
**1** for a routine with one `RCALL` and **0** for 5r's unreachable
configuration-bit routine.

## 5y. Port E bit 2 is the config flash's chip select, and the 525 has one analog input - MEASURED

Two facts about the 525's pins, both from the image and neither previously here.

### Every section read begins by pulling one pin low

`BCF LATE, 2` and `BSF LATE, 2` occur 46 and 47 times in the image. **Twelve of
the sixteen seeks to `0x066A8` are preceded by the assert at exactly eight
bytes**, and the eight bytes are the same four instructions every time:

```
07AF0:  BCF LATE, 2        pull RE2 low
07AF2:  MOVLB 0x1
07AF4:  MOVLW 0x0E         the section number, a literal
07AF6:  MOVWF 0x58         into 0x158
07AF8:  CALL 0x066A8       seek to section N
```

`0x158` is where the seek routine reads N, which this document already said from
the other end. So `RE2` is asserted before the transfer and released after: it is
the **chip select of the serial flash the configuration lives in**, and the one
extra release is the idle-high initialisation with no matching assert.

Of the four seeks not preceded by an assert, section 15's is the one that can be
shown positively: it is called at `0x0560E` from inside the ADC routine's own
bracket, `BCF` at `0x05600` and `BSF` at `0x0563E`. The other three are read the
same way, as calls made inside a caller's open bracket, but that is inference
rather than a trace.

The other two bits of the port are barely used and neither brackets a seek: bit 0
and bit 1 each get two asserts and two releases, against bit 2's forty-six and
forty-seven.

### There is exactly one analog input, and it is AN0

5m read the ADC chain and stopped at "what the voltage on AN0 actually is, is not
established". That is still true. What is settled now is that **there is nothing
else to confuse it with**. Seventeen instructions in the whole image touch an ADC
register, and three independent statements agree:

* **`ADCON0`'s channel select bits are never written.** The register is only ever
  `CLRF`ed and then bit 0 set, so `CHS3:CHS0` stay zero. No instruction anywhere
  selects a channel other than 0.
* **`ADCON1` is loaded with `0x0E`** at all three initialisation sites,
  `0x00E6A`, `0x055B4` and `0x07ECC`, which makes AN0 analog and every other pin
  digital. At `0x055A6` it is set to `0x0F`, all digital, so that `PORTA` bit 0
  can be read as a **digital level**, and then restored - the same pin serves
  both ways.
* **`TRISA` is loaded with `0x01`.** RA0 is the only input on port A at all; RA1
  to RA7 are outputs, and `PORTA` is preloaded `0x2C`.

`ADCON2` is `0x86`: right justified, zero `TAD` acquisition, conversion clock
Fosc/64.

The consequence for 5x: three of the thirty firmware screens are `Battery ADC`,
`LightSense ADC` and `Revision ADC` calibration failures, and **this remote has
one analog channel**. So the thirty are the firmware family's fixed vocabulary
and not a list of what this model can raise, which is the same conclusion the
four-of-thirty raise-site count reached from the other side.

## 5z. The 525's keypad is eight by seven, and its own key table says so - MEASURED

5n has the arch 8 keypad, 4 by 16, because @kkong42 put a multimeter on an 880
and an 885 board. Nobody has opened a 525. This is the same answer for arch 9
from the firmware, checked against the config.

### How the firmware finds a key

One sense line, `PORTB` bit 7, and **two binary searches over the same eight
lines**:

| | routine | masks on `LATD` | contributes |
|---|---|---|---|
| pass 1 | `0x06FA4` | active high: `01 02 03 04 08 0C 0F 10 20 30 40 C0 F0` | **1 to 7** |
| pass 2 | `0x0701C` | active low: `FE FD FC FB F7 F3 F0 EF DF CF BF 7F` | **`0x00` to `0x38` in eights** |

Each probe is `RCALL`, then `BTFSC PORTB, 7`, then branch: thirteen writes and
thirteen tests resolve one line in three or four steps. The two results are added
at `0x070BE`, so a scan code is `A * 8 + B` with `A` in 0..7 and `B` in **1..7,
never 0**.

The two passes do not drive the port the same way. Pass 1's helper `0x0715A` is
`MOVWF LATD` and a delay. Pass 2's `0x07156` is `MOVWF LATD`, then `BCF LATE, 0`
and `BSF LATE, 0`, then `SETF LATD`, then the delay - the mask is written, `RE0`
is pulsed, and the port returns to idle. **The natural reading is that one axis
is driven straight off port D and the other through an external latch that `RE0`
strobes off the same eight lines**, which is inference from the instruction
order and is not a measurement.

`RB7` is also the wake: `0x070C4` scans, calls `0x0716C` to clear `LATD`, and
then clears `INTCON` bit 0, the port B interrupt-on-change flag.

### The config agrees, and it agrees exactly

The 525's key table holds 51 codes at `0x0000FB`. **Fifty carry bit 7**; the one
that does not is `0x06`. Strip the bit and take the firmware's own split,
`A = code >> 3` and `B = code & 7`:

```
    0 1 2 3 4 5 6 7
A=0 . # # # # # # #
A=1 . # # # # # # #
A=2 . # # # # # # #
A=3 . # # # # # # #
A=4 . # # # # # # #
A=5 . # # # # # # #
A=6 . # # # # # # #
A=7 . # . . . . . .
```

`B` is never 0, which is exactly what a pass returning 1 to 7 forces. Seven of
the eight rows are **completely full**, and 50 of 56 crosspoints are used. The
grid was not fitted: the split came out of the instructions before the table was
looked at, and the empty column is the prediction it made.

Bit 7 is therefore a flag on the code and not part of the coordinate, and what it
means is not established here.

### What this is worth

A keypad's geometry is readable from a config alone, without opening the remote.
That is a second route to 5n's 4 by 16 and the only route available for every
model nobody has a board for. Whether the split is per architecture is not
answered by one file; see `codex-work/tooling/KEYPAD-GEOMETRY-REPORT.md`.

## 5r. The 525 can rewrite its own firmware, and its configuration bits - MEASURED

`tools/hid_query.py` refuses `0x30 WRITE_FLASH`, `0x40 WRITE_FLASH_DATA`,
`0xA0 WRITE_MISC` and `0xD0 ERASE_FLASH`, and has since the first read. That was
written as caution. It is not caution.

[@dannybloe](https://github.com/dannybloe) read the write path out of the arch 12
and arch 14 firmware and found that one command serves two destinations, chosen
by a selector the address validator sets: external storage or **internal program
flash**, the part's own self programming sequence. Arch 9 was outside his scope,
and the 525's image is the only arch-9 one anyone has.

It has the same capability, and one more. Searching the 32 KiB image for the
PIC18 unlock - `0x55` and `0xAA` into `EECON2`, then `WR` - finds three call
sites of one shared five-instruction routine at `0x00056`, `0x00AC4` and
`0x07D5C`. What matters is which `EECON1` is standing when it is called:

| set at | `EECON1` | bits | what commits |
|---|---|---|---|
| `0x000C2` | `0x84` | EEPGD, WREN | **program flash write** |
| `0x000F4` | `0x94` | EEPGD, FREE, WREN | **program flash erase** |
| `0x00172` | `0xC4` | EEPGD, CFGS, WREN | **configuration bits** - see below |
| `0x00160` | `0x04` | WREN | on-chip data EEPROM |
| `0x00126` | cleared | - | data EEPROM read |

`TBLWT*` appears three times, at `0x002C2`, `0x00B14` and `0x07DAE`, which is the
only way to load program flash holding registers. The `0x84` path masks an
address with `0xF0` before arming, and each path polls `EECON1` bit 1 for
completion.

### The route to them, traced

They are not scattered helpers. A nine-entry XOR chain at `0x001D0` dispatches on
the **first byte of the USB endpoint buffer** at bank 4 `0x420`:

| command byte | routine | `EECON1` | what it does |
|---:|---|---|---|
| `0x02` | `0x000BC` | `0x84` | **program flash write** |
| `0x03` | `0x000E4` | `0x94` | **program flash erase** |
| `0x04` | `0x00126` | cleared | data EEPROM read |
| `0x05` | `0x00148` | `0x04` | data EEPROM write |
| `0x00`, `0x01`, `0x06` | `0x00062`, `0x0006C` | none | no flash access |
| `0x07`, `0xFF` | - | none | no call |

That block is surrounded by USB buffer descriptor handling - bank 4, the `UOWN`
and `DTS` bits, a 64-byte count - and it is entered from the main firmware by a
`GOTO` at `0x00C60`, one way, never returning. What decides that jump is a byte
of the USB endpoint buffer at `0x412`: non-zero sets the state variable `0x93` to
6 and jumps; zero sets it to 5 and returns.

**So the path from the wire to a program flash erase is unbroken.** It is not a
bootloader behind a physical gate. What is not traced is what the outer protocol
requires to reach `0x00C42` in the first place, and what value that byte must
hold - so the conclusion is that the route exists, not that it is easy.

**The configuration-bit path is the one worth naming separately.** Program flash
can be rewritten; configuration bits decide whether the part can be programmed at
all. A remote that loses its firmware is a recovery problem. A remote whose
configuration word is wrong can be neither read nor reprogrammed by anything this
project can build.

### The configuration-bit routine has no caller

This corrects the first version of this subsection, which listed the `0xC4`
routine among the ones the command table reaches. It is not among them. **No
instruction anywhere in the 32 KiB image references `0x00172`** - not a call, not
a jump, not a branch. The routine before it ends in a `RETURN` at `0x00170`, so
it is not reached by falling through either.

The capability is compiled in and, as this image stands, nothing invokes it. That
is worth stating precisely rather than dropping, because "the part can do it" and
"the firmware does it" are different claims and only the first is true here. A
computed jump could still reach it and a literal scan would not see one.

So the rule stands as written, and now it has a reason rather than an instinct:

> `hid_query.py`'s whitelist is `0x10`, `0x55`, `0xB2`, `0xB3` and nothing else.
> Do not widen it as a side effect of another change.

Two further notes, since this is where a reader will look for them:

- **The 64-byte hazard does not apply to a config write.** Danny's "a transfer
  that ends mid block leaves its tail unprogrammed, with no error" is about the
  *internal* arm - the part self programming. A config goes to external storage,
  a different arm of the same command. The write this project performed on
  2026-08-22 (5o) was never exposed to it.
- That write was done by **concordance**, not by anything here, and concordance
  erases first and verifies after. Its transcript shows two erase steps at
  `0x820000` and `0x830000`, 64 KiB apart, then 92 KiB written and 92 KiB
  verified. A relocated config is longer, and the margin before a third erase
  block is needed is about 36 KiB - but the failure mode is loud rather than
  silent, because the verify pass reads back what was programmed.

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
| `repair_890_dump.py` | remove the duplicated 54-byte blocks a Harmony 890 read leaves behind; refuses unless the result reproduces the stated end address and the stored checksum |
| `verify_525_semantics.py` | re-assert every claim in 4k and 4l against the sample; `--firmware` pins the handlers too |
| `verify_small_sections.py` | re-assert 4r against every public sample; `--negative` breaks each checked field and demands a failure |
| `verify_arch8_human_oracles.py` | check @kkong42's H880/H885 screen inventories against the public configs; needs `--explorations` |
| `verify_arch8_standard_bindings.py` | the join that fixes all 55 arch 8 keys; needs `--explorations` and `--manual` |
| `arch8_ir_duration_reader.py` | walk arch 8 infrared by stated addresses only, and prove the walk on carrier frequency and protocol headers |
| `verify_ir_spelling.py` | recompute both 5q tables and require every long run to fit one of three spellings; `--negative` pins the rule and demands refusals |
| `render_525_screens.py` | draw all 135 menu pages and the five font sheets as BMPs |
| `analyze_525_ir.py` | expand the class 5 IR dictionaries, decode NEC, correlate with mode bindings |
| `class5_ir_encoder.py` | build and decode a relocatable arch-9 literal class-5 record |
| `verify_525_class5_encoder.py` | repack the six learned X96 signals and require an exact decode; `--bundle` writes golden vectors |
| `clone_525_device.py` | offline fifth-device proof, optionally placing one new IR record; never hardware-safe |
| `roundtrip.py --resize` | relocate a config and require the symbolic shape, all 135 screens and all 200 expanded IR records to come back exact |
| `pointer_census.py` | expose the reader-backed arch-9 address fields as one closed inventory |
| `relocate_arch9.py` | insert bytes in memory and rewrite only the complete pointer census; unsupported families are refused by name |
| `verify_arch9_relocation.py` | check exact relocation diffs and meaning, omit every holder class in turn and require failure, and refuse two insertion points that break no pointer and still cost structure |
| `edit_525_label.py` | bounded proof: change one same-length screen label and check nothing else moved |
| `relocate_525_label.py` | bounded proof: append a *longer* label and retarget every user of the old one. Superseded, see below |
| `relocate_525_label_in_place.py` | the same longer label without appending: the file keeps its length and its picture bank |

### Live communication with the remote (read-only)

| script | what it does |
|---|---|
| `hid_listen.py` | dump HID capabilities; with an argument, listen for input reports |
| `hid_query.py` | **protocol core** - GET_VERSION, ReadMisc; command whitelist |
| `poll_state.py` | poll state variables, report changes |
| `capture_keys.py` | sample state during key presses, group by pause |
| `probe_kinds.py` | try every address space (EEPROM/STATE/RAM/REGISTER) |

## 5q. How a duration block is spelt, on two architectures - MEASURED

Both rules below are [@dannybloe](https://github.com/dannybloe)'s, measured by
him against configurations Logitech's own generator produced, in
[harmony-explorations](https://github.com/dannybloe/harmony-explorations)
commit `229b937`. What this section adds is the same two questions asked of the
samples in this repository, on arch 8 and arch 9, to find out whether they
belong to the generator or to one architecture.

### Rule 1: a once block leads with silence, a held block never does

| | once blocks | leading with silence | held blocks leading with silence |
|---|---:|---:|---:|
| arch 9, the 525 | 261 | 261 | 0 of 153 |
| arch 8, H880 records with two or more blocks | 163 | 163 | 0 |

On arch 8 every record carrying more than one block puts the silence in slot 0
and in no later slot, 163 of 163. So the rule is the generator's rather than one
architecture's.

One difference does not carry across. Arch 9 leads with **two** values, 50,000 us
in 153 blocks and 500,000 us in 108. Arch 8, in these samples, always leads with
exactly 50,000. Why arch 9 has the second value is unknown.

### Rule 2: it holds once a trailing `1` is read as a sentinel

His rule spells a duration too long for one word as maximal words with the
remainder balanced across the final two, smaller half first, so no word falls
below half the maximum. Taken literally it does not describe these files:

Every **long run** - one whose duration exceeds what a single word can hold -
turns out to be one of three spellings, and the argument is not that each is
common but that **nothing falls outside them**:

| | long runs | the rule literally | + a trailing `1` | a merged space | left over |
|---|---:|---:|---:|---:|---:|
| arch 9, the 525 | 909 | 495 | 314 | 100 | **0** |
| arch 8, 13 samples | 14,666 | 8,023 | 6,423 | 220 | **0** |

The third column is not a third spelling. See below.

```
35,101   stored  17550, 17550, 1               the rule  17550, 17551
96,078   stored  32767, 32767, 30543, 1        the rule  32767, 32767, 30544
69,090   stored  446, 32767, 17938, 17938, 1   the rule  32767, 18161, 18162
```

So the rule holds on both architectures **once the trailing `1` is treated as a
sentinel rather than as a duration**. A one microsecond pulse is not something an
infrared emitter produces, which agrees, but the argument is the count.

### The leading word is not a spelling. It is this reader gluing two durations together

This subsection has been wrong twice about the same 100 runs, and the second
error is the more instructive one.

The first version called them an arch 9 residue and left them unexplained. The
second found the same shape on **arch 8** - 220 runs across the four `Update`
samples, 216 of them 446 us and four of them 310 us - and concluded that a
leading word set aside was a third spelling the generator uses.

It is not a spelling at all. A long duration is stored as consecutive words of
one polarity, and so are two short durations that happen to be adjacent, and
**nothing in the words distinguishes those two cases**. This reader coalesces
same-polarity words, so where the generator wrote an ordinary bit space followed
by the inter-frame gap, the reader saw one impossible run and a rule was invented
to explain it.

What settles it is that the glued-on value is an ordinary duration of that
protocol, and says so in its own block:

| | value | carrier | occurrences as a standalone duration |
|---|---:|---:|---|
| arch 9 and arch 8 | 446 us | 36,200 Hz | the commonest short space there is |
| arch 8, four records | 310 us | 36,001 Hz | likewise |

Every one of the 320 is a silence, never a mark; every one sits deep in its
block, where a frame ends rather than where one begins; the value tracks the
carrier frequency rather than the architecture; and **320 of 320 occur as a
standalone duration of the same polarity in the same block.** The verifier makes
that a condition rather than a remark: a merged space whose value does not stand
alone somewhere in its own block is reported as left over.

So rule 2 needs the sentinel and nothing else. With the sentinel, and without
gluing across a duration boundary, it describes every long run on both
architectures - 909 of 909 and 14,666 of 14,666.

> The lesson is the project's usual one wearing a new coat. A reader that merges
> two things the format keeps apart will make you invent a rule to explain the
> merge, and the invented rule will fit, because it was fitted.

A fourth class was tried and rejected - a leading word with no sentinel. It never
fired on either architecture, and leaving it in would have accepted almost any
two-word run. A class that explains everything explains nothing.

**The arch 8 counts in the earlier version could not be reproduced.** They read
`2,179 / 1,202 / 977` over "H880 and H885"; no counting unit tried here produces
them, and the measurement tool that was used at the time does not either. The
numbers above come from `tools/verify_ir_spelling.py`, which anyone can run.

### How this was measured

The arch 9 side uses this repository's own class 5 readers. The arch 8 side
needed a duration reader that did not exist, so one was written and proved
before use rather than after. **It is in the repository now**, as
`tools/arch8_ir_duration_reader.py`, and `tools/verify_ir_spelling.py`
recomputes both tables above from it. The proof it carries: the carrier frequencies it
derives have a median of 38,001 Hz on both samples, and the leading pairs it
produces match real protocol headers, Kaseikyo at 3,500/1,700 us and NEC at
9,000/4,500. A reader returning plausible numbers proves nothing; one that lands
on 38 kHz and on two named protocols by two separate routes is worth measuring
with.

Protocols 10 and 14 are not covered. The 890 samples are a different container
and the reader refuses them, which is why the table above names arch 8 and arch 9
and claims nothing wider.

**Safety:** `hid_query.py` carries a whitelist (`ALLOWED_FIRST_BYTE`) and hard
refuses `0x30 WRITE_FLASH`, `0x40 WRITE_FLASH_DATA`, `0xA0 WRITE_MISC` and
`0xD0 ERASE_FLASH`. Every other script goes through it. `hid_listen.py` opens the
device with `GENERIC_READ` only. Nothing in this repository writes to a remote.
