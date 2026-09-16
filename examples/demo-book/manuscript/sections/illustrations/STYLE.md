# The book's visual language

Read this before drawing anything. It exists to be checked, not admired.

## Canvas

Every figure is `viewBox="0 0 960 540"`, exactly, no other size. The
first element inside `<svg>` is a full-canvas `<rect>` filled paper
(below), and nothing else ever uses that fill.

## Palette

Four colors, each with one job. A figure never uses a fifth, and never
uses one of these four for a job that is not its own.

| Name       | Hex       | Job                                              |
|------------|-----------|---------------------------------------------------|
| paper      | `#f7f4ee` | the canvas background, and only the background     |
| ink        | `#1a1a1a` | the chapter itself: structure, the permanent thing |
| marginalia | `#2a6f6f` | a note, a mark, a sidecar: the attached apparatus  |
| strike     | `#b5482f` | one crossed-out or forbidden thing, never more than once per figure |

Colors are always written as six-digit hex (`#rrggbb`), never a named
color and never `rgb()`. The grayscale edition is generated from these
files automatically, by reading exactly that pattern; a color spelled
any other way passes straight through unconverted and nothing catches
it.

## Line weight

Two stroke widths, nothing between them. `10` for a body: the bike, the
sidecar, the chapter, anything that is a shape in its own right,
whatever color it is drawn in. `6` for a connection: a strut, a join,
anything that attaches one body to another.

## Type

One size: `font-size="22"`, `font-family="Liberation Serif"`. No figure
sets a second size. A label is sentence case, one to three words, and
carries no terminal punctuation. A figure holds at most four labels;
past that, it is two figures.

## The punctuation treaty extends here

A label is prose. It contains no em-dash, ever, for the same reason the
chapters don't: a colon where the second word names the first, a comma
for a looser join, or a second label instead of a longer one.

## Grayscale edition

`quirework book figures build` derives the gray gallery from these exact
hex values; nothing is drawn twice. Two colors that read apart by hue
alone (marginalia against strike, here) still have to read apart by
weight or position once hue is gone, because the e-ink edition keeps
only weight and position.
