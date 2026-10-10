# Worked examples

A project in here consumes Quirework; it is not part of it. It supplies
content, configuration, procedures and editorial rules of its own, and
nothing in it is imported by the tool.

## side-quest

`side-quest` is a complete short book: a comic memoir in a preface and
nine chapters, a spec of what it commits to and the trail of how each
commitment got there, a sidecar for every prose file, one figure in two
inks, a painted cover, its own procedures and readers, an installed
package of its own house rules, three derived editions bound to the same
spec, and the fifteen sessions it was written in. It also keeps the
records of the workshop, writers room, blind read, roundtable and
consistency review that made parts of it.

It is fiction, about a documentation engineer who builds Quirework
instead of finishing a book, and it was written by an AI playing that
author, which its README says. Run it:

```bash
cd examples/side-quest
quirework spec notes --primer                    # what a session reads first
quirework spec trail the-name                    # one commitment's history
quirework spec notes bind --slug the-name        # the same commitment in every edition
quirework spec units --check                     # the files and the reading order agree
quirework publish build                          # the book and its three editions
```

Its own gates need its own package, which nothing else here installs:

```bash
pip install -e examples/side-quest/sq_checks
quirework dev check                              # no em dash, two inks
```

## demo-site

`demo-site` is a small blog built by Hugo: two posts in page bundles,
one of them a draft, a spec of its own for each post and a shared one
for what they both lean on, marks kept outside the tree Hugo renders,
and a voice policy that fails the check on an em-dash. A third post has
a spec and no text yet. It is fiction, the log of a lighthouse keeper,
and its README lists the commands a companion runs on it.

## Why they are in this repository

Each is documentation and a test at once.
`tests/test_example_side_quest.py` and `tests/test_example_site.py` run
these gates on every test run, so a change to Quirework that would break
a working project fails here rather than in somebody's book or site.

Writing them is also what found the bugs they were written to find. A
directory named `docs`, a font stack read as a single family, a figure
caption that wrapped across two lines, a nested project collecting the
sessions of the repository around it, a catalog page generated off the
wrapper's fixed point, a seat refused for login files its harness no
longer writes: every one of those passed a reading of the code and
failed the first project that was not the one the code grew up in.
