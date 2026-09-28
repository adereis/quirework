# Write a post

<!-- @skill: Draft or revise one post of the Lighthouse Log against its
     spec. -->

Read `quirework spec notes --primer -c SLUG` before touching the post.
It lists the post's own spec, the shared commitments the post binds, and
the marks in its sidecar. The spec says what the post holds. The post is
how it says it.

A post that is not written yet may already have a spec. Draft against
it. A new post gets its spec first, in `specs/SLUG/spec.md`, and its
marks in `specs/SLUG/marks.md` once it has headings to mirror.

Before changing a shared commitment, run
`quirework spec notes bind --slug SLUG`. Every post it lists has to be
read again, because a claim one post changes is a claim another post now
contradicts.

Write one sentence per line and never an em-dash. Then run, in order:

1. `quirework text wrap --check content/posts/SLUG/index.md`
2. `quirework text check`
3. `quirework spec units --check`
4. `quirework spec notes --check`
5. `quirework publish build --drafts`

Report what changed, which commitments it touched, and anything left for
the keeper to decide.
