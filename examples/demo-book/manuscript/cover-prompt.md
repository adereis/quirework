# Cover art brief

For handing to an image model. This is a brief for a new illustration,
not a description of `cover.svg`; keep that file, this is a second,
better cover to sit beside it.

## What the image is for

A book cover, portrait orientation (2:3, e.g. 1600×2400), for both an
HTML landing page and an EPUB. It must read clearly as a small
thumbnail, not only at full size. Leave the top third (or one full side)
as calm, low-detail space: the title, subtitle and author name are added
afterward in layout, on top of the art, not by the model.

## The prompt

> A single homemade contraption sits on an otherwise empty writing
> desk, built from ordinary household objects rather than any
> recognizable computer or electronic part: a bicycle wheel, a
> colander, a few pulleys, rubber bands, a funnel. Into one side feeds
> a tangled, chaotic snarl of thread. Out the other side, the
> contraption produces a short, neat row of small tags or cards,
> evenly spaced, hanging in a straight line like laundry on a line.
> The machine looks improvised and a little precarious, clearly built
> by hand from parts that did not start out as a machine, and clearly
> not finished. Style: warm, dry, gently comic line illustration,
> closer to a New Yorker cartoon or a patent-drawing diagram than a
> cartoon strip or a photo. No slapstick, no motion lines, no
> exaggerated expressions. Wide, calm negative space above and around
> the machine. Warm cream paper background. The illustration is
> mostly monochrome dark ink linework; the only two colors allowed are
> a muted rust-orange on the tangled input thread and a muted teal on
> the neat output tags, so the eye reads chaos-in, order-out at a
> glance without needing to read anything.

## Mood

Dry and quietly absurd, not zany. The joke is that the machine is
obviously improvised and obviously still a little broken, not that
anything is exploding or flying apart. Fond rather than mocking: this is
what it looks like when someone would rather build a tool than finish an
argument with themselves.

## Palette

Warm cream background (`#f7f4ee` if the model takes hex). Ink-dark line
art (`#1a1a1a`) for the machine, the desk, and everything that is not
the chaos or the order. One warm rust-orange (`#b5482f`) for the tangled
input thread, and one muted teal (`#2a6f6f`) for the neat output tags.
No fifth color. These match the palette the book's own interior figures
use, so the cover and the inside plate feel like one hand drew them even
though they were not.

## What must NOT be in it

- No lettering, no words, no numbers, no readable labels on the tags.
  The model is bad at type; asking for blank tags and adding a title in
  layout will always beat asking for legible ones.
- No em-dash, no punctuation mark of any kind, drawn as a graphic
  element. That joke already belongs to `cover.svg`; this cover earns
  its place a different way.
- No laptop, phone, screen, keyboard, circuit board, or binary code. The
  story is about a writer, not about software; the machine should look
  like it came from a workshop, not a data center.
- No human face in close-up. A distant or partial figure (a hand, a
  shoulder, a silhouette) is fine if it helps the composition; a clear
  portrait is not, since nobody in this book is a real person.
- No photorealism and no 3D render look. Flat or lightly shaded line
  illustration only.

## What it needs to say to someone who has not opened the book

This is a comedy about a man who could not stop his own book from
arguing with itself, so he built a machine instead. The reader should be
able to look at the cover for two seconds and get "someone built a
strange, homemade device to turn a mess into something orderly," and
nothing more specific than that. The plot, the tool's name, and the joke
about punctuation are all still worth discovering on the page.
