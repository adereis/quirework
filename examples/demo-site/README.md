# The Lighthouse Log

A small fictional blog, kept by the keeper of the Gull Point light, and
a worked example of Quirework on a site rather than a book. Hugo renders
it; Quirework keeps what each post commits to and checks the prose.

```text
content/posts/<slug>/index.md   the posts, as Hugo page bundles
specs/_site.md                  what several posts lean on
specs/<slug>/spec.md            what one post commits to
specs/<slug>/marks.md           that post's marks, mirroring its headings
quirework/terms.toml            the voice rules `text check` enforces
procedures/                     how a companion writes a post
```

One post is published and one is a draft. A third has a spec and no post
yet, which `spec notes --check` reports as an advisory.

```bash
cd examples/demo-site
quirework spec units                                  # posts by date, drafts marked
quirework spec notes --primer -c why-the-lamp-turns   # one post's spec and marks
quirework spec notes bind --slug recognizable-signal  # posts sharing a claim
quirework spec notes --check                          # marks mirror, slugs resolve
quirework text wrap --check content/posts/*/index.md  # one sentence per line
quirework text check                                  # no em-dash came back
quirework publish build --drafts                      # Hugo, into build/site
```

The last command needs Hugo installed. The site has no theme, so Hugo
warns that it found no layouts; the build still succeeds.
