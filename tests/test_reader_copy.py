"""A seat handed a page gets what a reader of the page gets, and no more.

A blind reader of a site's post was handed its TOML frontmatter (the draft
flag, the tags, the summary the author wrote about the post) and its
shortcodes raw, and a figure written as a shortcode never arrived as a figure.
Every fixture here is fiction.
"""

import pytest

from quirework.delivery import artifacts
from quirework.delivery.text import reader_markdown

POST = """+++
title = 'Why the Lamp Turns'
date = 2031-03-02
draft = true
summary = "A turning light tells a ship which land it is."
tags = ['lights']
+++

The lamp turns once every ten seconds.

{{< figure src="lamp-period.svg" alt="A lamp flashing every ten seconds." caption="The period." >}}

{{< note >}}
A period is the whole cycle, dark included.
{{< /note >}}

Read [the fog signal post]({{< ref "what-the-fog-signal-is-for" >}}) too.

{{< youtube abc123 >}}

Write it literally as `{{</* figure */>}}` in a post.
"""


def test_a_page_s_frontmatter_never_reaches_the_reader():
    text, _ = reader_markdown(POST)
    for secret in ("draft", "summary", "tags", "turning light tells", "+++"):
        assert secret not in text, secret
    assert text.startswith("The lamp turns once every ten seconds.")


def test_yaml_frontmatter_is_dropped_too():
    text, _ = reader_markdown('---\ntitle: "Fog"\ndraft: true\n---\n\nFog bends.\n')
    assert text == "Fog bends.\n"


def test_a_figure_shortcode_is_a_figure():
    text, figures = reader_markdown(POST)
    assert figures == [
        {
            "source": "lamp-period.svg",
            "file": "figures/lamp-period.png",
            "caption": "The period.",
            "alt": "A lamp flashing every ten seconds.",
        }
    ]
    assert "![The period.](../figures/lamp-period.png)" in text
    assert "*Figure description:* A lamp flashing every ten seconds." in text


def test_shortcodes_keep_what_a_reader_reads():
    text, _ = reader_markdown(POST)
    assert "A period is the whole cycle, dark included." in text
    assert "Read the fog signal post too." in text
    assert "youtube" not in text and "{{<" not in text.replace("{{</*", "")
    # A page showing shortcode syntax literally is showing prose.
    assert "`{{</* figure */>}}`" in text


def test_a_plain_markdown_image_is_a_figure_with_its_alt_text():
    text, figures = reader_markdown("Look.\n\n![A lamp at night.](night.png)\n")
    assert figures[0]["alt"] == "A lamp at night."
    assert "![A lamp at night.](../figures/night.png)" in text


def test_a_figure_with_nothing_to_say_is_refused():
    with pytest.raises(RuntimeError, match="no alt text"):
        reader_markdown('{{< figure src="lamp.svg" >}}\n')
    with pytest.raises(RuntimeError, match="no alt text"):
        reader_markdown("![](lamp.svg)\n")
    with pytest.raises(RuntimeError, match="no fig-alt"):
        reader_markdown("![The lamp](lamp.svg){#fig-lamp}\n")


def test_a_mark_of_any_kind_goes_with_its_comment():
    text, _ = reader_markdown("<!-- @lighthouse(beam) keep the period -->\nIt turns.\n")
    assert text == "It turns.\n"


def test_the_prose_delivery_hands_over_the_reader_s_copy(tmp_path):
    source = tmp_path / "index.md"
    source.write_text(POST)
    [(kind, material)] = artifacts.prose(source, tmp_path / "out")
    text = material.read_text()
    assert kind == "text"
    assert "draft" not in text and "summary" not in text
    assert "[Figure caption: The period.]" in text


def test_a_figure_that_is_not_beside_the_page_is_named(tmp_path):
    source = tmp_path / "index.md"
    source.write_text('Hi.\n\n{{< figure src="/images/lamp.png" alt="A lamp." >}}\n')
    with pytest.raises(ValueError, match="not a file beside the page"):
        artifacts.prose_images(source, tmp_path / "out")
