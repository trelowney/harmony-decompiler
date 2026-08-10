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
  deliberately unusual button layout, so codes can be matched against intent.
  [@kkong42](https://github.com/kkong42) supplied close to this on 10 August 2026
  for a Harmony 885 in daily use: the config, plus a written list of every device,
  every activity and every custom button label including the blank positions. It
  is arch 8 rather than arch 9, but it is the first sample in this repository
  where somebody can say what the remote is supposed to be doing

The assignment appears to be **shared across models**: arch 8 (720/785/88x) uses
the same code groups in the same order, 41 of the 525's 51 codes overlapping
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
config and 3,311 in an arch 8 sample**, in a shared vocabulary. Six of them are
now identified, four of those from the firmware and one from the person who
designed it:

| opcode | meaning | how |
|---|---|---|
| `0x7D` | send an IR command from a group | corpus closure against the IR groups |
| `0x7C` | `QueueDelay`, per device | [@glenharris](https://github.com/glenharris), [discussion #14](https://github.com/trelowney/harmony-decompiler/discussions/14) |
| `0x75` | sound a tone | handler `0x01DC4`, a counted GPIO toggle |
| `0x80 \| n` | write state variable `n` | dispatcher `0x01C9A`, closed against the name table |
| `0x7E` | select a mode | corpus, and the fifth-device experiment |
| `0x7F` | run another list | references another list every time, both architectures |

What the rest do is still open:

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

> **One caution about how to count them.** An earlier revision of this file
> talked about 59 second-level sub-opcodes, from 24 comparisons on the low byte
> and 35 on the high byte in the dispatcher.
> [@glenharris](https://github.com/glenharris) points out that this is not how
> the machine is built: an instruction is 24 bits wide, and its fixed pattern
> can run through bytes 1 and 2, leaving anywhere from 0 to 16 bits of data. An
> instruction with two data bits has a 22-bit fixed pattern. So those 59
> comparisons are successive tests of one pattern, not 59 operations, and the
> right thing to recover from the firmware is a set of masks and data widths.

## 1b. What is inside a block - ANSWERED, and the answer retires the question

This section used to describe the largest remaining gap: roughly 9,400 bytes of
payload inside 1,072 blocks, made of "45 distinct symbol values, almost all
below `0x30`", in runs terminated by `00`, with pairs of runs differing in
exactly one symbol.

There are no blocks. Those bytes are screen programs, and those runs are the
menu labels, written as glyph numbers that are local to whichever font opcode 16
last selected. Counted over the 183 glyph strings the current decompiler
recovers: 67 distinct codes, 47 of them below `0x30`, the most common taking
9.0% of all glyphs. Which is the same population the old note was describing.

The pairs differing in one symbol were labels differing by one letter.

**The negative result in this section was also wrong, and instructively so.** It
said the payloads are "not obviously text", because a frequency count did not
look like English: the most common symbol took 8.6% where English gives `e`
12.7%. The bytes were text the whole time. The test could not work, for two
reasons that were both knowable at the time. The codes are indices into a font,
so the same letter has a different number in a different font set and the counts
are smeared across five alphabets. And the corpus is menu labels for one
person's four devices, which is not prose and has no reason to follow prose
statistics. A negative result is only as good as the assumption it tests, and
this one silently assumed a single global alphabet.

See [FORMAT.md §4l](FORMAT.md).

## 1c. What is in an arch 8 config

Arch 8 decompiles and round-trips, but under 5% of it is understood, and the
recognisers that fire are the ones shared with arch 9. No bitmaps were found.
Where its screen images live, and what fills the other 95%, is open here.

Two things to do before starting. First, read
[@dannybloe](https://github.com/dannybloe)'s
[harmony-explorations](https://github.com/dannybloe/harmony-explorations), which
covers arch 8 with the same parser as 9, 12 and 14; there is little point
rediscovering it. Second, there are now many more samples than the four in
`samples/arch8/`: [@kkong42](https://github.com/kkong42) posted eleven 880, 885
and 890 configs in this repository's issues on 10 August 2026, and for one
885 also wrote down what the remote actually shows, device by device and button
by button. That last one is ground truth of a kind this project has never had.

`roundtrip.py --all` covers whatever is in `samples/`, so this is a
self-checking place to start: add a recogniser, see whether the round trip still
passes.

## 2. The bytecode instruction set (section 8) - ANSWERED as a structure

Section 8 closes to the byte on arch 9: a leading action list of 34 bytes, 11
instructions, then the packed run of all 135 mode-page binding lists, 1,052
bytes, and nothing else. It is not an array of per-record programs, which is
what an earlier revision of this file guessed from the pointer in what it called
a record trailer. That pointer belonged to the page record behind it.

What the instructions *mean* is question 1a above, and this section no longer
duplicates it. See [FORMAT.md §4k](FORMAT.md).

---

## 3. How IR signals are encoded in flash - ANSWERED for arch 9

Base slot 5 holds one group per device, each group a list of record addresses.
An arch-9 record is class 5: a carrier period and on-time, then up to sixteen
groups of three pointers into bodies, each body an index list into a symbol
table, each symbol a counted run of duration words.

**That layout is [@dannybloe](https://github.com/dannybloe)'s**, from
[harmony-explorations](https://github.com/dannybloe/harmony-explorations), where
it was also checked against firmware. Reproduced and used here with attribution;
see [FORMAT.md §4n](FORMAT.md). On the 525 sample this expands all 200 records,
and the user's learned X96 group reduces to six distinct NEC frames on address
`01 FE`.

What is still open:

- **the other classes.** Class 5 is what this one arch-9 config happens to use
  throughout. Nothing here says what classes 1 to 4 are, and arch 8, 12 and 14
  are untouched.
- **what the three pointer slots per group mean** for a signal captured from a
  real remote. Press, repeat and release is the obvious guess and it is a guess.
  NULL slots are common and significant.
- **whether the dictionary is required.** `tools/class5_ir_encoder.py` packs
  literally, one symbol per stream, which is not what Logitech's compiler
  produces. Whether the firmware minds is untested.

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
  headers are decoded and most of what follows them turns out to be screen
  programs; what is left over is smaller than it was and no longer has a name.
- **Section 17 is solved**: four 96x64 monochrome LCD bitmaps. Rooted screen
  control flow contains 1,114 opcode-3 picture draws; 1,080 immediately follow
  an opcode 22 page selection and 34 do not, and those 34 are all the same
  one-pixel rule under a menu heading.
- **Sections 5, 7, 8 and 11 came off this list.** 5 is the infrared groups, down
  to every pointer inside every record, on
  [@dannybloe](https://github.com/dannybloe)'s published class-5 layout; 8 is a
  leading action list plus every mode page's binding list, closing to the byte;
  11 is the screen-program table; 7 holds the five font sets.
  [FORMAT.md §4k, §4l and §4n](FORMAT.md).

> The counts above are what the decompiler reported when this was written and are
> left as a record of that pass. Reading the screen programs first took the 525
> from 952 to 3,171 pointers. The relocation/render audit later corrected the
> model to **3,609** recomputed pointers and **36.8%** structurally decoded. A
> later independent-reader failure exposed one more opaque pointer graph: the
> class-5 IR records. Typing all four IR groups, 200 record headers, 380 bodies,
> five symbol tables and 43 symbol blocks raises the current resize closure to
> **5,015** recomputed pointers and **76.46%** structurally decoded.
>
> The percentage dipped in the middle of that because 1,080 twelve-byte runs
> formerly claimed as block headers are really `opcode 22 + opcode 3` pairs; the
> stronger reading claims the ten-byte picture draw and leaves the two-byte page
> selection to the screen walker. No known bytes or pointers were discarded. The
> same audit had already **removed 124** references an earlier recogniser
> invented, by matching `16 <u24>` inside an opcode 4 whose y coordinate happened
> to be `0x16`. A recogniser that scans for a shape invents things as readily as
> it misses them, and the round trip cannot tell: a wrong claim about bytes that
> come back unchanged still passes.

Pointers are now symbolic - `region + delta` - so the compiler relinks every one
it knows about when something changes length. The same-size restriction is
therefore lifted for *known* pointers. It is emphatically not lifted for unknown
ones: anything pointer-shaped inside a still-opaque region gets left behind
silently. That is the main risk in the whole approach, and it is not theoretical.
It has now happened twice:

| what was left behind | what it cost | who found it |
|---|---|---|
| the trailer `u16` checksum | every edited config the remote would have refused | [@dannybloe](https://github.com/dannybloe)'s parser |
| body, table and symbol pointers inside the IR records | all 200 infrared commands ruined by any length change | [@dannybloe](https://github.com/dannybloe)'s class-5 reader |

Both times every test in this repository passed. Both times an outside reader of
the same format found it in one attempt. That is worth more than the two fixes:
it says that a round trip against a file's own former self is not an oracle, and
the only cheap substitute is somebody else's parser.

So the remaining question is narrower and more concrete than it was: **do sections
1, 2, 3, 4 or 16 contain pointers in some shape the recogniser does not match?**
Section 13 did, and was found by relaxing the requirement that addresses
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
| Is there a published solution to this format already? | Yes, and it is further along than this one. [@dannybloe](https://github.com/dannybloe)'s [harmony-explorations](https://github.com/dannybloe/harmony-explorations) reads architectures 8, 9, 12 and 14 with one parser, MIT licensed. This row said "No, as of 2026-08" and was out of date within days |
| Where is the text the screen shows? | In the config, as runs of font-local glyph numbers drawn by the screen programs. There is nothing to search for and searching was the wrong idea - [§4l](FORMAT.md) |
| Is the XML `CHECKSUM` the only checksum? | No. There is a second `u16` before the end marker and it is the one the remote checks - [§4m](FORMAT.md) |
| How many devices does the 525 sample have? | Four, not the three its state-variable names imply. The fourth only exists as pixels - [§5i](FORMAT.md) |
| Are record bodies blocks, one per keyboard-matrix row? | No. There are no blocks. Those bytes are screen programs and the payload inside them is menu text - [§4f](FORMAT.md) and question 1b above |
| Does each record end with a trailer pointing at its own bytecode? | No. That was the next page record, read from the wrong offset - [§4g](FORMAT.md) |
| Does `0x7C` carry a quantity rather than a delay? | No. It is `QueueDelay`, per device, from [@glenharris](https://github.com/glenharris) who designed it. The measurements were right and the inference was wrong - [§4k](FORMAT.md) |
| Are there 59 second-level sub-opcodes? | Probably not. An instruction is 24 bits with a fixed pattern that can run through all three bytes, so those comparisons are one pattern being tested, not 59 operations. [@glenharris](https://github.com/glenharris), question 1a above |
| How is infrared stored? | Class 5: carrier, then bodies indexing a symbol table of duration runs. Layout by [@dannybloe](https://github.com/dannybloe) - [§4n](FORMAT.md) |
