# SOP fragment: what every edition of Side Quest keeps

Side Quest has three derived editions: a six-pager for Hedda, a young
readers' edition, and the book as poems. Each is made from the book and
answers to the same spec, `manuscript/sections/_spec.md`. The book is
the master. An edition never feeds back into it, and nothing an edition
needs is a reason to change a chapter.

Read `AGENTS.md` first. The cast, their pronouns and the house rules
hold in every edition exactly as they hold in the book.

## Drafting

`quirework publish variant build KIND` drafts the edition blind, into
`variants/KIND/_raw/`, with the drafters
`procedures/variant-policy.toml` names. A drafter reads the whole book
with every mark stripped and is handed the two briefs below: this
file's, then the kind's own. Nothing in `_raw/` is ever edited; it is
what a model wrote.

No edition has a back cover: a back cover belongs to a book you can pick
up. The tool plans a seat for every unit with prose, the back cover
included, and has no way to leave one out. So a chapter-shaped edition
is built with `--plan-only`, the `draft-back-cover` seat is taken out of
the plan it writes, and the plan is then prepared, started and imported
with the commands the build prints.

## Composing

Read each draft against the spec, section by section, and against the
chapter it retells. A draft that adds a scene, a line of dialogue, a
fact or a feeling the book does not hold, settles a doubt the book
leaves open, rounds a number, or gives Otto or Em a pronoun has failed,
however well it reads. Fix it in the composed text, or redraft.

Write the composed documents beside `_raw/`, at the edition's top level,
with a `_quarto.yml` of the edition's own. Each document gets a sidecar,
`_marks/STEM.md`, whose headings copy the document's verbatim, as a
chapter's do. Under each heading, `@bind` every spec section that part
carries, and `@omit` every section the edition leaves out, with the
reason, so the whole spec is accounted for:

```markdown
<!-- @bind(people-who-ask) -->
<!-- @omit(em-dashes-off-the-page) A memo has no room to explain a promise it keeps without saying. -->
```

Then run the gates: `quirework spec notes --check`, which lists any
section the canon binds that the edition neither binds nor omits;
`quirework spec notes bind --slug SLUG` for any section in doubt; and
`quirework dev check`, which holds the composed editions to the house
rules as it holds the book. `quirework publish build --edition KIND`
renders the edition into `build/variants/KIND/book/_site/`.

## Blind-draft brief

These rules hold in every edition of this book, whatever its reader or
form.

Invent nothing. Every scene, line of dialogue, fact, number, date,
feeling and outcome in your text must be in the book. Where the book is
silent, you are silent. Do not supply what someone thought, what
happened next, or what a thing was called, if the book does not say.

Keep the facts exact. Names, numbers, days of the week, months, times of
day, counts and quotations are the book's own, unrounded and unchanged.
A line the book quotes is quoted as the book gives it, word for word,
with its spelling and capitals.

Doubts stay doubts. What the book leaves unknown, unexplained or unasked
stays that way. Simpler is allowed; more certain is not.

Nobody is a villain, the assistant and the colleague included. Add no
moral and no lesson the book does not draw.

Pronouns: the narrator is "I". Hedda is she, Red is he. Otto never gets
a pronoun of any kind, and is never "it": write "Otto" every time,
however often that is. Em never gets one either: write "Em" every time.

Write no em dash, anywhere, and no three hyphens in a row, which print
as one. Use a colon, a comma, or two sentences. Em's messages are
counted, never quoted with their punctuation.

Dry and concrete. Never explain a joke the book leaves for the reader.
Short: if a sentence can go, it goes. US spelling.
