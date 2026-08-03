# Open questions

What is *not* known, roughly in order of how much it blocks. If you can answer any
of these — even partially, even with "I checked and it is not X" — please open an
issue or a discussion.

---

## 1. Which matrix position is which physical button?

**This is the blocker.** Everything else can proceed without it; a usable editor
cannot.

Key codes are keyboard-matrix coordinates, `code = 0x80 | (row << 3) | column`,
giving a clean 8 × 7 grid with 50 occupied positions and no collisions
([FORMAT.md §5g](FORMAT.md#5g-key-codes-are-keyboard-matrix-addresses)). What is
missing is the map from a grid position to the button a human presses.

Why the obvious routes do not work:

- **Reading it off the config table order** — the order is grouped by matrix row,
  but the columns are permuted differently in each pair of rows. It is Logitech's
  canonical key ordering, not the visual layout.
- **Sniffing key presses over USB** — the remote locks its UI while connected. It
  sends no HID input reports (`NumberInputButtonCaps = 0`), and the only state
  change visible from outside is a backlight boolean, which two *different* keys
  set identically. Three independent attempts, all negative;
  [FORMAT.md §5d](FORMAT.md#5d-identifying-keys-over-usb-is-impossible--closed)
  has the detail so nobody has to repeat them.

What would answer it:

- **original Logitech documentation or source** — the mapping was a table in the
  configuration software once
- **a service manual or schematic** for any remote in this family
- **a photo of a bare PCB** — enough to trace which switch sits on which row/column
- **buzzing out a matrix with a multimeter** — definitive, and only needs doing
  once for one model
- **a config whose key assignment is already known** — e.g. one built with a known,
  deliberately unusual button layout, so codes can be matched against intent

The assignment appears to be **shared across models**: arch 8 (720/785/88x) uses
the same code groups in the same order, 34 codes overlapping with the 525
([§5f](FORMAT.md#5f-key-codes-are-shared-across-architectures)). So solving it for
*one* remote in the family carries most of the way to the others — and arch 8
layouts are far better documented publicly. A 720 or an 880 is probably an easier
target than a 525.

---

## 2. The bytecode instruction set (section 8)

Section 8 fits the shape `<u16 operand> <u8 opcode>`, and every one of the 114
records carries a pointer into it, which suggests each record references its own
bytecode. The original developer described the remote as *"a Von-Neumann style
computing device with a 16 bit instruction"*, which matches independently.

Unknown: what the opcodes mean. Observed so far are `0x7E, 0x7F, 0x9E, 0x9F, 0xA6,
0xA7`. Currently a hypothesis, not a verified finding —
[FORMAT.md §4c](FORMAT.md#4c-section-8-looks-like-bytecode--hypothesis).

Note this does **not** block the round-trip work: section 8 can travel as an
opaque blob for as long as it needs to.

---

## 3. How IR signals are encoded in flash

The 114-record array in the low region is almost certainly the IR codes — right
size, right count, and each record carries a bytecode pointer. The record header
and trailer are decoded
([§4d](FORMAT.md#4d-the-114-record-array--header-solved)); the payload is not.

What is known is only the *wire* format used to send IR data to the (now dead)
Logitech server, from `libconcord/web.cpp:252`: `F<carrier>P<mark>S<space>…`
in microseconds. Whether flash uses anything resembling that is unknown.

Anyone who has matched a known IR command — a specific button on a specific
device, ideally captured with a receiver — against bytes in a config would move
this a long way.

---

## 4. The unidentified sections

Of 18 sections, 0, 6 and 8 are understood to varying degrees. Sections 1–5, 7 and
9–17 are not. Sizes and offsets are tabulated in
[FORMAT.md §3](FORMAT.md#3-sections-18-entries-contiguous-no-gaps).

For the round-trip compiler the priority is narrower than "understand everything":
**which sections contain pointers**. A section whose pointers are decoded can have
everything else in it stay opaque and still survive a size change. Sections 5, 11,
12 and 15 are already known to use the `<u8 count> <u24 address>[count]` shape.

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
| Can key presses be read over USB? | No. Three approaches, all fail — [§5d](FORMAT.md#5d-identifying-keys-over-usb-is-impossible--closed) |
| Can configs be diffed to isolate a change? | No. A small logical change moves 73–84% of bytes — [§5](FORMAT.md#negative-result-diffing-samples-does-not-work) |
| Is `config_base` the same across architectures? | Yes, `0x20000` on both arch 8 and arch 9 |
| Are pointers 32-bit? | No, 24-bit little-endian |
| Does the remote need a libusb/Zadig driver swap? | Not for the 525 — it runs on the native HID stack. That applies to the 900/1000 |
| Does EEPROM/RAM/REGISTER hold anything readable? | No, only `kind=01` STATE returns data, and only in word mode |
| Is there a published solution to this format already? | No, as of 2026-08 |
