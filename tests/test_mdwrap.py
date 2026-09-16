from quirework.text import mdwrap


# --------------------------------------------------------------------------
# Sentence mode (semantic line breaks) — the canonical book prose.
# --------------------------------------------------------------------------


def test_splits_one_sentence_per_line():
    assert mdwrap.rewrap("The cat sat. The dog ran.") == "The cat sat.\nThe dog ran."


def test_idempotent():
    once = mdwrap.rewrap("The cat sat. The dog ran. Birds flew.")
    assert mdwrap.rewrap(once) == once


def test_headings_and_lists_pass_through():
    src = "# Title\n\n- one. two.\n"
    out = mdwrap.rewrap(src)
    assert "# Title" in out
    # a bullet line is not reflowed into two sentence-lines
    assert "- one. two." in out


def test_fenced_code_is_verbatim():
    src = "```\na. b. c.\n```"
    assert mdwrap.rewrap(src) == src


def test_html_comment_is_verbatim_if_not_annotation():
    # standard HTML comments (like the _map.md header) survive a rewrap intact
    src = "<!-- this is a long standard comment that should not be wrapped at all even if it is very very very long -->"
    assert mdwrap.rewrap(src) == src


def test_annotation_comment_is_wrapped():
    # @-annotations inside HTML comments are now hard-wrapped at 72 columns
    src = "<!-- @todo: this is a very long todo that extends far beyond the seventy two character limit and therefore should be wrapped by the mdwrap script into multiple lines. -->"
    out = mdwrap.rewrap(src)
    assert out != src
    lines = out.split("\n")
    assert len(lines) > 1
    assert lines[0].startswith("<!-- @todo:")
    assert lines[1].startswith("     ")  # hanging indent (5 spaces)
    assert out.endswith("-->")


def test_abbreviation_is_only_protected_as_a_whole_word():
    # A substring match finds the "al." of "et al." inside "real." / "moral." /
    # "final." and eats the sentence break after them (silently, both ways: the
    # merged line survives --check, and a correctly split pair gets re-merged).
    for word in ("real", "moral", "final", "usual", "potential", "equal"):
        src = f"The pain was {word}. No one saw it."
        assert mdwrap.rewrap(src) == f"The pain was {word}.\nNo one saw it.", word


def test_real_abbreviations_still_protected():
    # the reason _protect exists at all: these must NOT split
    for src in (
        "The study (Hedgecock et al. 1975) held up.",
        "See e.g. the note below.",
        "Dr. Smith agreed with Prof. Jones.",
        "The line runs from W. V. O. Quine to here.",
    ):
        assert mdwrap.rewrap(src) == src


def test_comment_closing_and_reopening_on_one_line_is_split():
    # deleting one mark from a stack of them leaves `... -.-><!-- @ref ...`; the
    # scanner used to hand the tail to _reflow_comment, which gave up silently
    src = (
        "<!-- @guidance first mark. -->"
        "<!-- @ref second mark lives in ideas/exercises/ai-trolley.md -->"
    )
    out = mdwrap.rewrap(src)
    lines = out.split("\n")
    assert lines[0] == "<!-- @guidance first mark. -->"
    assert lines[1].startswith("<!-- @ref second mark")
    assert out.endswith("-->")
    assert mdwrap.rewrap(out) == out


def test_comment_after_prose_gets_its_own_line():
    # a comment opening mid-line was not seen as a comment at all: sentence mode
    # reflowed it AS PROSE, splitting the annotation at its internal ". "
    src = "The vote never turned. <!-- @todo(x) gloss this. A watcher had the verdict. -->"
    out = mdwrap.rewrap(src).split("\n")
    assert out[0] == "The vote never turned."
    assert out[1] == "<!-- @todo(x) gloss this. A watcher had the verdict. -->"


def test_prose_after_a_comment_close_is_not_glued_to_it():
    src = "<!-- @guidance a mark -->The prose resumes here. And keeps going."
    assert mdwrap.rewrap(src) == (
        "<!-- @guidance a mark -->\nThe prose resumes here.\nAnd keeps going."
    )


def test_comment_delimiter_inside_a_code_span_is_text():
    # docs that *talk about* the stamp must not be read as opening a comment
    src = "The stamp is `<!-- Added: x -->` at the top. The next sentence."
    assert mdwrap.rewrap(src) == (
        "The stamp is `<!-- Added: x -->` at the top.\nThe next sentence."
    )


def test_unterminated_comment_is_kept_verbatim():
    # never drop content: an unclosed comment passes through untouched
    src = "<!-- @todo(x) someone forgot to close this\n     and it runs to EOF"
    assert mdwrap.rewrap(src) == src
    assert mdwrap.reflow_columns(src) == src


def test_line_start_guard_is_waived_inside_comments():
    # An em-dash token landing exactly on the fold. In Markdown a leading "--"
    # is a setext rule, so the guard drags it back up and the line runs over
    # width; inside an HTML comment nothing parses as a block, so the width
    # wins instead. (The repo's annotations use " -- " as an em-dash throughout,
    # which is how a guard meant for prose came to inflate comment lines.)
    tokens = ["x" * 65, "--", "tail"]
    guarded = mdwrap._wrap_tokens(tokens, "<!-- ", "     ", 72)
    waived = mdwrap._wrap_tokens(tokens, "<!-- ", "     ", 72, guard_line_start=False)
    assert guarded[0].endswith(" --") and len(guarded[0]) == 73
    assert len(waived[0]) == 70 and waived[1] == "     -- tail"


def test_comment_closer_may_overhang_the_margin():
    # PINNED BEHAVIOR, not a bug: the closing "-->" rides on the last line even
    # when that carries it past 72. It is a delimiter, not content; wrapping it
    # as a token would orphan it onto a line of its own. If you came here to
    # make annotations respect 72 strictly, that is the trade you are making --
    # see AGENTS.md, "Markdown conventions".
    # the real @genesis mark from ch5, whose last line lands on the margin
    src = (
        "<!-- @genesis Rocky was a natural choice: the author is a fan of "
        "Project Hail Mary and of science fiction generally, and Rocky is "
        "alien, widely known, and ties straight to the philosophical Bat "
        "(Nagel). -->"
    )
    lines = mdwrap.rewrap(src).split("\n")
    assert all(len(l) <= 72 for l in lines[:-1])
    assert lines[-1].endswith(" -->")
    assert len(lines[-1]) > 72  # the closer overhangs, on purpose
    assert len(lines[-1].rstrip()[:-4]) <= 72  # the content it rides on does not
    assert mdwrap.rewrap("\n".join(lines)) == "\n".join(lines)  # and it is stable


def test_column_prose_still_guards_the_line_start():
    # the waiver is comment-only: in prose a leading "--" is a setext rule
    src = (
        "The audit covered every mark and every tracked run in the file -- "
        "a long tail of them, gathered over several months of review work."
    )
    for line in mdwrap.reflow_columns(src).split("\n"):
        assert not line.lstrip().startswith("--")


def test_column_mode_splits_joined_comments_too():
    src = "<!-- @guidance first. --><!-- @todo second. -->"
    out = mdwrap.reflow_columns(src)
    assert out == "<!-- @guidance first. -->\n<!-- @todo second. -->"


def test_fenced_div_callout_passes_through():
    # ::: callout delimiters must pass through; prose inside still wraps
    out = mdwrap.rewrap("::: {.callout-note}\nFirst one. Second one.\n:::")
    assert "::: {.callout-note}" in out
    assert out.strip().endswith(":::")
    assert "First one.\nSecond one." in out


def test_block_image_passes_through_verbatim():
    # a figure line (![caption](src){attrs}) must stay one physical line so its
    # multi-sentence caption and fig-alt/#fig-id attributes survive; the sentence
    # splitter must not break it (esp. inside a quoted fig-alt with ";" or ".").
    fig = (
        "![First sentence. Second sentence.](figures/x.svg)"
        '{#fig-x fig-alt="Alt one; alt two. Alt three."}'
    )
    src = f"The cat sat. The dog ran.\n\n{fig}\n\nBirds flew. Fish swam."
    out = mdwrap.rewrap(src)
    assert fig in out  # figure line untouched
    assert "The cat sat.\nThe dog ran." in out  # prose before it still wraps
    assert "Birds flew.\nFish swam." in out  # prose after it still wraps


def test_yaml_frontmatter_is_preserved():
    # a multi-key frontmatter block must not be reflowed into one line, but prose
    # after it must still wrap
    src = '---\ntitle: "A"\nsubtitle: "B"\n---\n\nThe cat sat. The dog ran.'
    out = mdwrap.rewrap(src)
    assert '---\ntitle: "A"\nsubtitle: "B"\n---' in out
    assert "The cat sat.\nThe dog ran." in out


# --------------------------------------------------------------------------
# Column mode (hard wrap) — the technical docs.
# --------------------------------------------------------------------------


def _widths(text):
    return [len(line) for line in text.split("\n")]


def test_column_wraps_paragraph_to_width():
    src = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
        "mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega."
    )
    out = mdwrap.reflow_columns(src, width=40)
    assert max(_widths(out)) <= 40
    assert len(out.split("\n")) > 1  # actually wrapped
    # content preserved (whitespace-collapsed)
    assert mdwrap._canonical(src) == mdwrap._canonical(out)


def test_column_is_idempotent():
    src = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
        "mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega."
    )
    once = mdwrap.reflow_columns(src, width=40)
    assert mdwrap.reflow_columns(once, width=40) == once


def test_column_list_hanging_indent():
    src = (
        "- First bullet with enough words to require wrapping across more "
        "than a single line at this narrow width here."
    )
    out = mdwrap.reflow_columns(src, width=40)
    lines = out.split("\n")
    assert lines[0].startswith("- ")
    # continuation is indented under the bullet text (hanging indent = 2)
    assert all(ln.startswith("  ") and not ln.startswith("- ") for ln in lines[1:])
    assert max(_widths(out)) <= 40


def test_column_list_item_second_paragraph_wraps():
    """A list item may hold several paragraphs; the blank ends the paragraph,
    not the item, so the next one reflows under the item's hanging indent."""
    src = (
        "1. First paragraph of the item.\n"
        "\n"
        "   A second paragraph inside the same item, long enough that it "
        "has to wrap at this width.\n"
    )
    out = mdwrap.reflow_columns(src, width=40)
    lines = [ln for ln in out.split("\n") if ln.strip()]
    assert lines[0].startswith("1. ")
    assert all(ln.startswith("   ") for ln in lines[1:])
    assert max(_widths(out)) <= 40
    assert mdwrap._canonical(src) == mdwrap._canonical(out)
    assert mdwrap.reflow_columns(out, width=40) == out  # idempotent


def test_column_indented_code_in_list_item_stays_verbatim():
    """Four columns past the item's own indent is an indented code block, and
    reflowing one would corrupt it. This is the line the paragraph rule above
    must not cross."""
    code = "       code = 'a line far longer than the width, left alone'"
    src = f"1. An item.\n\n{code}\n"
    out = mdwrap.reflow_columns(src, width=40)
    assert code in out.split("\n")


def test_column_list_ends_at_column_zero_prose():
    """Column-zero prose ends the list, so an indented block after it is code
    again and not a continuation of the item several paragraphs back."""
    code = "    still code, and still far longer than the wrapping width"
    src = f"- A bullet.\n\nPlain prose ends the list.\n\n{code}\n"
    out = mdwrap.reflow_columns(src, width=40)
    assert code in out.split("\n")


def test_column_nested_bullets_keep_indent():
    src = "- parent item\n  - child item that is fairly long and wraps a bit here now\n"
    out = mdwrap.reflow_columns(src, width=40)
    assert "- parent item" in out
    # the nested bullet keeps its two-space indent
    assert any(ln.startswith("  - child") for ln in out.split("\n"))
    assert mdwrap._canonical(src) == mdwrap._canonical(out)


def test_column_link_never_split():
    # a link straddling the wrap boundary must stay on one line: breaking it would
    # insert a space into the destination. The _canonical guard would abort, so a
    # clean reflow proves the tokenizer kept it whole.
    src = (
        "word word word word word [the linked phrase](https://example.com/x) "
        "tail tail tail tail tail."
    )
    out = mdwrap.reflow_columns(src, width=30)
    assert "[the linked phrase](https://example.com/x)" in out
    assert mdwrap._canonical(src) == mdwrap._canonical(out)


def test_column_inline_code_never_split():
    src = (
        "prefix prefix prefix `code span with spaces inside` suffix suffix suffix here."
    )
    out = mdwrap.reflow_columns(src, width=25)
    assert "`code span with spaces inside`" in out
    assert mdwrap._canonical(src) == mdwrap._canonical(out)


def test_column_table_and_blockquote_verbatim():
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = mdwrap.reflow_columns(table, width=10)
    assert out == table  # tables never reflow
    quote = "> a long quoted line that would exceed the width but stays verbatim."
    assert mdwrap.reflow_columns(quote, width=20) == quote


def test_column_no_spurious_list_marker_at_line_start():
    # a mid-prose "+" must not wrap to the start of a line, where it would parse
    # as a nested bullet (and change the render). Reflow must be idempotent here.
    src = (
        "- audited all 127 comments + tracked runs against this file and then "
        "folded in the recovered deletions after a careful second pass here."
    )
    out = mdwrap.reflow_columns(src, width=40)
    for line in out.split("\n")[1:]:
        assert not line.lstrip().startswith(("+ ", "- ", "* ")), line
    assert mdwrap.reflow_columns(out, width=40) == out  # idempotent


def test_column_indented_block_kept_verbatim():
    # an indented code block (no fence) must not be dedented/reflowed into prose;
    # _canonical cannot guard this because it collapses leading whitespace.
    src = "Intro paragraph here.\n\n    indented code line one\n    indented code two\n"
    out = mdwrap.reflow_columns(src, width=20)
    assert "    indented code line one" in out
    assert "    indented code two" in out


def test_column_overlong_token_kept_whole():
    src = "see https://example.com/a/very/long/url/that/exceeds/the/width/limit here"
    out = mdwrap.reflow_columns(src, width=20)
    assert "https://example.com/a/very/long/url/that/exceeds/the/width/limit" in out
    assert mdwrap._canonical(src) == mdwrap._canonical(out)


# --------------------------------------------------------------------------
# Trailing whitespace + classification + file processing.
# --------------------------------------------------------------------------


def test_trailing_whitespace_stripped_all_modes(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    # trailing mode (a SOP path): only trailing WS removed, no reflow
    sop = tmp_path / "sops" / "x.md"
    sop.parent.mkdir()
    sop.write_text("keep this long line unwrapped but drop the trailing space   \n")
    assert mdwrap.process(str(sop), check=False) is False
    assert (
        sop.read_text() == "keep this long line unwrapped but drop the trailing space\n"
    )


def test_trailing_mode_preserves_directive_lines(tmp_path):
    # GEMINI.md is a block of one-per-line @-imports; trailing mode must not merge
    # them (column reflow would). Only trailing whitespace is touched.
    (tmp_path / "pyproject.toml").write_text("")
    f = tmp_path / "GEMINI.md"
    f.write_text("@AGENTS.md   \n@docs/AGENTS.md\n@src/mindwright/AGENTS.md\n")
    mdwrap.process(str(f), check=False)
    assert f.read_text() == "@AGENTS.md\n@docs/AGENTS.md\n@src/mindwright/AGENTS.md\n"


def _repo(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    return tmp_path


def test_check_passes_on_wrapped_column_file(tmp_path):
    repo = _repo(tmp_path)
    f = repo / "ideas" / "note.md"
    f.parent.mkdir()
    f.write_text("Short line that fits.\n")
    assert mdwrap.main(["--check", str(f)]) == 0


def test_check_fails_on_unwrapped_column_file(tmp_path):
    repo = _repo(tmp_path)
    f = repo / "ideas" / "note.md"
    f.parent.mkdir()
    long = " ".join(["word"] * 40) + ".\n"
    f.write_text(long)
    assert mdwrap.main(["--check", str(f)]) != 0


def test_check_fails_on_unwrapped_sentence_file(tmp_path):
    repo = _repo(tmp_path)
    f = repo / "docs" / "canonical" / "01-x.qmd"
    f.parent.mkdir(parents=True)
    f.write_text("The cat sat. The dog ran.\n")
    assert mdwrap.main(["--check", str(f)]) != 0


def test_check_flags_trailing_whitespace(tmp_path):
    repo = _repo(tmp_path)
    f = repo / "sops" / "x.md"
    f.parent.mkdir()
    f.write_text("a line with trailing space \n")  # trailing mode still flags WS
    assert mdwrap.main(["--check", str(f)]) != 0


# --------------------------------------------------------------------------
# classify: the per-chapter metadata sidecars wrap as 72-column source
# --------------------------------------------------------------------------


def test_marks_sidecar_edited_long_mark_wraps_at_72():
    # the point of column mode for sidecars: a hand-edited long mark is
    # hard-wrapped on commit, with a hanging indent, like it was inline.
    long = "# H {#sec-x}\n\n<!-- @guidance " + "word " * 40 + "-->\n"
    out = mdwrap.reflow_columns(long.rstrip("\n"), 72)
    assert out != long.rstrip("\n")  # it reflowed
    assert all(
        len(l) <= 76 for l in out.splitlines()
    )  # 72 + the -->-overhang allowance
    assert mdwrap._canonical(long.rstrip("\n")) == mdwrap._canonical(
        out
    )  # content intact


# --------------------------------------------------------------------------
# CLI hardening: a typo must never silently turn a check into a write
# --------------------------------------------------------------------------


def test_unknown_flag_is_fatal_and_writes_nothing(tmp_path):
    """`--chek` once fell through the flag loop and REWROTE the files it was
    asked to check. Unknown flags must exit 2 before any file is touched."""
    f = tmp_path / "ideas" / "n.md"
    f.parent.mkdir()
    unwrapped = "x " * 60 + "\n"
    f.write_text(unwrapped)
    try:
        rc = mdwrap.main(["--chek", str(f)])
    except SystemExit as e:
        rc = e.code
    assert rc == 2
    assert f.read_text() == unwrapped, "the file must not be rewritten"


def test_missing_file_is_a_clean_error(tmp_path, capsys):
    rc = mdwrap.main(["--check", str(tmp_path / "ideas" / "nope.md")])
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err
