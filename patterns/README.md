# ImHex patterns

[ImHex](https://github.com/WerWolv/ImHex) patterns for a Harmony config blob,
suggested by @glenharris in
[discussion #3](https://github.com/trelowney/harmony-decompiler/discussions/3).

| file | what it is |
|---|---|
| `harmony.hexpat` | the type definitions, hand written, with the reasoning for each |
| `harmony525.hexpat` | generated: every region of the 525 config, placed |
| `arch8-Update.hexpat` | generated: the same for an arch 8 sample |

Open a generated one alongside the matching config and the whole blob is
annotated, not just its header.

## Why two files

Most of these structures cannot be found by scanning a config from the top.
They are reached by following pointers, and several are distinguished only by
where a pointer lands rather than by anything in their own bytes. A bitmap is a
bitmap because a block header points at it; the same bytes elsewhere are not
one. A five-byte key table read at a stride of four dissolves into noise.

So `harmony.hexpat` describes the shapes and `tools/export_hexpat.py` runs the
decompiler over a real config and writes out where each of them actually sits:

```
python tools/export_hexpat.py samples/harmony525/config.bin
```

The generated file embeds a copy of the types, so it stands alone. Regenerate
rather than editing it, and put anything worth keeping into the recognisers in
`tools/hconfig.py`, where the round-trip test can check it.

## Reading the result

The patterns cover the blob, which is the binary part of an `.EZHex` with the
XML header and its `\r\n` removed. `samples/harmony525/config.bin` is already
in that form; `tools/split_ezhex.py` produces one from any `.EZHex`.

Addresses inside a config are absolute flash addresses rather than file
offsets, with `config_base = 0x20000`, so a pointer reading `0x02F35B` means
offset `0xF35B`. They are 24 bit, three bytes little-endian, which is why `0x02`
is the most common byte in the whole file.

An `Opaque` region is one nobody has explained yet. There are a lot of them:
27% of the 525 config is decoded and the rest passes through as bytes. They are
placed explicitly rather than skipped because the model the decompiler works to
is that regions tile the blob completely, with no gaps and no overlaps, and a
gap is somewhere a structure can hide unnoticed.

## Checking a change

ImHex has a command line interface, so a pattern can be compiled and run
against a config without opening the GUI:

```
imhex --pl run -i samples/harmony525/config.bin -p patterns/harmony525.hexpat
```

Silence and an exit code of zero mean it compiled and evaluated cleanly. To see
what it produced, ask for the data instead:

```
imhex --pl format -i samples/harmony525/config.bin -p patterns/harmony525.hexpat -o out.json -f json
```

That writes one JSON entry per placed region - 2,771 of them for the 525 - which
is also a quick way to check a new recogniser against the decompiler's own
output. Both files here have been run this way on ImHex 1.38.1.

Two names to avoid if you add a field: `padding` and `parent` are reserved
words in the pattern language, and a template parameter cannot share a name
with a field in the same struct.
