# Harmony IR learner for Windows

A capture-only front end to Concordance's IR learning API. It lets a Harmony
remote be the infrared receiver: point the original equipment remote at the
Harmony, press a key, get the carrier frequency and the mark/space durations
out as JSON.

**It needs no Logitech account, no service, no `LearnIR.EZTut`, no LearnIR
hardware, no 32-bit Python and no compiler.** It uses the `libconcord-6.dll`
that a normal Concordance install already puts on your disk.

It also writes nothing. The script imports eleven functions from libconcord and
not one of them writes, erases, resets or updates anything; the whole capture
path is `init`, identify, `learn_from_remote`, free, `deinit`. That is a
property of the file rather than a promise, and it is worth checking yourself
before running anything from a repository like this one.

## Use

Connect the Harmony over USB and wait for the beep and `USB connected`.

Probe, which sends no capture command at all:

```powershell
powershell -ExecutionPolicy Bypass -File .\HarmonyIrLearner.ps1
```

Capture one key:

```powershell
powershell -ExecutionPolicy Bypass -File .\HarmonyIrLearner.ps1 -Capture -Key Power -Output .\captures\power.json
```

If Concordance is not in `Program Files` or on `PATH`, add
`-ConcordanceDir <the directory holding libconcord-6.dll>`.

### Aiming it

On a Harmony 525 the learning sensor is at the **bottom end, below the numeric
keypad**. It is not the USB end, and guessing wrong is the most common reason a
capture times out. Hold the source remote 5 to 10 cm away, pointing at it.

**Tap the key, do not hold it.** libconcord stops at 1000 durations, and a held
key can fill that with valid repeats and still hand you a truncated result. The
script warns when a capture hits the limit; treat that as a retry, not a result.

A Harmony cannot hear its own transmission, so the source has to be a different
remote.

## Output

JSON: the carrier in Hz, the durations in microseconds alternating mark and
space starting with a mark, the identity of the remote that captured it, the
SHA-256 of the DLL that did the work, and a warnings list.

Two behaviours worth knowing, both from reading libconcord rather than guessing:

- it waits 5 seconds for the first mark and then ends the capture after 0.5
  seconds of silence, so a real capture takes about half a second longer than
  the key press;
- on an error it can still return a partial signal, so the script reports
  `libconcord_error` and `partial` alongside the durations instead of throwing
  the data away.

## How well does it work

Well enough to trust. The same Samsung key was captured twice, once with a
LearnIR V2 and once through a Harmony 525, and compared against the record a
generated config stores for it:

| | LearnIR V2 | via the 525 | stored in the config |
|---|---:|---:|---:|
| carrier | 38,000 Hz | 38,237 Hz | 38,001 Hz |
| header mark / space | 4474 / 4474 | 4472 / 4478 | 4474 / 4474 |
| frame plus gap | 108,508 us | 108,494 us | 108,504 us |

Across the 64 bit cells of the frame the largest disagreement between the 525
and the LearnIR was **21 us**, on cells that are nominally 560 us. Both decode
to the same Samsung32 payload, `07 07 02 FD`.

So a 525 is within a few per cent of a dedicated analyser, which is far inside
what any decoder cares about. What it is not is precise about carrier: the two
receivers disagree by 0.6% here and by 1.1% on a Panasonic elsewhere, so match
on timings and treat carrier as approximate.

## Scope

Concordance's `SupportedModels.md` marks Learn IR as working for architecture 9,
which is the 36x, 51x, 52x and 55x families. Other architectures are listed
there too; only arch 9 has been exercised here.

Danny Bloemendaal independently established in the 525 firmware that `0x70` and
`0x80` bracket capture and that the firmware pushes samples through the same
double-buffered unsolicited stream as the later architectures. See
`docs/FORMAT.md` section 5m for what this repository does with the result, and
`tools/ir_keymap_oracle.py` for matching a capture against a config to find out
which key it is.

## Licence

The transport and the API are Concordance and libconcord, by their
contributors, under GPL-3.0. If you distribute this script together with
libconcord, the packaging has to comply with that licence.

`Unkn0wn-dx/RE-HARMONY` has a fuller Harmony One learner with two-capture
comparison and waveform analysis. It was a reference for what a good one looks
like; no source was copied from it.
