# Harmony 525 — arch 9 sample

A config read off a physical Harmony 525 on 2026-08-02 with
`concordance --dump-config`. As far as anyone in the
[concordance thread](https://github.com/jaymzh/concordance/issues/66) is aware,
this is the only publicly available **arch 9** config. The four samples already in
circulation are all arch 8 (720/785/88x).

It is published so the claims in [`docs/FORMAT.md`](../../docs/FORMAT.md) can be
checked rather than taken on trust. Every offset quoted there refers to this file.

## Files

| file | size | what it is |
|---|---|---|
| `config.EZHex` | 81,639 B | the complete file as dumped — XML header + blob |
| `config.bin` | 78,486 B | just the binary blob, split out for convenience |
| `header.xml` | 3,153 B | just the XML header, byte-exact |
| `header-pretty.xml` | 2,652 B | the same header, reformatted for reading |
| `SHA256SUMS.txt` | | checksums for all of the above |

`config.bin` and `header.xml` are derivable from `config.EZHex` with
`tools/split_ezhex.py`; they are included so a quick look does not require running
anything.

## What this remote is

| | |
|---|---|
| model | Logitech Harmony 525 "Mocha Decaf" |
| USB | `VID_046D` / `PID_C111`, rev `0916` |
| architecture | 9, protocol 9 |
| firmware | 3.0, board 2.5.0, skin 22 |
| flash | 512 KiB external (`FF:12`, 25F040) |
| config occupies | 77 of 384 KiB (19%) |
| devices configured | Panasonic TV, Genius amplifier, XBOX 360 |

The remote runs on the **native Windows HID stack**. No Zadig or libusb driver
swap is needed — that requirement applies to the Harmony 900/1000, not this
generation.

## Privacy

The XML header was checked before publishing: `UserId` reads `0`, there is no
serial number and no account data. The `<POSTOPTIONS>` blocks point at
`members.harmonyremote.com`, which has been dead for years.

What the file does reveal is the device brands and the state-variable names, which
is how `TV_Panasonic`, `Amplifier_Genius` and `XBOX_360` end up quoted throughout
the documentation. Published knowingly by the owner.

## Integrity

```sh
sha256sum -c SHA256SUMS.txt
python ../../tools/split_ezhex.py config.EZHex
```

The second command re-checks the container from the inside: `<BINARYDATASIZE>`
against the real blob length, and `<CHECKSUM>` against an XOR of the blob seeded
`0x69`. Both should pass.

## If you write this to a remote

Don't, unless it is a 525 and you have a reason to. `<INTENDEDVERSION>` in the
header pins PROTOCOL 9, SKIN 22, FLASH 0xFF:0x12, BOARD 2.5.0; a remote that does
not match refuses the transfer with *"This configuration file is not compatible
with your Harmony Remote."* On a matching 525 it would replace whatever
configuration is currently on the device with one set up for someone else's TV.

Restoring a dumped config needs `--force`, because concordance marks its own dumps
as such:

```sh
concordance --write-config config.EZHex --force
```

This has not been tested by the person who dumped it — testing it means writing to
the only 525 involved.
