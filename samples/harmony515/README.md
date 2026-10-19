# Harmony 515 (protocol 9, skin 67)

One config uploaded by **[@kkong42](https://github.com/kkong42)** in
[issue #35](https://github.com/trelowney/harmony-decompiler/issues/35), with the
"happy to publish" box ticked. Mirrored unmodified, and only the config: the
firmware and safe mode dumps in the same upload stay where they are. If they
would rather it were not here, it comes straight back out.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `H515-Config.EZHex` | #35 | 54,696 B | 9 | 67 | 1.0.0 |

It is the same architecture as the Harmony 525 and runs the same firmware
(FORMAT 5ab), and it round trips byte for byte. Three devices (a mini system,
a squeezebox and a digital music server) and two activities.

What differs from the 525's files, and what the tools had to learn for it
(FORMAT 5ai):

* the typeface: skin 67 draws other pixels, and its glyph codes past 49 mean
  other characters, so text is read through `tools/arch9_glyphs.json`, built by
  `tools/build_arch9_glyphs.py` from this file and the 525 sample;
* the state variables after the devices' own: CurrentActivityState is 17 here
  (15 on the 525's), and the variable a Devices soft key sets is 21 (18);
* a corner's second line is drawn one row lower, at 25 and 47.

`tools/verify_500_series.py` reads it and composes on it.
