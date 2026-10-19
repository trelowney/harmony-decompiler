# harmony-decompiler

Reverse engineering the **Logitech Harmony configuration binary** of the older
EasyZapper remotes (the 525, 515, 555 and their arch 8 cousins, the 720/785/88x),
so a remote can be read, understood, changed and written again without the
service that used to do it.

That service is gone: `members.harmonyremote.com` serves a discontinuation
notice. The later MyHarmony service still answers for the remotes it serves
([@dannybloe](https://github.com/dannybloe) measured that on 7 August 2026), but
not for these.

## Where it stands

**On a Harmony 525 this now does what Logitech's software used to, in a browser.**
The page at **https://trelowney.github.io/harmony-decompiler/web/** reads the
remote over USB, shows its devices, activities and every screen on a drawn
remote you can press, and changes it:

* devices: add (learned through the 525 itself, typed, pasted as Pronto, or
  found in Flipper-IRDB or irdb), edit key by key, rename, move, delete, and
  keep track of whether a device is on;
* activities: add, edit, rename, move, delete; switch devices on and off, send
  any commands in order, wait between them, end on a device's own screen, take
  the volume from another device;
* write it back the way Concordance does, with a backup saved first and a button
  to put the backup back.

Every one of those has been written to the author's 525 and pressed. The
configurations are composed from the remote's own, not generated from nothing:
see [FORMAT.md](docs/FORMAT.md) 5af to 5ah.

**The Harmony 515 and 555** run the same firmware. A 515's configuration is read
and changed the same way, checked against a sample, never written to a real one.
A 555 is read; the one sample is empty, so nothing can be added to it yet
(FORMAT 5ai).

**The format** decompiles to JSON and compiles back byte for byte on every arch
8 and arch 9 sample here, and a length change relinks every pointer the
decompiler knows. What every section, instruction and infrared record means is
in [FORMAT.md](docs/FORMAT.md); what is still unknown is in
[OPEN-QUESTIONS.md](docs/OPEN-QUESTIONS.md).

> **Experimental, and entirely at your own risk.** Written to one Harmony 525
> only, the author's own; a 515 or 555 has never been written to. Writing to a
> remote can leave it not working, with no way back. No warranty, no support,
> and nobody here is responsible for a remote, or anything else, that it
> damages. Not affiliated with Logitech.

## In a browser, with nothing installed

Open the page in Chrome or Edge (they have WebHID), press **Read the remote**,
and save the backup it offers. Or open a saved `.EZHex`. Nothing is sent
anywhere but to the remote: the page runs the Python in `tools/` in the browser
(Pyodide). To run it locally, serve the repository and open `web/`:

```sh
python -m http.server 8525      # then http://127.0.0.1:8525/web/
```

The USB reading follows [@dannybloe](https://github.com/dannybloe)'s protocol
work in harmony-explorations (MIT); the write follows libconcord step by step.
If you write with Concordance instead, close the page first: while it holds the
remote, Concordance cannot find it again after the restart.

Codes from databases are fetched only when one is picked:
[Flipper-IRDB](https://github.com/Lucaslhm/Flipper-IRDB) (CC0) and irdb.
Contains/accesses irdb by Simon Peter and contributors, used under permission.
For licensing details and for information on how to contribute to the database,
see https://github.com/probonopd/irdb

A database code is sent the way the remote already sends that protocol to a
device it knows: the owner's Panasonic wants three frames a press and fourteen
to switch on, and gets them (FORMAT 5ah). The encoders are checked bit for bit
against frames the remote is known to send (`tools/verify_flipper_ir.py`,
`tools/verify_irdb.py`, `tools/verify_pronto.py`); RC5, RC6 and Sony are
encoded from Flipper's definitions and not yet checked against a device.

## On the command line

```sh
cd tools
python roundtrip.py                       # decompile, recompile, compare bytes
python roundtrip.py --resize              # lengthen a name, check it relinks
python decompile.py --summary             # what is decoded and what is not
python decompile.py config.EZHex out.json
python compile.py out.json new.EZHex --against config.EZHex
python render_525_screens.py --out rendered   # draw every screen as a BMP
python verify_compose525.py               # every composing operation, checked
python verify_500_series.py               # the same on the 515 and 555 samples
```

`tools/compose525.py` is the library the page calls: `add_device`,
`rekey_device`, `rename_device`, `delete_device`, `place_device`,
`add_activity`, `edit_activity`, `delete_activity`, `place_activity`,
`add_power`, and readers for devices, activities and what they do. Each
operation checks its own result before handing it back. Nothing in `tools/`
writes to a remote; `tools/README.md` lists the scripts.

## How the config works, in short

A config is a program for an interpreter in the remote's firmware, not a table
to fill in. The parts that matter for changing one:

* **an action** is three bytes, `<u16 operand><u8 opcode>`, and a key press runs
  a list of them: send an infrared command (`0x7D`), wait for that device in
  tenths of a second (`0x7C`), set a state variable (`0x80 | n`), show a screen
  (`0x7E`), run another list (`0x7F`). FORMAT 4p, 5aa, 5ah;
* **a key** is looked up through a stack of binding lists: the global map, the
  screen's own list, the activity's key set, the defaults. FORMAT 5ag;
* **a device** is an infrared group of commands plus a screen of its own, and its
  power is a state variable whose transitions send the on and off codes. FORMAT
  5v, 5ag;
* **text** is glyph numbers assigned per configuration, drawn by screen programs;
  a code is read by the pixels it draws. FORMAT 4l, 5ai;
* **infrared** is stored as carrier, then blocks of duration runs, at most 127
  words a block. FORMAT 4n, 5af.

Two of those were found by other people's readers refusing a file every test
here had passed (the trailer checksum, FORMAT 4m, and the infrared pointers,
4n), which is why every change is also checked with
[@dannybloe](https://github.com/dannybloe)'s independent codec.

## Repository layout

```
web/                    the page: read, try, change and write a remote
tools/                  the decompiler, the composer, the checks (read-only)
docs/FORMAT.md          everything known about the format, with the evidence
docs/OPEN-QUESTIONS.md  what is not known, and what would answer it
docs/BUTTON-LAYOUT.md   the 525's buttons as Logitech documents them
samples/harmony525/     the author's 525, as read and as composed
samples/harmony515/     a 515, and samples/harmony555/ an empty 555
samples/arch8/          thirteen 720/785/88x configs from two contributors
samples/harmony890/     two protocol 10 configs, which nothing here reads yet
samples/harmony650/     one protocol 14 config, same
patterns/               ImHex patterns
schema/                 @glenharris's sketch of a decompiled config
```

## Samples wanted

A config dumped off a real remote is still the most useful thing to send, with
a note of what the remote shows (devices, activities, button labels).

```sh
concordance --dump-config my-remote.EZHex
```

Most wanted now: **a Harmony 555 with at least one device on it**, which is what
adding to a 555 needs, and anyone with a **515** willing to try a write. The
file holds your device brands and activity names and no account data (the
`UserId` in the samples reads `0`); have a look before posting. There is an
issue form for it: **Contribute a config sample**.

## Contributing

Questions, half-formed ideas and "I tried X and it did not work" are all
welcome. Discussions for open-ended thinking, issues for findings, samples and
bugs. See [CONTRIBUTING.md](CONTRIBUTING.md). Nobody here is expected to already
know this hardware.

## Credit and prior work

The format was designed by [@glenharris](https://github.com/glenharris), who has
been generous with guidance in the concordance thread and in this repository's
discussions: `0x7C` is his `QueueDelay`, and an instruction is 24 bits with a
fixed pattern that can run through all three bytes. Neither he nor anyone else
credited here is responsible for anything claimed here.

[@dannybloe](https://github.com/dannybloe)'s
**[harmony-explorations](https://github.com/dannybloe/harmony-explorations)**
(MIT) is a parallel effort and further along on the remotes it covers: it
composes and writes complete configurations for the Harmony 600, 650 and 700
and is close on the Harmony One. No code has moved between the projects; these
findings are his and are used with attribution rather than rediscovered:

* how a glyph is packed on arch 9, and that a glyph code is read by its pixels;
* that the trailer `u16` is checked at all;
* the class 5 infrared layout, and that a symbol block holds at most 127 words;
* one infrared group is one device, and the mode and page record shapes;
* state variables, power transitions and the all-off list;
* `0x7C` in tenths of a second;
* the USB read protocol the page uses.

This work leans directly on
**[concordance / libconcord](https://github.com/jaymzh/concordance)**
(© Phil Dibowitz 2007, © Kevin Timmerman 2007, maintained by
[@jaymzh](https://github.com/jaymzh)): the `.EZHex` container and its checksum,
the HID framing, and the write sequence the page follows. Where a file here
reimplements something learned from it, it says so at that point.

Samples and measurements came from people who had no reason to bother:
[@kkong42](https://github.com/kkong42) posted arch 8, 515, 555, 890 and other
configs, wrote out an 885 in daily use button by button, and buzzed out an 880
and an 885 board; [@psolyca](https://github.com/psolyca) dumped a 650 three
times one device apart; [@Rtas-17](https://github.com/Rtas-17) found how the
base is read out of the file. Earlier samples came out of the
[concordance thread](https://github.com/jaymzh/concordance/issues/66).

A good deal of what this repository documents was worked out by other people
first, and where that is the case it should say so on the line where the claim
is made, not only here.

## Licence

**GPL-3.0-or-later**, see [LICENSE](LICENSE). This matches libconcord,
deliberately: parts of this work are derived from reading that GPLv3 source.
The sample configs in `samples/` are published by their owners for research use.
