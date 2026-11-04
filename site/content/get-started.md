---
title: Get started
seo_title: Set up AI writing and research in your project
description: >-
  Install Quirework in a book or site repository, connect your terminal AI
  companion, and begin with a bounded writing or research task.
lede: >-
  Start with your project and the AI companion you already use in a
  terminal. Quirework gives that collaboration a memory and a method.
---

## What you need

You need Git, Python 3.11 or later, and an AI companion that can read
files and run commands in your project's repository. Quirework supplies
the tools. Your companion operates them. You judge the writing and make
the decisions.

A book can use Quarto to render its editions. A site can keep its own
publisher, such as Hugo. The
[installation guide](repo:README.md#install) lists the rendering
dependencies for each format.

## Install in your project

Quirework is under heavy development. Its commands and settings still
change between releases, and a new project can find rough edges in it.
So install it inside your project, as a checkout your companion can fix
or improve while it works.

{{% aside %}}
The project pins the revision of Quirework it runs. A fresh clone gets
the same tool, and moving to a newer one is a commit of its own.
{{% /aside %}}

Quirework needs Python 3.11 or later. In your book's or site's
repository, add it as a submodule and install it from there:

```
git submodule add https://github.com/adereis/quirework.git vendor/quirework
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e 'vendor/quirework[quarto]'
```

The `quarto` extra installs what a book needs to render; leave it out
for a site. Then open your companion there and ask it to start a book,
or a site, by the [authoring guide](repo:docs/authoring.md). The
[configuration guide](repo:docs/configuration.md) connects a project you
already have.

The released package is on PyPI as `quirework`, for running the commands
yourself or trying the tool out. The
[install instructions](repo:README.md#install) cover both, and a clone
for working on Quirework itself.

## Give your companion a first job

For a new project, open your companion in the repository and ask:

> Help me set up this writing project with Quirework. Read
> vendor/quirework/docs/authoring.md first. Ask me what the work is for
> and what it needs to stay true to before drafting its spec.

For an existing project, start with the
[configuration guide](repo:docs/configuration.md). Keep the first task
small: one chapter or post, one question, and a change you can judge.
The [workflow guide](repo:docs/workflows.md) explains the commands your
companion uses.

## Know what to review

The [spec and trail](/spec-and-trail/) keep the work's commitments and
the decisions behind them. [Procedures](/procedures/) set the method for
a draft, a blind read or an independent review. Checks catch structural
errors. Editorial judgment stays with you.

Ask your companion to return the proposed change, the checks it ran, and
the decisions that need your attention. Start the next task after you
have read what came back.
