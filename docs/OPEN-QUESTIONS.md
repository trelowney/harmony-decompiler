# Open questions

What is *not* known, roughly in order of how much it blocks. If you can answer any
of these - even partially, even with "I checked and it is not X" - please open an
issue or a discussion.

---

## 1. Which matrix position is which physical button?

**This is the blocker.** Everything else can proceed without it; a usable editor
cannot.

Key codes are keyboard-matrix coordinates, `code = 0x80 | (row << 3) | column`,
giving a clean 8 x 7 grid with 50 occupied positions and no collisions
([FORMAT.md §5g](FORMAT.md#5g-key-codes-are-keyboard-matrix-addresses)). The
buttons on the other side of the gap - what Logitech names them, how they are
arranged, and which of them the manual actually counts - are written up in
[BUTTON-LAYOUT.md](BUTTON-LAYOUT.md). What is missing is the map between the two.

Why the obvious routes do not work:

- **Reading it off the config table order** - the order is grouped by matrix row,
  but the columns are permuted differently in each pair of rows. It is Logitech's
  canonical key ordering, not the visual layout.
- **Sniffing key presses over USB** - the remote locks its UI while connected. It
  sends no HID input reports (`NumberInputButtonCaps = 0`), and the only state
  change visible from outside is a backlight boolean, which two *different* keys
  set identically. Three independent attempts, all negative;
  [FORMAT.md §5d](FORMAT.md#5d-identifying-keys-over-usb-is-impossible--closed)
  has the detail so nobody has to repeat them.

What would answer it:

- **original Logitech documentation or source** - the mapping was a table in the
  configuration software once
- **a service manual or schematic** for any remote in this family
- **a photo of a bare PCB** - enough to trace which switch sits on which row/column
- **buzzing out a matrix with a multimeter** - definitive, and only needs doing
  once for one model
- **a config whose key assignment is already known** - e.g. one built with a known,
  deliberately unusual button layout, so codes can be matched against intent

The assignment appears to be **shared across models**: arch 8 (720/785/88x) uses
the same code groups in the same order, 34 codes overlapping with the 525
([§5f](FORMAT.md#5f-key-codes-are-shared-across-architectures)). So solving it for
*one* remote in the family carries most of the way to the others - and arch 8
layouts are far better documented publicly. A 720 or an 880 is probably an easier
target than a 525.

---

## 1a. What the instruction opcodes mean

**The best question in this file to work on**, on the grounds that it can be
answered from data and firmware we already have, by anyone, with no hardware.

The question this replaces was "what does a key table's `target` mean", and it is
answered: it indexes an array of **action lists** in section 10, each of them
`<u8 count> <u16 operand> <u8 opcode>[count]`, the same instruction section 8 is
built from. A key binding runs a list. See
[FORMAT.md §4i](FORMAT.md#4i-section-10-indexes-action-lists--solved).

So the question moves down a level. There are **1,043 instructions in the 525
config and 3,311 in an arch 8 sample**, in a shared vocabulary, and nothing is
known about what any opcode does:

| opcode | arch 9 | arch 8 |
|---|---|---|
| `0x7F` | 235 | 828 |
| `0x7E` | 134 | 521 |
| `0x7D` | 200 | 239 |
| `0x7C` | 203 | 242 |
| `0x1F` | 57 | 252 |
| `0x07` | 20 | 640 |

Four of them carry three quarters of arch 9, so they will be the common
operations: send an IR command, push or pop a menu, set a state variable, run
another list.

Some of it is already constrained, and `tools/actions.py` prints the working.
`0x7F` references another list, every time, in both architectures. `0x7D` always
opens a list with an operand unique to it and `0x7C` always closes one with a
vocabulary of nine values. `0x7E` stays inside the record count on arch 9.
`0x07`, `0x0F`, `0x1F` and `0x3F` carry a negative signed number in every single
instance. The full argument is in
[FORMAT.md §4i](FORMAT.md#4i-section-10-indexes-action-lists--solved).

So the shape is not a mystery, only the meaning. Three routes, in the order they
are likely to pay:

- **The firmware.** These opcodes are dispatched by a switch in the config
  interpreter, which is in a PIC18 image any owner can dump with
  `concordance --dump-firmware`. A disassembly settles it rather than supporting
  a guess. See
  [discussion #7](https://github.com/trelowney/harmony-decompiler/discussions/7).
- **Correlation.** List 79 is what every key runs in the catch-all table, so it
  is a good candidate for "do nothing". Operands can be checked against things
  whose ranges are already known: state variable addresses from the name table,
  the 114 records in the low region, menu indices.
- **Observation.** Point a key at a different list, load it, press the key, watch
  what the remote does. That needs a remote somebody is willing to write to.

Doing this for even two or three opcodes would turn the decompiler's output from
a structure into something a person can read.

## 1b. What is inside a block

The largest remaining gap. Blocks are decoded down to their headers - twelve
bytes, one per matrix row, each selecting an LCD bitmap - and terminated by
`0x17`. What sits in between is not understood. Roughly 9,400 bytes across 1,072
blocks, though 364 of those are empty.

What is known about the payloads:

- 45 distinct symbol values, almost all below 0x30
- strings of them are terminated by `00`
- a recurring shape `05 <n> 16 0F ...` where only the second position varies
- pairs of strings differing in exactly one symbol and identical otherwise

**A tested hypothesis that did not hold: they are not obviously text.** If these
were names in a substitution alphabet the symbol frequencies should look like a
natural language, and they do not - the most common symbol takes 8.6% against the
12.7% English gives to `e`, and the distribution is much flatter than prose. 45
symbols is about right for uppercase plus digits, so it is not ruled out, but the
frequency evidence does not support it and nothing here should be written up as if
it did.

The pairs differing in one symbol are the most promising thread: whatever varies
between two otherwise identical blocks is likely to be the thing a block is
*about*. Two configs from the same remote differing by one deliberate change would
settle it quickly, which is another reason samples matter.

## 1c. What is in an arch 8 config

Arch 8 decompiles and round-trips, but only about 2% of it is understood, and
the recognisers that fire are the ones shared with arch 9. It has no block
headers, and no bitmaps were found. Where its screen images live, and what fills
the other 98%, is open.

The four samples are in `samples/arch8/` and `roundtrip.py --all` covers them, so
this is a self-checking place to start: add a recogniser, see whether the round
trip still passes.

## 2. The bytecode instruction set (section 8)

Section 8 fits the shape `<u16 operand> <u8 opcode>`, and every one of the 114
records carries a pointer into it, which suggests each record references its own
bytecode. The original developer described the remote as *"a Von-Neumann style
computing device with a 16 bit instruction"*, which matches independently.

Unknown: what the opcodes mean. Observed so far are `0x7E, 0x7F, 0x9E, 0x9F, 0xA6,
0xA7`. Currently a hypothesis, not a verified finding -
[FORMAT.md §4c](FORMAT.md#4c-section-8-looks-like-bytecode--hypothesis).

Note this does **not** block the round-trip work: section 8 can travel as an
opaque blob for as long as it needs to.

---

## 3. How IR signals are encoded in flash

The 114-record array in the low region is almost certainly the IR codes - right
size, right count, and each record carries a bytecode pointer. The record header
and trailer are decoded
([§4d](FORMAT.md#4d-the-114-record-array--header-solved)); the payload is not.

What is known is only the *wire* format used to send IR data to the (now dead)
Logitech server, from `libconcord/web.cpp:252`: `F<carrier>P<mark>S<space>...`
in microseconds. Whether flash uses anything resembling that is unknown.

Anyone who has matched a known IR command - a specific button on a specific
device, ideally captured with a receiver - against bytes in a config would move
this a long way.

---

## 4. The unidentified sections

**Partly answered.** The priority here was never "understand everything" but the
narrower question of **which sections contain pointers**, since a section whose
pointers are decoded can keep everything else opaque and still survive a change in
length. Running the decompiler settles it:

- **Pointer tables, now decoded and recomputed on compile:** sections 5, 6, 7, 9,
  10, 11, 12, 13, 14, 15 - 685 addresses in total. Section 10 turns out to be
  nothing
  *but* a pointer array, 487 entries filling all 1,463 bytes. The 114 record
  headers add another 249, for 952 recomputed pointers in total.
- **Decoded structures:** section 0 is the name table; the four key tables sit in
  the region below 0xF35B.
- **Still entirely opaque:** sections **1, 2, 3, 4, 16**, plus the bulk of
  6, 9 and 14 that follows their pointer prefixes. In the record array the
  headers, trailers and block headers are decoded; what remains is the payload
  inside each block.
- **Section 17 is solved**: four 96x64 monochrome LCD bitmaps, which is what all
  1072 block headers point at. So a block header selects a screen layout. What
  the rest of a block says is still open.
- **Sections 7, 8 and 11 came off this list.** 8 is a leading action list plus
  every mode page's binding list, closing to the byte; 11 is the screen-program
  table; 7 holds the five font sets. [FORMAT.md §4k and §4l](FORMAT.md).

> The counts above are what the decompiler reported when this was written and are
> left as a record of that pass. Reading the screen programs took the 525 from 952
> to **3,171** recomputed pointers and from 27% to **41.5%** decoded, and it also
> **removed 124** references the old recogniser had invented: it matched
> `16 <u24>` inside an opcode 4 whose y coordinate happened to be `0x16`. Which is
> the paragraph below arriving from the other direction. A recogniser that scans
> for a shape invents things as readily as it misses them, and the round trip
> cannot tell: a wrong claim about bytes that come back unchanged still passes.

Pointers are now symbolic - `region + delta` - so the compiler relinks every one
it knows about when something changes length. Digging into the record bodies
turned up 124 more that had been sitting in hex the decompiler was copying
verbatim, which is a fair warning about how many might still be hidden. The same-size restriction is
therefore lifted for *known* pointers. It is emphatically not lifted for unknown
ones: anything pointer-shaped inside a still-opaque region gets left behind
silently. That is now the main risk in the whole approach.

So the remaining question is narrower and more concrete than it was: **do sections
1, 2, 3, 4, 8 or 17 contain pointers in some shape the recogniser does not
match?** Section 13 did, and was found by relaxing the requirement that addresses
ascend - its last entry jumps backwards.

There is now a specific reason to think the answer is yes. The original developer,
asked about that first pointer table, says it points at per-subsystem data, that
each subsystem has its own format, and that some of them hold *"nested data
structures or structures containing **relative** pointers to other locations
within the config"*
([discussion #1](https://github.com/trelowney/harmony-decompiler/discussions/1)).

Everything the recogniser looks for is a 24-bit **absolute** address offset from
`config_base`, and it is deliberately strict about it. A relative offset - from
the start of its own structure, or of its section - would be invisible to it, and
would also be invisible in the round-trip test, because a pointer that is never
recognised is copied as hex and passes through unchanged. That is precisely the
failure mode that leaves an edited config quietly broken. If they do not, then length changes become possible as soon as the
114-record array is understood. If they do, that shape needs finding. Section 4
(2,551 B of 3-byte records) and section 17 (3,096 B) are the two worth looking at
first, on size alone.

Sizes and offsets are tabulated in
[FORMAT.md §3](FORMAT.md#3-sections-18-entries-contiguous-no-gaps); the pointer
breakdown is in [§4b](FORMAT.md).

---

## 5. `u32` at offset 0x08

Reads `0x00001400` (5120) in the 525 config and `0x00001500` in the arch 8
samples. It tracks the `SKIN` value in the XML header (22 = 0x16 versus 21 = 0x15)
closely enough to be suspicious, but not exactly. Small, self-contained, probably
solvable by anyone with three configs from different models.

---

## 6. Generating a config from scratch

The long-term goal, and the original developer's assessment of it was *"very
hard"*: the config is a compiled program for a Von-Neumann machine, not a data
structure that can simply be filled in.

The round-trip approach exists precisely to avoid needing this on day one. Modify
an existing config, keep the size fixed, prove correctness by byte identity, and
expand outward from there.

---

## Questions that have been answered

Kept here so nobody spends an evening on them twice.

| question | answer |
|---|---|
| Can key presses be read over USB? | No. Three approaches, all fail - [§5d](FORMAT.md#5d-identifying-keys-over-usb-is-impossible--closed) |
| Can configs be diffed to isolate a change? | No. A small logical change moves 73-84% of bytes - [§5](FORMAT.md#negative-result-diffing-samples-does-not-work) |
| Is `config_base` the same across architectures? | Yes, `0x20000` on both arch 8 and arch 9 |
| Are pointers 32-bit? | No, 24-bit little-endian |
| Does the remote need a libusb/Zadig driver swap? | Not for the 525 - it runs on the native HID stack. That applies to the 900/1000 |
| Does EEPROM/RAM/REGISTER hold anything readable? | No, only `kind=01` STATE returns data, and only in word mode |
| Is there a published solution to this format already? | No, as of 2026-08 |
| Where is the text the screen shows? | In the config, as runs of font-local glyph numbers drawn by the screen programs. There is nothing to search for and searching was the wrong idea - [§4l](FORMAT.md) |
| Is the XML `CHECKSUM` the only checksum? | No. There is a second `u16` before the end marker and it is the one the remote checks - [§4m](FORMAT.md) |
| How many devices does the 525 sample have? | Four, not the three its state-variable names imply. The fourth only exists as pixels - [§5i](FORMAT.md) |
