# Contributing

This is a small research project about an obsolete remote control. Please treat it
as a workshop rather than a product: half-finished ideas, "I tried this and it did
not work", and questions from people who have never touched this hardware are all
genuinely useful.

No prior knowledge of Harmony internals is assumed. If something in the docs is
impenetrable, saying so is a contribution - it usually means it is badly written.

## Where to put things

| | |
|---|---|
| **Discussions** | open-ended thinking, "has anyone considered...", brainstorming, questions |
| **Issues** | a specific finding, a config sample, a bug in a script, a concrete task |
| **Pull requests** | code and documentation changes |

If you are not sure, pick either. Nobody will mind, and things can be moved.

## Contributing a config sample

The most useful single thing you can add, especially from a remote that is **not**
a 525.

```sh
concordance --dump-config my-remote.EZHex
```

Attach the file to an issue using the **Contribute a config sample** form, and say
which remote it came from.

Before posting, have a look at what is in it. The XML header of the sample in this
repository has `UserId` set to `0` and carries no account data, but the config
does contain your device brands and your activity names - for example the sample
here makes it obvious the owner has a Panasonic TV and an XBOX 360. That is
usually harmless, but it should be your decision rather than a surprise.

Every sample is worth having even if it duplicates a model already present.
Two configs from the *same* remote taken before and after one deliberate change
are especially valuable, even though naive diffing does not work - knowing exactly
what changed logically is the point.

## Contributing a finding

Please separate **what was measured** from **what it probably means**. The format
docs mark hypotheses explicitly and it matters: several early conclusions in this
project turned out to be wrong, and the ones caught early were caught because they
were labelled as guesses.

Useful to include:

- the offsets and the bytes
- which config it came from
- how it was checked, and on how many instances
- anything that would falsify it

Negative results belong in the docs too. `FORMAT.md` has a section for approaches
that were tried and do not work, and `OPEN-QUESTIONS.md` ends with a table of
questions already settled, specifically so nobody repeats the work.

## Contributing code

There is no build system and no dependencies - the scripts in `tools/` are plain
Python 3 using only the standard library, and the Windows HID access goes through
`ctypes` rather than a binding. Keeping it that way means anyone can run a script
without setting anything up. If something genuinely needs a dependency, say so in
the PR and it can be discussed.

Style: whatever the surrounding file already does.

### The safety rule

**Nothing in this repository writes to a remote.**

`tools/hid_query.py` carries an explicit command whitelist and hard-refuses
`0x30 WRITE_FLASH`, `0x40 WRITE_FLASH_DATA`, `0xA0 WRITE_MISC` and
`0xD0 ERASE_FLASH`. Every other script routes through it. Please do not remove or
bypass that whitelist. Writing to a remote is a real and useful thing to work on,
but it should be a deliberate, separate, clearly-labelled piece of work rather than
something that arrives inside a patch about parsing.

If you are experimenting with writes, do it on a remote you are prepared to lose.
The current understanding - from the original Harmony developer - is that pushing
a bad config through concordance should confuse the runtime rather than brick the
device, and that safe mode ignores the config entirely so a new one can be sent.
He also noted that a config could in principle contain an instruction sequence
that makes the runtime write to arbitrary flash, firmware and bootloader included.
Treat it as recoverable-but-not-guaranteed.

Also worth knowing: for these older models, **safe mode cannot be restored**.
concordance has `--dump-safemode` but no `--write-safemode`. Ordinary config
programming does not touch it, but it is a good reason not to go poking at flash
regions outside the config.

## Licence

By contributing you agree your work is released under **GPL-3.0-or-later**, the
same licence as [libconcord](https://github.com/jaymzh/concordance), which parts
of this project are derived from.
