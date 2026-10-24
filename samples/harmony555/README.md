# Harmony 555 (protocol 9, skin 48)

Two configs uploaded by **[@kkong42](https://github.com/kkong42)**, in
[issue #42](https://github.com/trelowney/harmony-decompiler/issues/42) and
[issue #46](https://github.com/trelowney/harmony-decompiler/issues/46), each with
the "happy to publish" box ticked. Mirrored unmodified, under the names they
were uploaded with, and only the configs: the firmware, safe mode and update
files in the same uploads stay where they are. If he would rather they were not
here, they come straight back out.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `H555-Config.EZHez` | #42 | 24,480 B | 9 | 48 | 3.0.0 |
| `H555-New-1-Config.EZHex` | #46 | 122,507 B | 9 | 48 | 3.0.0 |

Both are the same architecture as the Harmony 525, run the same firmware
(FORMAT 5ab) and round trip byte for byte.

## `H555-Config.EZHez`, issue #42

An **empty** configuration: no devices and no activities, which is what a 555
holds before it was ever set up.

Its typeface is the 525's: 50 of its 53 glyph codes draw the 525's pixels, and
the other three (a comma, a K and a double quote) are read from this file into
`tools/arch9_glyphs.json`.

A first device can be added to it, with nothing to copy from: every key as
Logitech lists them on the 525 and the 515, the Devices screen's own frame for
its page, section 5's count grown from 0, and the state variable a Devices
soft key sets read off the system list that clears it (FORMAT 5ak).

## `H555-New-1-Config.EZHex`, issue #46

Seven devices and six activities, programmed with the Harmony 7.x desktop
software: the **first arch 9 configuration from Logitech's own compiler with
more than four devices**. Devices and the activity menu are two pages each, and
font set 4 is eight rows high, the page counter's, as `compose525.counter_font`
builds it for an empty 555. Each activity has its own Devices screen (section 9
lists 4 to 10, one per activity and one for none), and unlike what the composer
writes, each lists the devices in its own order.

Six of its codes draw characters no earlier file had, read by eye on
2026-10-24 into `tools/arch9_glyphs.json`: J, j, both brackets, an apostrophe,
and a 7 in the counter's font.
