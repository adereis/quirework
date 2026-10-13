# Quirework's site: contributor guide

The website at quirework.com, for an author deciding whether to write
with Quirework. It is a Hugo site with project-level layouts and one
plain stylesheet, `assets/css/main.css`. It has no theme, no Sass and no
Node, so it builds with the Hugo binary alone.

## Where it sits

The site describes the tool, so it is a consumer of it, like the
examples. Nothing under `src/` reads it, and its commits stay apart from
the tool's and from the examples' (the repository's `AGENTS.md`, "Git").

It never says more than the guides do. A page explains an idea once and
links the guide that develops it, with a `repo:` link (below). When a
feature the site describes changes, change the page in the same series
of commits.

## What it may say about mindwright

The home page names one project written with Quirework, the book at
mindwright.dev. It may say what that site says publicly:

- that a book by Ademar Reis, with the subtitle *A software engineer's
  philosophy of mind*, is due in early 2027;
- the page's paragraph on how the book is being made, quoted word for
  word;
- the link to mindwright.dev.

It may also say two things of its own. The book is being written with
Quirework. It is not published yet, and once it is, it will be the
measure of the tool, which the samples are not. Both are claims about
Quirework, made by its maintainer, and neither describes the book.

It says nothing else about the book:

- **no title and no cover**, since mindwright.dev shows neither.
  "mindwright" is the site's name, never the book's, so it is never set
  in italics as a title.
- **nothing about how the book uses Quirework**, beyond that it is
  written with it. A connection drawn here between the book and one of
  the tool's features is read as a fact about the book, and only
  mindwright.dev may state one.

When mindwright.dev's page changes, this section changes with it.
Nothing links the two but this rule.

## Models and harnesses

The copy speaks of seats from different makers and harnesses, and of a
person, and names no product. The harnesses and models Quirework drives
today are implementation details that change. Captured output may name
one where an example's own text does.

## The tool first, the samples last

The site is about the tool. Side Quest and the Lighthouse Log are
fiction that AI wrote to test it, with prose no person reviewed, and
they never lead.

- **The samples are fixtures, never showcases.** A visitor judges a tool
  by the best work it shows, and nobody judged a page of these. So no
  copy praises their prose, and no caption calls an edition good. A
  caption says how an edition was made, never how well it reads.
- **Wherever a reader reaches a sample, it says who wrote it.** Each
  mention says that AI wrote it and that no person reviewed its prose.
  The home page also says that the book at mindwright.dev, once
  published, will be the measure of the tool.
- **The home page reaches them only in their own section**, near its
  end, after the spec, the trail, the procedures and the book written
  with Quirework. Every section before it explains the tool in general
  terms and draws it with general diagrams.
- **The home page shows no picture of a sample.** The cover lives on the
  examples page, inside the explanation. On the home page it would be a
  showcase, whatever the copy beside it says.
- **A deeper page opens on the tool too.** Where it shows the samples'
  output, it says once that they are fiction that AI wrote and no person
  reviewed, and every caption names the sample book as the sample book.
- **What a sample did is set apart** with `{{% sample %}}`, a dotted
  rule beside text that itself begins "In the sample book". The style
  marks it, and the words say it, since a style alone does not reach
  every reader.
- **The sample's own story, figure and diagrams live on the examples
  page.**

## What is drawn, and what is captured

- **Diagrams of the mechanism are general**: `commitment.svg`,
  `trail-general.svg`, `seeing.svg`, `loop.svg`, `seats.svg` and
  `provenance.svg` show no example's content, and say what Quirework
  does to any work.
- **Diagrams of a sample draw its own data**: `bind.svg` and
  `bind-narrow.svg` show Side Quest's four bindings and its review
  finding, and `trail.svg` its six breadcrumbs. A change to `the-name`
  in Side Quest is a change to all three.
- **Listings** are command output captured from the examples by
  `scripts/capture-listings` into `assets/listings/`. They are never
  edited by hand. `{{< listing name="..." lines="1-14,21-24" >}}` shows
  an excerpt and marks every gap, so an excerpt never passes for the
  whole output.
- **Screenshots** come from a render of Side Quest, made by
  `scripts/capture-images`. The cover and the figure are not copied:
  `hugo.toml` mounts them from the book, so the site shows what the book
  ships.

After a change to what a command prints, or to an example, run both
scripts and review the diff like any other.

## Voice

- No em dashes. Short declarative sentences, plain words.
- "Your companion", never a product name.
- A heading names its content.
- The Markdown sits at the fixed point of `quirework text wrap`, which
  the repository's pre-commit check holds it to. The wrapper passes a
  line that starts with a shortcode through untouched, so every
  shortcode, and every paired shortcode's opening and closing, sits on a
  line of its own.

## Design

**The palette and the text face are mindwright.dev's**, so the tool and
the book being written with it read as one author's work: `--paper`,
`--paper-2`, `--ink`, `--ink-soft`, `--rule` and the Spectral files in
`static/fonts/`, copied by hand. `OFL.txt` ships beside the fonts
because redistributing them requires it.

**`--signal` means one thing**: Quirework caught this. It is the orange
of Side Quest's cover, used only in a diagram, where a finding is
marked, and always with a label beside it, since color alone does not
reach every reader. A link, a heading or a button never uses it.

**The page has a margin**, the way a Quirework project keeps its notes
beside the prose. `{{% aside %}}` puts a note there, beside the block
after it. A diagram, a listing, a gallery or a table spans the column
and the margin. Below 64rem a note falls into the column.

**No inline styles or scripts.** `static/_headers` sends a Content
Security Policy that allows only the site's own files and Cloudflare's
analytics beacon. A diagram is inline SVG styled by the `dg-` classes in
the stylesheet, with no `<style>` element and no `style=` attribute.
Presentation attributes such as `font-weight` are allowed, but a class
rule outranks them, so a stroke width that differs gets a class of its
own.

**Diagrams stay legible.** On a phone a wide diagram keeps a minimum
width and scrolls inside its frame, with a shade on the edge while there
is more to see. A diagram that carries a section has an upright variant,
named with `narrow="..."`, which a phone shows instead.

**One moment of motion**: the circle on the home page's first diagram
(`class="draw-on-view"`) draws itself as the diagram scrolls into view.
A browser without scroll-driven animation shows it already drawn, and so
does one whose reader asked for reduced motion.

**The favicon is `static/favicon.svg`**: Spectral Bold's "q" as an
outline, paper on ink, since a browser draws an SVG icon without the
page's fonts. After changing it, regenerate the others:

    cd static
    rsvg-convert -w 180 -h 180 favicon.svg -o apple-touch-icon.png
    rsvg-convert -w 16 -h 16 favicon.svg -o ~/tmp/fav-16.png
    rsvg-convert -w 32 -h 32 favicon.svg -o ~/tmp/fav-32.png
    magick ~/tmp/fav-16.png ~/tmp/fav-32.png favicon.ico

## Links into the repository

A link written `repo:docs/workflows.md` opens that file on GitHub, and
`repo:examples/side-quest/` opens a directory
(`layouts/_default/_markup/render-link.html`). The repository's address
is written once, in `hugo.toml`.

## The sample book's renders

The site serves Side Quest's renders at `/side-quest/`: the book and its
three editions, each in the browser and as downloads. They are rendered
at every deploy by `quirework publish build` and never committed, so the
site never serves a render older than the book, or than the tool.

- **`scripts/stage-renders` is the only way in.** It copies each edition
  into `renders/`, which `hugo.toml` mounts at `/side-quest/`, and
  refuses to run if any edition or format is missing. It names each
  download for its edition, so that two never land in one folder as the
  same file.
- **Every page says the book is a test fixture**, which AI wrote and no
  person reviewed. A reader can land on any chapter from anywhere, so
  the notice goes on each page, and the robots tag and an `X-Robots-Tag`
  header keep the pages out of search results. The PDF, EPUB and Word
  files are the renders as made. They carry the same message in the
  book's own note, on an unlisted page before the preface, which the
  book's `AGENTS.md` describes.
- **The pages run under a looser policy of their own.** Quarto's pages
  need inline scripts and styles, so `static/_headers` drops the site's
  Content Security Policy for `/side-quest/` and sets one that allows
  them, still from this site's own files only. The downloads carry no
  policy at all. A rule that drops a header and sets it again serves
  only the new value: that was tested with `wrangler dev`, and is worth
  checking with `curl -I` after a deploy that changes `_headers`.
- **The book's pages fetch nothing from anyone else.** Its HTML theme's
  typeface ships with the book (its `AGENTS.md`), which is why the
  policy for `/side-quest/` needs no font service.

## Build and deploy

See `README.md`. The site is a Cloudflare Worker with static assets and
no script, configured in `wrangler.jsonc`, and deployed by the GitHub
Actions workflow `.github/workflows/site.yml`. A local build needs
nothing but Hugo; rendering the book needs what Quirework's install
guide lists for Quarto.

The workflow has two jobs so that nothing installed to render the book
holds the Cloudflare token. The build job installs Quirework, Quarto and
the system tools, renders, stages and builds, and uploads the site. The
deploy job holds the token and runs only Wrangler on that upload.

Every pin in it is deliberate:

- **Actions are pinned by commit**, with the release in a comment. A tag
  can be moved; a commit cannot.
- **Hugo by version and checksum.** Keep `HUGO_VERSION` in step with the
  local Hugo, and take the checksum from the release's checksums file.
- **Quarto by exact version**, the one authors render with, since an
  unpinned install takes the newest release and the renders change under
  it.
- **Wrangler by exact version**, because a bare `npx wrangler` fetches
  the newest release on every deploy, and that code runs with the right
  to publish the site.

Bump a pin on purpose, and watch the next run. A pin taken newer than a
couple of weeks has had little time to be vetted.

The workflow runs on a push that touches the site, the sample book or
the tool, since each can change what the site serves.

## Set outside this repository

Several parts of the live site are set in Cloudflare's dashboard or at
the search engines, not here.

- **The deploy token** is an account API token named "quirework-site
  deploy (GitHub Actions, trimmed)" with three permissions and nothing
  else: Workers Scripts Write and Account Settings Read on the account,
  and Workers Routes Write on the quirework.com zone only. That is what
  `wrangler deploy` of an assets-only Worker with a custom domain uses,
  as a full deploy proved. It never expires. It and the account id are
  the secrets of the repository's `production` environment, which only
  `main` may deploy from. To roll it, create a new token with the same
  three permissions, replace `CLOUDFLARE_API_TOKEN` there, run the
  workflow, and delete the old token only after that deploy succeeds.
- **The Worker has no `workers.dev` address and no previews**, as
  `wrangler.jsonc` declares, so quirework.com is its only address.
- **Web Analytics** runs with automatic setup. Cloudflare adds its
  beacon script to the HTML a browser receives, so production pages
  carry a script that no layout contains, and that neither `hugo server`
  nor `curl` shows. It sets no cookies, so the site needs no consent
  banner. The policies in `static/_headers` allow it.
- **`www` redirects to the apex** through a Redirect Rule, a 301 that
  keeps the path and query string. Its DNS record points at 192.0.2.1, a
  documentation address that is never reached, because the rule answers
  first.
- **Always Use HTTPS** is on for the zone, and `static/_headers` sends
  HSTS for a year on top of it.
- **DNSSEC** is on. The domain is registered with Cloudflare, which
  keeps a fingerprint (DS record) of its signing key at the registry.
  **Before moving the domain's DNS away from Cloudflare, turn DNSSEC off
  and wait at least a day before changing nameservers.** If the
  fingerprint is still at the registry when another provider starts
  answering, validating resolvers reject every answer, and the site
  stops resolving for most people.
- **Google Search Console** verifies the domain through a TXT record at
  the apex (`google-site-verification=...`). Deleting it unverifies the
  property.
- **Bing Webmaster Tools** verifies the site through a DNS-only CNAME
  from a hex name to `verify.bing.com`. Deleting it unverifies the site.
  Both search engines have the sitemap,
  `https://quirework.com/sitemap.xml`, which lists only the site's own
  pages, since the sample book is `noindex`.
