"""A reader's copy of a unit's source: the Markdown a reader of the page sees.

A seat handed prose must get what a reader of the rendered page gets and
nothing the author keeps for themselves: no frontmatter (a draft flag, tags, a
summary the author wrote about the piece), no comments (the marks), no raw
output blocks, no anchors and no generator syntax. It is not a renderer and
makes no layout claim.

The source may be written for Quarto or for a site generator, and the
projection reads either by its syntax alone, since a delivery never knows the
project's format and must not import it:

- frontmatter in either convention, TOML between `+++` or YAML between `---`
- a figure as Pandoc writes it, `![caption](plate.svg){fig-alt="..."}`, whose
  `fig-alt` is required; as plain Markdown, `![alt text](plate.png)`; or as
  Hugo's `{{< figure src="..." alt="..." caption="..." >}}`
- any other shortcode: a paired one keeps its content and loses its tags, a
  link whose target is one keeps its text, and a standalone one is dropped,
  since it renders as something no Markdown reader can be handed

Anything that survives the projection and should not have is refused by name
rather than delivered.
"""

import re
from pathlib import Path

from quirework.core import frontmatter

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

RAW_BLOCK = re.compile(r"^```\{=[^}]+\}\s*$.*?^```\s*$", re.MULTILINE | re.DOTALL)

ATTR_BLOCK = re.compile(r"\s*\{(?=[#.])[^{}\n]*\}")

_IMAGE = r"(?:svg|png|jpe?g|webp)"

# `![text](source){attrs}`: Pandoc's figure when the attribute block is there,
# plain Markdown when it is not. A title after the source is allowed.
FIGURE = re.compile(
    r"!\[(?P<caption>[^\]]*)\]\((?P<name>[^)\"]+?\." + _IMAGE + r")"
    r"(?:\s+\"[^\"]*\")?\)"
    r"(?P<attrs>\{[^\n]*\})?"
)

# A shortcode tag, `{{< ... >}}` or `{{% ... %}}`. The escaped form a page
# uses to show one literally (`{{</* ... */>}}`) is not a tag.
_SHORTCODE = r"\{\{[<%](?!/\*)\s*(?P<body>.*?)\s*[%>]\}\}"
SHORTCODE = re.compile(_SHORTCODE, re.DOTALL)
SHORTCODE_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\(\s*" + _SHORTCODE + r"\s*\)")
_PARAM = re.compile(r"""(\w+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|(\S+))""")


def _params(body: str) -> dict[str, str]:
    return {
        m.group(1): next(g for g in m.group(2, 3, 4) if g is not None)
        for m in _PARAM.finditer(body)
    }


def _paired(text: str) -> str:
    """Drop the tags of every paired shortcode, keeping what they enclose."""
    names = {
        m.group("body").split()[0].lstrip("/")
        for m in SHORTCODE.finditer(text)
        if m.group("body").startswith("/")
    }
    for name in names:
        tag = r"\{\{[<%]\s*/?" + re.escape(name) + r"\b.*?[%>]\}\}"
        text = re.sub(tag, "", text, flags=re.DOTALL)
    return text


def reader_markdown(text: str, alt: bool = True) -> tuple[str, list[dict]]:
    """Reader-facing Markdown from a unit's source, and its figures.

    ``alt`` prints each figure's alt text as a visible description under the
    embed. Keep it where the reader gets no image, since it is then the only
    account of the figure; drop it where the image is delivered, since there
    it restates the picture and lets a reader rate one it never looked at.
    """
    found = frontmatter.block(text)
    if found:
        text = text[found.end() :]
    elif text.startswith(("---\n", "+++\n")):
        raise RuntimeError("unterminated frontmatter")
    text = HTML_COMMENT.sub("", text)
    text = RAW_BLOCK.sub("", text)
    figures = []

    def figure(name: str, caption: str, description: str) -> str:
        png = Path(name).with_suffix(".png").name
        figures.append(
            {
                "source": name,
                "file": f"figures/{png}",
                "caption": caption,
                "alt": description,
            }
        )
        embed = f"![{caption}](../figures/{png})"
        return f"{embed}\n\n*Figure description:* {description}" if alt else embed

    def markdown_figure(match: re.Match) -> str:
        caption = match.group("caption").strip()
        name = match.group("name")
        attrs = match.group("attrs")
        if attrs is not None:
            alt_match = re.search(r'fig-alt="([^"]+)"', attrs)
            if not alt_match:
                raise RuntimeError(f"figure has no fig-alt: {name}")
            return figure(name, caption, alt_match.group(1))
        if not caption:
            raise RuntimeError(f"figure has no alt text: {name}")
        return figure(name, caption, caption)

    def shortcode_figure(match: re.Match) -> str:
        body = match.group("body")
        if not re.match(r"figure\b", body):
            return match.group(0)
        params = _params(body)
        name = params.get("src", "")
        if not re.search(r"\." + _IMAGE + r"\Z", name):
            raise RuntimeError(f"figure shortcode has no image source: {body}")
        caption = params.get("caption") or params.get("title") or ""
        description = params.get("alt") or caption
        if not description:
            raise RuntimeError(f"figure has no alt text: {name}")
        return figure(name, caption, description)

    text = FIGURE.sub(markdown_figure, text)
    text = SHORTCODE.sub(shortcode_figure, text)
    text = SHORTCODE_LINK.sub(lambda m: m.group("text"), text)
    text = _paired(text)
    text = re.sub(r"(?m)^[ \t]*" + _SHORTCODE + r"[ \t]*$", "", text, flags=re.DOTALL)
    text = SHORTCODE.sub("", text)
    text = ATTR_BLOCK.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    leaks = []
    if "<!--" in text:
        leaks.append("HTML comment")
    if "{#" in text:
        leaks.append("anchor")
    if re.search(r"!\[[^\]]*\]\([^)]*\.svg", text):
        leaks.append("SVG source path")
    if re.search(r"^```\{=", text, re.MULTILINE):
        leaks.append("raw-format block")
    if SHORTCODE.search(text):
        leaks.append("shortcode")
    if leaks:
        raise RuntimeError(f"clean Markdown still contains: {', '.join(leaks)}")
    return text, figures
