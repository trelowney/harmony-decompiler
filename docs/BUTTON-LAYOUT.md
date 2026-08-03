# Harmony 525 - buttons, as documented by Logitech

Extracted from page 5 of the official *Harmony 525 User Manual*, which is titled
"The buttons on your Harmony 525". The manual is not redistributed here; at the
time of writing Logitech's documentation server is still serving it:

```
https://images.harmonyremote.com/EasyZapper/Downloads/UserManual/525/enu/525_UserManual.pdf
```

The diagram on that page is **vector art, not a bitmap**, so the labels are real
text with coordinates and can be recovered mechanically rather than read off a
picture. Reproduce with:

```sh
python tools/manual_layout.py 525_UserManual.pdf 5
```

Every other page of the manual was checked; page 5 is the only one carrying a
labelled diagram.

> **Read this alongside [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md).** This document
> gives the buttons a human sees. [FORMAT.md §5g](FORMAT.md) gives the 8x7 matrix
> the config addresses. **Joining those two is the unsolved problem**, and nothing
> below solves it - a button being named "Guide" here does not tell you which
> matrix cell it is wired to.

---

## Buttons Logitech names explicitly

Page 5 pairs each of these with a description, which makes the naming
authoritative rather than inferred.

| button | what the manual says it does |
|---|---|
| **Off** | turns off every device in the current Activity |
| **Activities** | returns to the Activities starting point |
| **Devices** | (present in the diagram; described elsewhere in the manual) |
| **Help** | starts the on-remote help |
| **Menu** | opens the on-screen menu for the selected device |
| **Info** | opens the information section of the on-screen guide |
| **Exit** | closes the on-screen menu or guide |
| **Guide** | opens the on-screen guide |
| **Glow** | lights the buttons and the screen |
| **Vol** | changes the volume |
| **Ch** | changes the channel |
| **Mute** | mutes the sound |
| **OK** | centre of the navigation pad |
| navigation pad arrows | move through menus and on-screen guides |
| screen paging arrows | page through items on the touch screen |
| previous channel | returns to the last channel |
| **Play, Pause, Stop, Rec, Rew, Fwd, Skip, Replay** | transport controls, described as "the play area" |
| **0-9, `*` (clear), `#` (enter)** | the number pad |

Two details worth keeping, because they affect the count:

- The manual says "the previous channel **buttons**", plural, while showing one
  label. Either a typo or there is more than one.
- Volume and channel are each described in the singular, as one button, but a
  rocker is electrically **two** switches and so occupies two matrix cells.

## Approximate physical layout

Rows recovered from the y-coordinates of the diagram, top to bottom. Horizontal
positions are only roughly recoverable: the PDF merges adjacent labels into
single text runs - `1 2 3` and `Stop Replay Skip Play` each come out as one
fragment - so the per-key x is lost even though the row structure is solid.

```
        Off        Activities
        Devices              Help

        ┌───────────────────────────┐
        │        LCD screen         │      96 x 64 px per the manual
        └───────────────────────────┘

        Stop   Replay   Skip   Play
        Rec    Rew      Fwd    Pause

        Menu   Info
        Exit   Guide

          [+]                 [+]
         rocker    OK       rocker         labelled Vol and Ch
          [-]                 [-]

                 Glow

         1   2   3
         4   5   6
         7   8   9
         *   0   #                         clear / enter
```

Mute and previous-channel appear in the manual's callout column but not in the
diagram itself, so their position is not established here.

Which rocker is volume and which is channel is **not** resolved by the
extraction: the label fragment comes out merged as `Vol Ch` on the left of the
pad, while the `+` and `-` glyphs that survive individually sit on the right.

## How this squares with the 50 matrix cells

The config gives **50 occupied cells** in an 8x7 matrix
([FORMAT.md §5g](FORMAT.md)). Counting what the manual documents:

| group | count |
|---|---|
| Off, Activities, Devices, Help, Glow | 5 |
| Menu, Info, Exit, Guide | 4 |
| navigation pad + OK | 5 |
| Vol +/-, Ch +/- | 4 |
| Mute, previous channel | 2 |
| Play, Pause, Stop, Rec, Rew, Fwd, Skip, Replay | 8 |
| 0-9, `*`, `#` | 12 |
| **firmly documented** | **40** |

Plus, mentioned but never counted: the LCD paging arrows, the side LCD buttons,
and the coloured teletext buttons (page 9). Ten cells of headroom is a plausible
home for those, but the manual does not state their number, so **the remaining 10
are inference, not evidence**. Do not treat 40 + 10 = 50 as a confirmation of
anything.

The manual also does **not** state a total button count anywhere, including in
`Appendix C - Product Specification` on page 35. A number circulating elsewhere
claims "50 buttons", but the same source claims an 84 x 84 px LCD where the
manual says 96 x 64, so it is not reliable.

## Why this still does not give the mapping

The order of codes in the config is grouped by matrix row, but columns are
permuted differently in each pair of rows, so the table order is Logitech's
canonical key ordering rather than anything visual. Knowing that a remote has a
Guide button, and knowing that cell (r3, c5) is occupied, does not connect them.

What would: a service manual or schematic, a photo of a bare PCB, or somebody
buzzing out a matrix with a multimeter. See
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) §1.
