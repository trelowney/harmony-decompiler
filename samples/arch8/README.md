# Arch 8 samples (Harmony 720/785/88x)

Thirteen configs from two people. All thirteen decompile and recompile byte for
byte, and all thirteen internal checksums verify. If either of them would rather
their files were not mirrored here, say so and they come straight back out.

## The four `Update*.EZHex`, from @guyman70718

Shared by **[@guyman70718](https://github.com/guyman70718)** in
[jaymzh/concordance#66](https://github.com/jaymzh/concordance/issues/66#issuecomment-2358539103)
on 2025-09-18, as
[EZHex.Samples.zip](https://github.com/user-attachments/files/22412763/EZHex.Samples.zip).

Mirrored here unmodified, with thanks. The originals are GitHub issue
attachments, and this whole project exists because Logitech's hosting for these
remotes went away - [@trn1ty](https://github.com/trn1ty) raised the same concern
in that thread and proposed archiving the remaining EZ files on the Internet
Archive. If anyone would rather their file were not mirrored, say so and it comes
straight back out.

Only the filenames changed, because `Update(2) .EZHex` had a space before the
extension that made it awkward to handle. Contents are byte-identical; verify
with `sha256sum -c SHA256SUMS.txt`.

## What they are

| file | size | protocol | skin | board | flash |
|---|---|---|---|---|---|
| `Update.EZHex` | 447,410 B | 8 | 15 | 1.8.0 | 0x01:0x49 |
| `Update-1.EZHex` | 477,470 B | 8 | 15 | 1.8.0 | 0x01:0x49 |
| `Update-2.EZHex` | 497,555 B | 8 | 15 | 1.8.0 | 0x01:0x49 |
| `Update-3.EZHex` | 499,001 B | 8 | 15 | 1.8.0 | 0x01:0x49 |

All four are architecture 8 - the 720/785/88x family - as against the
Harmony 525 in [`../harmony525/`](../harmony525/), which is architecture 9.
Their magic is `TPTP ... DKDK` rather than `AHCM ... MCHA`, and they are five to six
times larger.

`UserId` reads `0` where it appears, and there is no serial number or account
data in the headers.

**These are probably four configs for one remote**, not four different remotes:
identical board revision, identical flash ID, one contributor. Worth keeping in
mind before concluding that anything identical across all four is identical
across the whole model family.

> That paragraph used to end "a sample from a *second* arch 8 remote would be
> very welcome". It arrived. See below.

## The nine `H88*.EZHex`, from @kkong42

Uploaded by **[@kkong42](https://github.com/kkong42)** on 2026-08-10 across
[issues #18 to #26](https://github.com/trelowney/harmony-decompiler/issues/20),
one per file, each with the "happy to publish" box ticked. Mirrored here
unmodified.

| file | issue | size | protocol | skin | board |
|---|---|---:|---:|---:|---|
| `H880-Bedroom.EZHex` | #18 | 393,040 B | 8 | 15 | 1.8.0 |
| `H885-Bedroom.EZHex` | #19 | 393,040 B | 8 | 15 | 1.8.0 |
| `H880-Bedroom-Spare-1.EZHex` | #21 | 393,040 B | 8 | 15 | 1.8.0 |
| `H880-Bedroom-Spare-2.EZHex` | #22 | 393,040 B | 8 | 15 | 1.8.0 |
| `H880-Bedroom-Spare-3.EZHex` | #23 | 393,040 B | 8 | 15 | 1.8.0 |
| `H885-LivingRoom.EZHex` | #20 | 529,924 B | 8 | 17 | 2.1.0 |
| `H885-LivingRoom-Spare-1.EZHex` | #24 | 529,924 B | 8 | 17 | 2.1.0 |
| `H885-LivingRoom-Spare-2.EZHex` | #25 | 529,924 B | 8 | 17 | 2.1.0 |
| `H885-LivingRoom-Spare-3.EZHex` | #26 | 529,924 B | 8 | 17 | 2.1.0 |

**`H885-Bedroom.EZHex` is an 880, not an 885**, which the uploader spotted
himself. Skin 15, board 1.8.0 and 393,040 bytes put it exactly with the 880
group. The filename is kept as uploaded so it still matches its issue; do not
use it as an 885 baseline.

What these add that the `Update*` four could not:

- **a second owner**, so anything identical across both sets is a property of
  the model and not of one person's habits;
- **two board revisions**, 1.8.0 and 2.1.0, and the 2.1.0 files are a third
  larger at the same protocol;
- **a written record of what one of the remotes actually displays.** For
  `H885-LivingRoom` the uploader also listed every device, activity and custom
  button label, including the blank positions, in
  [issue #20](https://github.com/trelowney/harmony-decompiler/issues/20). That is
  the only sample in this repository where an answer can be checked against what
  the hardware really shows, and the blanks are the useful part: a position
  nobody assigned should have nothing pointing at it.

He also supplied Harmony 880 and 885 firmware dumps, in
[discussion #17](https://github.com/trelowney/harmony-decompiler/discussions/17).
Firmware is not mirrored in this repository, by policy.

## Why they matter

They are the reason several findings in [`../../docs/FORMAT.md`](../../docs/FORMAT.md)
can be stated as general rather than as quirks of one remote:

- **The container is shared across architectures.** Same header shape, same
  pointer table, and `config_base` is `0x20000` on both - `0x092E57 - 470619 + 4
  = 0x20000` for the first sample. Only the magic and the section contents
  differ.
- **Key codes look to be shared across models.** These four files are what
  established that arch 8 carries several key tables, three of which
  (`0x0001EF`, `0x000293`, `0x000A22`) are byte-identical in all four. The
  53-entry table at `0x000A22` has a contiguous target run of 17-69 and shares
  **41 of the 525's 51 codes**, in the same order - which is what suggests
  solving the button mapping for one remote in the family would carry over to
  the others.
- **Three of them were created about ten minutes apart**, which makes them the
  best available evidence for the most important negative result in the project:
  they still differ in 73-84% of their bytes, with the first difference at offset
  `0x000004`. A tiny logical change reshuffles the whole image. See
  [FORMAT.md §5](../../docs/FORMAT.md).

## Using them

The scripts pick this directory up automatically:

```sh
cd ../../tools
python find_keytables.py       # self-tests on the 525, then reports all four
python compare_keytables.py    # arch 9 against arch 8, main tables
python diff_samples.py         # the 73-84% result, reproduced
```
