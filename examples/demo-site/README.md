# The Lighthouse Log

A small fictional blog, kept by the keeper of the Gull Point light, and
a worked example of Quirework on a site rather than a book. Hugo renders
it; Quirework keeps what each post commits to and checks the prose.

The keeper is invented, and AI wrote the posts. No person edited or
reviewed them. The blog exists to test Quirework and to document it, so
read it for the structure the tool keeps, not as an example of the
writing Quirework helps an author make.

```text
content/posts/<slug>/index.md   the posts, as Hugo page bundles
specs/_site.md                  what several posts lean on
specs/<slug>/spec.md            what one post commits to
specs/<slug>/marks.md           that post's marks, mirroring its headings
research/sources.md             what the posts stand on, cited by slug
research/copies/<slug>.txt      a capture of a source, to check quotes
ideas/<name>/brief.md           a post that might be, with no authority
quirework/terms.toml            the voice rules `text check` enforces
procedures/                     how a companion writes a post
```

One post is published and one is a draft. A third has a spec and no post
yet, and an idea has a brief and no spec; `spec units` lists both. The
published post still carries a to-do, which `spec units --check`
reports, and it quotes the coast pilot, which `spec sources --check`
holds to the captured copy.

```bash
cd examples/demo-site
quirework spec units                                  # posts, planned posts, ideas
quirework spec notes --primer -c why-the-lamp-turns   # one post's spec and marks
quirework spec notes bind --slug recognizable-signal  # posts sharing a claim
quirework spec notes --check                          # marks mirror, slugs resolve
quirework spec units --check                          # what a published post owes
quirework spec sources --check                        # citations and quotations
quirework text wrap --check content/posts/*/index.md  # one sentence per line
quirework text check                                  # no em-dash came back
quirework publish build --drafts                      # Hugo, into build/site
```

The last command needs Hugo installed. The site has no theme, so Hugo
warns that it found no layouts; the build still succeeds.
