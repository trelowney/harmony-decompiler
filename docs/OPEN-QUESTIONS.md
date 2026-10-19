# Open questions

What is *not* known, roughly in order of how much it matters to someone using a
remote. If you can answer any of these, even partly, even with "I checked and
it is not X", please open an issue or a discussion.

This file was rewritten on 2026-10-19. Its earlier version led with questions
that have since been answered (which key is which, what the instructions mean,
what is in a block); they are in the table at the end so nobody spends an
evening on them twice.

---

## 1. Does a write work on a 515, and what does a 555 with devices look like?

The 515 and 555 run the 525's firmware (FORMAT 5ab), and a 515's configuration
is read and composed with the 525's tools, every operation passing its own
checks (5ai). Nobody has written one to a real 515. The write is the same USB
sequence, so it should, but "should" is what this repository has been wrong
about before.

The only 555 sample is empty: no device and no activity. A device is built from
an existing device's screen, and the state variable a Devices soft key sets is
read off an existing soft key, so nothing can be added to an empty 555.

**What would answer it:** a 515 owner willing to try a write with the page (it
saves a backup first and can put it back), and a 555 config with at least one
device on it.

## 2. A config with nothing to start from

Everything composed here starts from the remote's own configuration and changes
it: devices, activities, pages and screens are copied from what is there and
altered. That is enough for a remote that was ever set up. It is not enough for
one that was wiped, or for a 555 that never had a device.

[@dannybloe](https://github.com/dannybloe) builds complete configurations for
the Harmony 600, 650 and 700 with nothing copied from a Logitech file, so it can
be done; for arch 9 nobody has. The firmware's fixed screens (FORMAT 5x) and
the welcome and help screens would have to be built too.

## 3. The instruction families that dispatch again

`0x07`, `0x0F`, `0x1F` and `0x3F` are not single operations: each dispatches a
second time on bits of its operand (FORMAT 4j, 4k). The members the composer
needs are named: `0x1F 0xFFnn` selects an activity's key set, `0x1F 0xE8nn`
the list at RAM `0x341`, `0x1F 0xFCnn` pushes an event (5aa), and six members
of the `0x1F` band read state variables ([@dannybloe](https://github.com/dannybloe),
his section 277). The rest are unnamed. `tools/actions.py` prints what the data
says about each.

## 4. What the small `0x7C` after every send is for

`0x7C` is a per device wait in tenths of a second (Glen Harris, discussion #14;
@dannybloe, his section 235). Every send carries one with the value 1, and a
send without it goes out on a key press but not from an activity (his section
278). Whether that 1 is a tenth of a second the device needs between commands,
or something the queue needs, is not settled; the composer copies the value the
device's existing sends use.

## 5. The infrared classes other than 5

Every arch 9 record here is class 5: a carrier, then three block pointers (once,
held, tail) into bodies of duration runs (FORMAT 4n, 5q). Classes 1 to 4 exist
in the firmware and in no sample here.

## 6. Arch 8 beyond the round trip

All thirteen arch 8 samples round trip byte for byte and survive a length
change, and the 880/885 keypad is fixed (FORMAT 5n). Much of the rest of an
arch 8 config is decoded only as far as the pointers. harmony-explorations reads
arch 8 with the same parser as 9, 12 and 14 and is the place to start.

## 7. Protocols 10 and 14 here

The 890 (protocol 10) and 650 (protocol 14) samples are kept but nothing in this
repository decodes them (FORMAT 5j, 5u). @dannybloe's codec reads protocol 14 and
writes it.

---

## Questions that have been answered

Kept here so nobody spends an evening on them twice.

| question | answer |
|---|---|
| Which matrix position is which button on the 525? | The keypad is 8 by 7 and its own key table says so (FORMAT 5z); every key's code is named from what the sample's device modes send on it and confirmed by pressing (5af, `compose525.KEYS`). Hash is by elimination. The arch 8 boards were buzzed out by @kkong42 (5n) |
| Can key presses be read over USB? | No. The remote locks its UI while connected - [§5d](FORMAT.md) |
| What do the action instructions mean? | An action is `<u16 operand><u8 opcode>`. Send (`0x7D`), wait (`0x7C`), write a state variable (`0x80 \| n`), show a screen (`0x7E`), run a list (`0x7F`), look up section 14 (`0x72`), tone (`0x75`) - FORMAT 4i to 4p, 5aa. The dispatching families are question 3 |
| How does a key press find what to do? | A stack of binding lists: the global map, the screen's own list, the activity's key set, the defaults - FORMAT 4p, 5ad, 5ag |
| Why did a fifth device page never turn? | The page keys were bound in a list the Devices screen never searched, and there are four Devices screens, one per activity context - FORMAT 4q, 5ae |
| What is inside a "block"? | There are no blocks. Those bytes are screen programs and the payload is menu text - [§4f](FORMAT.md), [§4l](FORMAT.md) |
| Where is the text the screen shows? | In the config, as glyph numbers drawn by screen programs; a code is assigned per configuration and is read by its pixels - [§4l](FORMAT.md), [§5ai](FORMAT.md) |
| How is infrared stored? | Class 5: carrier, then bodies indexing a symbol table of duration runs, layout by @dannybloe - [§4n](FORMAT.md). A symbol block holds at most 127 words, which is why the first composed device was silent - [§5af](FORMAT.md) |
| Do the unidentified sections hide pointers? | Every section is named now: 1 protocol and skin, 2 the writeable flash, 3 the build time, 4 the firmware's error screens, 16 an empty pointer table - FORMAT 4r, 5x. 180 bytes were reached by nothing and are named too |
| What is the `u32` at offset 0x08? | The container format, `0x1400` on arch 9 and 14, `0x1500` on arch 8; not an architecture identifier, which section 1 states - FORMAT 5u |
| Is the XML `CHECKSUM` the only checksum? | No. A second `u16` before the end marker is the one the remote checks - [§4m](FORMAT.md) |
| Can a config grow? | Yes, by 52,588 checked bytes on a 525 before the boot validator's word count stops fitting; a device costs about 19 KB - FORMAT 5w, 4s |
| Can a composed config work on a real remote? | Yes: devices, activities, pages, deletions, power states, waits and database codes, each written and pressed on a 525 - FORMAT 5af to 5ah |
| How many frames does a Panasonic press need? | Three, and fourteen to switch on and off; codes from databases now copy what the remote already sends that protocol - FORMAT 5ah |
| Can configs be diffed to isolate a change? | No. A small logical change moves most of the bytes - [§5](FORMAT.md) |
| Is `config_base` the same across architectures? | No: `0x20000` on arch 8 and 9, `0x30000` on protocols 10 and 14, and it is read from the file - FORMAT 5j, 5s |
| Are pointers 32-bit? | No, 24-bit little-endian |
| Does the remote need a driver swap? | Not the 525: it is native HID, and the page reads it with WebHID |
| Can the 525 learn infrared? | Yes, and the page uses it to learn a device key by key - FORMAT 5m |
| Is there a published solution to this format already? | Yes, and further along on the remotes it covers: [@dannybloe](https://github.com/dannybloe)'s [harmony-explorations](https://github.com/dannybloe/harmony-explorations), MIT |
| Does `0x7C` carry a quantity rather than a delay? | No. It is `QueueDelay`, per device, in tenths of a second - Glen Harris, @dannybloe, FORMAT 4k, 5ah |
| Are there 59 second-level sub-opcodes? | No. An instruction is 24 bits with a fixed pattern that can run through all three bytes - Glen Harris |
