# harmony-decompiler

Reverse engineering the **Logitech Harmony configuration binary**, with the goal
of decompiling a config into a readable text format and compiling it back —
byte for byte.

Logitech's servers for these remotes are gone. A config that is already on a
remote can still be read off it, but nobody can generate a new one. This
repository is an attempt to change that.

> **Status: research in progress.** Large parts of the format are understood and
> verified; there is no working compiler yet. See
> [`docs/FORMAT.md`](docs/FORMAT.md) for what is known and
> [`docs/OPEN-QUESTIONS.md`](docs/OPEN-QUESTIONS.md) for what is not.

## Scope

This repository is about **the config format and the toolchain around it**.
Talking to the remote over USB is already solved by
[concordance / libconcord](https://github.com/jaymzh/concordance) — please use
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

Once every section containing pointers is decoded — even where the rest of that
section is still opaque — the same-size restriction can be lifted and larger
changes become possible.

## What is known so far

Verified against a real Harmony 525 config (arch 9, protocol 9):

- the `.EZHex` container: XML header + binary blob, size and XOR checksum
- the blob header: `AHCM` magic, an 18-entry section pointer table
- pointers are **24-bit little-endian absolute flash addresses**;
  `config_base = 0x20000` — confirmed on arch 8 too
- the name table, and the fact that its `index` field is the address of a
  **live state variable readable over USB**
- the **key table** format, `<u8 code> <u16 target> <0x7F>`, and that there is one
  overlay table per activity
- key codes are **keyboard matrix addresses**: `code = 0x80 | (row << 3) | column`
- the vendor HID protocol, reimplemented and cross-checked against concordance

Full detail, including the negative results worth not repeating, is in
[`docs/FORMAT.md`](docs/FORMAT.md).

## The main thing standing in the way

**Nobody knows which matrix position is which physical button.** The codes are
matrix coordinates, and the ordering in the config is Logitech's canonical key
order rather than the visual layout, so it cannot be read off the table. Sniffing
key presses over USB does not work — the remote locks its UI in USB mode and
never reports which key was pressed.

Without that map, a decompiler can round-trip a config perfectly and still not be
able to tell you which button you are remapping. If you have any of the following,
it would unblock a lot: original Logitech documentation, a service manual, a photo
of a bare Harmony PCB, or simply the patience to buzz out a matrix with a
multimeter. Details in [`docs/OPEN-QUESTIONS.md`](docs/OPEN-QUESTIONS.md).

## Repository layout

```
docs/FORMAT.md          everything known about the format, with evidence
docs/OPEN-QUESTIONS.md  what is unknown, and what would answer it
samples/harmony525/     a real arch 9 config, with checksums
tools/                  Python scripts used for the analysis (read-only)
```

## Samples wanted

The single most useful thing you can contribute is **a config dumped off a real
remote**, especially one that is not a 525. Four arch 8 samples (720/785/88x) are
already public in the thread linked above; every other model is unrepresented.

```sh
concordance --dump-config my-remote.EZHex
```

That file contains your device brands and your activity names. It does not
contain account credentials — the `UserId` field in the sample here reads `0` —
but do have a look before posting, and say which remote it came from.

There is a ready-made issue form for this: **Contribute a config sample**.

## Contributing

Questions, half-formed ideas and "I tried X and it did not work" are all welcome —
negative results genuinely save other people time, and there is a section for them
in `FORMAT.md`. Use **Discussions** for open-ended thinking, **Issues** for
specific findings, samples and bugs. See
[CONTRIBUTING.md](CONTRIBUTING.md).

Nobody here is expected to already know this hardware.

## Safety

Nothing in this repository writes to a remote. The scripts in `tools/` are
read-only by construction: `hid_query.py` carries a command whitelist that refuses
`WRITE_FLASH`, `WRITE_FLASH_DATA`, `WRITE_MISC` and `ERASE_FLASH`.

When the time comes to test writing, the current understanding — from the original
Harmony developer — is that transferring a bad config through concordance should
confuse the runtime rather than brick the remote: you can usually send a new config
over USB, and in the worst case boot into safe mode, where the remote ignores the
config entirely. The caveat he attached is worth repeating, though: a config could
in principle contain an instruction sequence that drives the runtime to write to
arbitrary flash addresses, firmware and bootloader included. **Do not do the first
write experiments on a remote you care about.**

## Credit and prior work

The format was designed by [@glenharris](https://github.com/glenharris), who has
been generous with guidance in the concordance thread. Neither he nor anyone else
credited here is responsible for anything claimed in this repository.

This work leans directly on
**[concordance / libconcord](https://github.com/jaymzh/concordance)**
(© Phil Dibowitz 2007, © Kevin Timmerman 2007, maintained by
[@jaymzh](https://github.com/jaymzh)). Specifically:

- the `.EZHex` container layout — where the blob starts, and that the checksum is
  an XOR seeded `0x69` — was read out of
  `libconcord/operationfile.cpp:find_config_binary`
- the vendor HID framing and the `ReadMisc` command shapes reimplemented in
  `tools/hid_query.py` were derived from `libconcord/libhidapi.cpp` and
  `libconcord/remote.cpp`
- `concordance --dump-config` produced the sample in this repository, and
  `--get-time` was used to validate that our protocol implementation is correct
- `specs/protocol.txt` documents the transport

Where a file here documents or reimplements something learned from that codebase,
it says so at the point it does.

## Licence

**GPL-3.0-or-later** — see [LICENSE](LICENSE).

This matches libconcord, deliberately. Parts of this work are derived from reading
that GPLv3 source, so the same licence is the honest and compatible choice, and it
keeps the door open for code to move in either direction between the two projects.

The sample config in `samples/` is published by its owner for research use.
