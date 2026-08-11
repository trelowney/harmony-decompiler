# harmony-decompiler

Reverse engineering the **Logitech Harmony configuration binary**, with the goal
of decompiling a config into a readable text format and compiling it back -
byte for byte.

The service that configured these remotes is gone: `members.harmonyremote.com`
now serves a discontinuation notice, and the models Logitech named in it are the
older EasyZapper platform this repository is about. A config that is already on
such a remote can still be read off it, but nobody can generate a new one. This
repository is an attempt to change that.

That sentence used to say "Logitech's servers are gone", flatly, and it was too
broad. [@dannybloe](https://github.com/dannybloe) measured it on 7 August 2026:
the later **MyHarmony** service does still answer, and it still compiles configs
for the remotes it serves. It does not serve these.

> **Status: the round trip works.** A 525 config decompiles to JSON and compiles
> back byte-identical, and a button can be remapped through it - changing exactly
> one byte of the blob, with both of the file's checksums recomputed. About 76% of
> the file is decoded so far and the rest passes through as opaque blobs, but that
> part includes **5,015 pointers**, which the compiler recomputes rather than
> copies.
>
> **The menus render.** All 135 screens of the 525 sample draw as bitmaps offline,
> and a device label on them can be edited and checked without going near a
> remote.
>
> **Length-changing edits work too**, as of the pointers becoming symbolic:
> lengthen a name and every section after it shifts, every known pointer relinks,
> the file comes back with the same structure, all 135 screens still render
> pixel-identical and all 200 infrared commands still expand to the same
> timings. Those last two clauses exist because the pointer check on its own
> once passed a file in which every IR command was ruined. See the caveat under
> *Editing*.
> See [`docs/FORMAT.md`](docs/FORMAT.md) for what is known and
> [`docs/OPEN-QUESTIONS.md`](docs/OPEN-QUESTIONS.md) for what is not.

```sh
cd tools
python roundtrip.py                       # decompile, recompile, compare bytes
python roundtrip.py --resize              # lengthen a name, check it relinks
python decompile.py --summary             # what is decoded and what is not
python decompile.py config.EZHex out.json
python compile.py out.json new.EZHex --against config.EZHex
python verify_525_semantics.py                # re-check the 525 evidence
python render_525_screens.py --out rendered   # draw all 135 menus as BMPs
```

## Scope

This repository is about **the config format and the toolchain around it**.
Talking to the remote over USB is already solved by
[concordance / libconcord](https://github.com/jaymzh/concordance) - please use
that for transfers. Keeping the two separate was
[suggested in the thread](https://github.com/jaymzh/concordance/issues/66) this
work grew out of, on the grounds that the data format and the USB transport are
logically independent problems.

## The plan

1. **Decompile** a binary config into a semi-human-readable format (JSON or
   YAML), with an explicit way of saying *"here is a binary blob I do not
   understand yet"*.
2. **Compile** that text back into a binary config, asserting at first that the
   result is the same size as the original, so pointers inside unknown blobs
   carry through unchanged.
3. **Assert byte identity** against the original. This is the part that makes the
   approach worth doing: if a config survives a decompile/recompile round trip
   unchanged, that is proof the model of the format is complete rather than
   plausible. Every newly decoded structure gets checked by the same test.

Once every section containing pointers is decoded - even where the rest of that
section is still opaque - the same-size restriction can be lifted and larger
changes become possible.

## What is known so far

Verified against a real Harmony 525 config (arch 9, protocol 9). Everything in
this list survives a decompile/recompile cycle byte-for-byte, which is a stronger
claim than "it looks right":

- the `.EZHex` container: XML header + binary blob, size and XOR checksum
- **a second checksum, the one the remote itself checks**: the `u16` immediately
  before the end marker is a `0x4321`-seeded XOR of the blob's little-endian
  words. Until this was found it passed through as opaque data, so every *edited*
  config this project produced carried a stale one and a remote would have
  refused it. A byte-identical round trip never showed it, and could not have
- the blob header: `AHCM` magic, a section pointer table of 18 sections and a
  trailing null. The same container carries arch 8, 12 and 14 under a different
  four letter cookie, and the table lines up by slot index
- pointers are **24-bit little-endian absolute flash addresses**;
  `config_base = 0x20000` - confirmed on arch 8 too
- **which sections carry pointer tables** - 10 of the 18, holding 685 addresses.
  Section 10 turns out to be nothing but a 487-entry pointer array
- the **header of all 114 records** in the region the section table does not
  cover, carrying another 249 pointers
- **section 17 is four 96x64 LCD bitmaps** - `tools/show_bitmaps.py` draws them.
  Three are page backgrounds. The fourth is solid ink, and the only thing ever
  copied out of it is the one-pixel rule under a menu heading
- **where the menu text lives.** It is not in the file as text and never was.
  Section 11 and every mode page hold a screen program; opcode 16 picks one of
  the five font sets in section 7, and opcodes 4 and 5 draw runs of *font-local
  glyph numbers*. `tools/render_525_screens.py` walks all 135 pages and draws
  them, which is how the sample's four devices got their names back
- **the 525's screen framing, from its firmware**: opcode 22 selects one of eight
  8-pixel rows, opcode 3 lays down that row's 96x8 strip, text is drawn over it,
  and opcode 23 transfers the finished row to the panel. Every one of the 135
  pages is exactly eight of those blocks
- **section 8 closes to the byte**: one leading action list of 34 bytes, then the
  packed run of all 135 mode-page binding lists, 1,052 bytes, and nothing else
- three more action opcodes: `0x75` sounds a tone through a counted GPIO toggle,
  `0x80 | n` writes state variable `n`, and `0x7C` is Glen Harris's per-device
  `QueueDelay`; its low-byte delay encoding/unit remains open
- **arch 8 works too**: all four 720/785/88x samples round-trip byte-identical,
  and survive a length change. Its markers are not what mirroring arch 9 would
  suggest, and its section table has a null in the middle
- the name table, wrapped in `0xFEED ... 0xBEEF` with a length field, whose `index`
  is the address of a **live state variable readable over USB**
- the **key table** format, `<u8 code> <u16 target> <0x7F>`, and that there is one
  overlay table per activity
- key codes are **keyboard matrix addresses**: `code = 0x80 | (row << 3) | column`
- **every pointer inside the infrared records**: four groups, 200 record headers,
  380 bodies, five symbol tables and 43 symbol blocks, on the layout
  [@dannybloe](https://github.com/dannybloe) published and verified against
  firmware. Until this was typed, lengthening a config quietly ruined all 200
  commands while every other check passed
- the vendor HID protocol, reimplemented and cross-checked against concordance

Two errors in this document's own earlier revisions were caught by the round-trip
test within an hour of it working, which is roughly the point of having it. Four
more, listed below, were caught by other people or by a stronger test, which is
the point of having those.

**Withdrawn, and worth knowing about.** Earlier revisions of this repository
claimed record bodies were blocks with a twelve-byte header, one per row of the
keyboard matrix; that records ended with a trailer pointing into section 8; that
124 references were buried in the bodies; and that `0x7C` carried a quantity
rather than a delay. All four are wrong. The first three were byte patterns that
really do occur and really are not structures, and reading the screen programs
from their roots dissolved all of them. The fourth was corrected by
[Glen Harris](https://github.com/glenharris), who designed the thing. See
[`docs/FORMAT.md`](docs/FORMAT.md) sections 4d, 4f, 4g and 4k, which keep the
old readings visible next to what replaced them.

Full detail, including the negative results worth not repeating, is in
[`docs/FORMAT.md`](docs/FORMAT.md).

## Editing

Pointers decompile as `region + delta` rather than raw offsets, so the compiler
recomputes them against wherever things end up. That is what makes a length
change survivable: `roundtrip.py --resize` lengthens a name, and all 5,015
symbolic pointers come back referring to the same things they referred to before.
On arch 9 the resize check also requires all 135 rendered screens to remain
pixel-identical and all 200 original class-5 IR records to expand exactly.

**This proves internal consistency, not that a remote would accept the result.**
A pointer hidden inside a region that is still opaque is invisible to this code
and would be silently left behind by exactly the kind of edit above. That is not
hypothetical: it has now happened twice, with the trailer checksum and with the
infrared records, and both times an outside reader found it rather than any test
here. Sections 1, 2, 3, 4 and 16 and most of the 114 record bodies are still
opaque, so treat length-changing edits as an experiment rather than a feature,
and read the safety note below before going anywhere near hardware.

## The main thing standing in the way

**Nobody knows which matrix position is which physical button.** The codes are
matrix coordinates, and the ordering in the config is Logitech's canonical key
order rather than the visual layout, so it cannot be read off the table. Sniffing
key presses over USB does not work - the remote locks its UI in USB mode and
never reports which key was pressed.

Without that map, a decompiler can round-trip a config perfectly and still not be
able to tell you which button you are remapping. Both halves of the problem are
written up - the matrix in [`docs/FORMAT.md`](docs/FORMAT.md), the buttons a human
sees in [`docs/BUTTON-LAYOUT.md`](docs/BUTTON-LAYOUT.md) - and joining them is
what nobody has done.

If you have any of the following, it would unblock a lot: original Logitech
documentation, a service manual, a photo of a bare Harmony PCB, or simply the
patience to buzz out a matrix with a multimeter. Details in
[`docs/OPEN-QUESTIONS.md`](docs/OPEN-QUESTIONS.md).

## Repository layout

```
docs/FORMAT.md          everything known about the format, with evidence
docs/OPEN-QUESTIONS.md  what is unknown, and what would answer it
docs/BUTTON-LAYOUT.md   the buttons as Logitech documents them
samples/harmony525/     a real arch 9 config, with checksums
samples/arch8/          thirteen 720/785/88x configs from two contributors
samples/harmony890/     two protocol 10 configs, which nothing here reads yet
samples/harmony650/     one protocol 14 config, same
patterns/               ImHex patterns, one hand written and the rest generated
schema/                 @glenharris's sketch of what a decompiled config should
                        look like, from the person who designed the format
tools/                  Python scripts used for the analysis (read-only)
```

## Samples wanted

The single most useful thing you can contribute is **a config dumped off a real
remote**, especially one from a model nobody has posted yet. What is here now:
one 525, thirteen 720/785/88x, two 890s and one 650, from four people.

Two of those arrived in August 2026 and immediately paid for themselves. The
890 and the 650 use protocols nothing here supports, and simply having them
showed that both are the familiar container with a different base address, which
is a much smaller gap than "unsupported" sounded like. See
[FORMAT.md §5j](docs/FORMAT.md).

Better still is a config **plus a note of what the remote actually shows** -
devices, activities, the labels on the custom buttons, and which button
positions are blank. [@kkong42](https://github.com/kkong42) did that for an 885
in [issue #20](https://github.com/trelowney/harmony-decompiler/issues/20), and it
is the only thing in this repository against which an answer can be checked.

```sh
concordance --dump-config my-remote.EZHex
```

That file contains your device brands and your activity names. It does not
contain account credentials - the `UserId` field in the sample here reads `0` -
but do have a look before posting, and say which remote it came from.

There is a ready-made issue form for this: **Contribute a config sample**.

## Contributing

Questions, half-formed ideas and "I tried X and it did not work" are all welcome -
negative results genuinely save other people time, and there is a section for them
in `FORMAT.md`. Use **Discussions** for open-ended thinking, **Issues** for
specific findings, samples and bugs. See
[CONTRIBUTING.md](CONTRIBUTING.md).

Nobody here is expected to already know this hardware.

## Safety

Nothing in this repository writes to a remote. The scripts in `tools/` are
read-only by construction: `hid_query.py` carries a command whitelist that refuses
`WRITE_FLASH`, `WRITE_FLASH_DATA`, `WRITE_MISC` and `ERASE_FLASH`.

When the time comes to test writing, the current understanding - from the original
Harmony developer - is that transferring a bad config through concordance should
confuse the runtime rather than brick the remote: you can usually send a new config
over USB, and in the worst case boot into safe mode, where the remote ignores the
config entirely. The caveat he attached is worth repeating, though: a config could
in principle contain an instruction sequence that drives the runtime to write to
arbitrary flash addresses, firmware and bootloader included. **Do not do the first
write experiments on a remote you care about.**

## Credit and prior work

The format was designed by [@glenharris](https://github.com/glenharris), who has
been generous with guidance in the concordance thread and in this repository's
discussions. Neither he nor anyone else credited here is responsible for anything
claimed here. Two things in this document are his directly: `0x7C` is
`QueueDelay`, a per-device queued delay rather than the quantity an earlier
revision called it, and an instruction is 24 bits wide with a fixed pattern that
can run through all three bytes, which is why this repository no longer talks
about 59 second-level sub-opcodes.

[@dannybloe](https://github.com/dannybloe)'s
**[harmony-explorations](https://github.com/dannybloe/harmony-explorations)** is a
parallel effort, MIT licensed, and further along as a general codec: it covers
architectures 8, 9, 12 and 14 with one parser. No code has moved between the two
projects in either direction, and the licence boundary is the reason. These
findings are his and are used with attribution rather than rediscovered:

- **how a glyph is packed on arch 9** - two bits to a pixel, with run and literal
  commands - which he published on 7 August 2026 and which the renderer in
  `tools/render_525_screens.py` implements
- **that the trailer `u16` is checked at all.** His parser is what showed that
  this project's first edited configs were internally invalid; the seed and the
  algorithm were then confirmed against the 525 firmware and all five samples here
- **the class-5 infrared layout**, header to body to symbol table to symbol
  block, published and checked against firmware. The relocation closure built on
  top of it here is this project's, and it exists because his reader refused a
  file that everything here had passed
- **one infrared group is one device**, and the shape of the mode and page
  records this repository's screen walker now reads from
- **the section 17 picture bank is self-verifying**, which is how an appended
  label edit here was caught leaving the file without a bank at all

He independently verified this project's arch-9 screen firmware lead against his
own 525 image, and found that opcode 23 brackets its transfer with `LATE` bit 2
as well as the `LATA` bit 5 reported here. He then corrected his own first
reading of the panel, and this repository's copy of it: `0xB0 | page` is shared
by several controller families and does not identify an SSD1306. The whole
bring-up sequence puts it in the **ST7565 / UC1701 class**, and the SSD1306's
mandatory charge-pump command appears nowhere in the firmware. He also measured
the server claim corrected at the top of this file.

This work leans directly on
**[concordance / libconcord](https://github.com/jaymzh/concordance)**
(© Phil Dibowitz 2007, © Kevin Timmerman 2007, maintained by
[@jaymzh](https://github.com/jaymzh)). Specifically:

- the `.EZHex` container layout - where the blob starts, and that the checksum is
  an XOR seeded `0x69` - was read out of
  `libconcord/operationfile.cpp:find_config_binary`
- the vendor HID framing and the `ReadMisc` command shapes reimplemented in
  `tools/hid_query.py` were derived from `libconcord/libhidapi.cpp` and
  `libconcord/remote.cpp`
- `concordance --dump-config` produced the sample in this repository, and
  `--get-time` was used to validate that our protocol implementation is correct
- `specs/protocol.txt` documents the transport

Where a file here documents or reimplements something learned from that codebase,
it says so at the point it does.

Samples came from people who had no reason to bother:
[@kkong42](https://github.com/kkong42) posted eleven 880, 885 and 890 configs
and, for one 885 in daily use, wrote out every device, activity and custom
button label to go with it, which is the closest thing this repository has to
ground truth. [@dmrzzz](https://github.com/dmrzzz) offered to press buttons on
hardware. Earlier samples came out of the
[concordance thread](https://github.com/jaymzh/concordance/issues/66).

The short version of all of the above: a good deal of what this repository
documents was worked out by other people first, and where that is the case it
should say so on the line where the claim is made, not only here.

## Licence

**GPL-3.0-or-later** - see [LICENSE](LICENSE).

This matches libconcord, deliberately. Parts of this work are derived from reading
that GPLv3 source, so the same licence is the honest and compatible choice, and it
keeps the door open for code to move in either direction between the two projects.

The sample config in `samples/` is published by its owner for research use.
