"""Reader-facing QMD projection, extracted from the established panel builder.

The legacy driver remains unchanged for historical runs. New preparation uses
this module; it is not a second Quarto renderer and makes no layout claim.
"""

import re
from pathlib import Path

MARK_RE = re.compile(r"@(bind|todo|guidance|genesis|variant|figure|ref|source)\b")

HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

RAW_BLOCK = re.compile(r"^```\{=[^}]+\}\s*$.*?^```\s*$", re.MULTILINE | re.DOTALL)

ATTR_BLOCK = re.compile(r"\s*\{(?=[#.])[^{}\n]*\}")

FIGURE = re.compile(
    r"!\[(?P<caption>[^\]]*)\]\((?P<name>[^)]+\.(?:svg|png|jpe?g|webp))\)"
    r"(?P<attrs>\{[^\n]*\})?"
)


def clean_qmd(text: str) -> tuple[str, list[dict]]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise RuntimeError("unterminated YAML front matter")
        text = text[end + 5 :]
    text = HTML_COMMENT.sub("", text)
    text = RAW_BLOCK.sub("", text)
    figures = []

    def replace_figure(match: re.Match) -> str:
        caption = match.group("caption").strip()
        name = match.group("name")
        attrs = match.group("attrs") or ""
        alt_match = re.search(r'fig-alt="([^"]+)"', attrs)
        if not alt_match:
            raise RuntimeError(f"figure has no fig-alt: {name}")
        alt = alt_match.group(1)
        png = Path(name).with_suffix(".png").name
        figures.append(
            {
                "source": name,
                "file": f"figures/{png}",
                "caption": caption,
                "alt": alt,
            }
        )
        return f"![{caption}](../figures/{png})\n\n*Figure description:* {alt}"

    text = FIGURE.sub(replace_figure, text)
    text = ATTR_BLOCK.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    leaks = []
    if "<!--" in text:
        leaks.append("HTML comment")
    if MARK_RE.search(text):
        leaks.append("annotation mark")
    if "{#" in text:
        leaks.append("anchor")
    if re.search(r"illustrations/[^)]+\.svg", text):
        leaks.append("SVG source path")
    if re.search(r"^```\{=", text, re.MULTILINE):
        leaks.append("raw-format block")
    if leaks:
        raise RuntimeError(f"clean Markdown still contains: {', '.join(leaks)}")
    return text, figures
