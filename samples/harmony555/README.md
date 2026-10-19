# Harmony 555 (protocol 9, skin 48)

One config uploaded by **[@kkong42](https://github.com/kkong42)** in
[issue #42](https://github.com/trelowney/harmony-decompiler/issues/42), with the
"happy to publish" box ticked. Mirrored unmodified, under the name it was
uploaded with (`.EZHez`), and only the config: the firmware, safe mode and
update files in the same upload stay where they are. If they would rather it
were not here, it comes straight back out.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `H555-Config.EZHez` | #42 | 24,480 B | 9 | 48 | 3.0.0 |

It is the same architecture as the Harmony 525, runs the same firmware
(FORMAT 5ab) and round trips byte for byte. It is an **empty** configuration:
no devices and no activities, which is what a 555 holds before it was ever set
up.

Its typeface is the 525's: 50 of its 53 glyph codes draw the 525's pixels, and
the other three (a comma, a K and a double quote) are read from this file into
`tools/arch9_glyphs.json`.

Nothing can be added to it yet. A device is built from an existing device's
screen, and the state variable a Devices soft key sets is read off an existing
soft key; with none, both are unknown. A 555 config with one device in it would
settle that.
