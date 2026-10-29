# quirework.com

The website for Quirework, built by Hugo from this directory.

## Preview

    hugo server        # live preview at http://localhost:1313

## Build

    hugo               # production build into public/

The only requirement is Hugo (extended), packaged on Fedora as `hugo`.
The build reads the example book's cover and figure from
`../examples/side-quest`, so it runs in a full checkout of the
repository.

## Refresh what the site shows

Every listing and screenshot on the site is captured from the examples.
After a change to what a command prints, or to an example:

    scripts/capture-listings          # command output, into assets/listings/
    scripts/capture-images --build    # Side Quest's editions, into assets/images/

`capture-listings` needs Quirework, from the checkout's `.venv` or on
`PATH`. `capture-images` needs Quarto, Google Chrome, Poppler and
ImageMagick; without `--build` it uses the book's last render.

## The sample book's renders

The site serves Side Quest in every edition and format, at
`/side-quest/`. The renders are never committed: each deploy makes them
fresh. To see them in a local preview, render the book and stage it:

    (cd ../examples/side-quest && quirework publish build)
    scripts/stage-renders             # into renders/, served at /side-quest/

`stage-renders` adds a notice to every page that the book is fiction,
and refuses to run if an edition or a format is missing. Without it, a
local build simply has no `/side-quest/`.

## Deploy

A Cloudflare Worker serving static assets, deployed by the GitHub
Actions workflow `.github/workflows/site.yml`. A release deploys it: the
release workflow calls it for every `v*` tag, before publishing to PyPI.
Between releases it deploys `main` when run by hand from the Actions
tab, and a hand run from any other branch builds without deploying. A
push runs nothing. Its build job renders the book with the Quirework of
the same commit, stages the renders, builds the site and checks that
every edition is in it. Its deploy job runs `wrangler deploy` on that
build.

The deploy needs two secrets in the repository's `production`
environment, which only `main` and `v*` tags may deploy from:

- `CLOUDFLARE_API_TOKEN`, a token allowed to edit Workers scripts and
  their routes on the account;
- `CLOUDFLARE_ACCOUNT_ID`, the account it deploys to.

The Worker is `quirework-site`. Everything else, the custom domain
included, is in `wrangler.jsonc`.
