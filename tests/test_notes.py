from conftest import patch_setting
import pytest
from quirework.project import Project, project_scope

# The names a project gives its spec and its review notes.
SPEC_FILE = "_spec.md"
BOOK_NOTES_FILE = "_review-notes.md"


def project_at(root):
    """A Quarto book with a spec, review notes and two companion collections,
    laid out as the configuration guide teaches, at `root`."""
    return Project.from_settings(
        root,
        {
            "manuscript": {
                "source": "docs",
                "chapters": "docs/canon",
                "quarto": "docs/_quarto.yml",
                "spec": f"docs/canon/{SPEC_FILE}",
                "review_notes": f"docs/canon/{BOOK_NOTES_FILE}",
                "companion_dirs": ["exercises", "cases"],
                "noncanonical_roots": ["analysis/", "ideas/", "scratchpad/"],
            }
        },
    )


@pytest.fixture(autouse=True)
def quarto_book(tmp_path):
    with project_scope(project_at(tmp_path)):
        yield


from quirework.runs import commands as run_commands
from quirework.manuscript import notes

SAMPLE = """\
---
title: "Ch"
---

# The shore {#sec-shore}

A kettle has states.
<!-- @todo  rephrase to stop begging the question -->
<!-- @bind(shore-condition) -->

## Swells

Felt is represented.
<!-- @flavor(layman,teen)
     drop the almanac aside; keep the image -->
<!-- @ref  conv:c0ffee00 -- the lamp-room exchange -->
"""


def test_scan_parses_kinds_args_body():
    annos = notes.scan_text(SAMPLE, "docs/canon/ch.qmd")
    kinds = {a.kind for a in annos}
    assert kinds == {"todo", "bind", "flavor", "ref"}

    binding = next(a for a in annos if a.kind == "bind")
    assert binding.args == ["shore-condition"]
    assert binding.body == ""


def test_multiline_body_collapsed():
    annos = notes.scan_text(SAMPLE, "x.qmd")
    flavor = next(a for a in annos if a.kind == "flavor")
    assert flavor.args == ["layman", "teen"]
    assert flavor.body == "drop the almanac aside; keep the image"


def test_section_attribution():
    annos = notes.scan_text(SAMPLE, "x.qmd")
    todo = next(a for a in annos if a.kind == "todo")
    assert todo.section == "sec-shore"  # prefers the {#id}
    ref = next(a for a in annos if a.kind == "ref")
    assert ref.section == "Swells"  # heading text when no id


def test_line_numbers():
    annos = notes.scan_text(SAMPLE, "x.qmd")
    todo = next(a for a in annos if a.kind == "todo")
    assert SAMPLE.splitlines()[todo.line - 1].startswith("<!-- @todo")


def test_annotations_in_code_fences_are_ignored():
    doc = (
        "# H {#sec-h}\n\n"
        "Real one here.\n<!-- @todo real -->\n\n"
        "```\n<!-- @todo example-in-fence -->\n```\n"
    )
    annos = notes.scan_text(doc, "x.qmd")
    bodies = [a.body for a in annos]
    assert "real" in bodies
    assert "example-in-fence" not in bodies


def test_scan_dir_and_main(tmp_path, capsys):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    annos = notes.scan([tmp_path])
    assert len(annos) == 4

    # positional arg is a KIND filter; the scan path moves to --path
    rc = notes.main(["bind", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "shore-condition" in out
    assert "@todo" not in out


def test_kind_flag_alias_and_union(tmp_path, capsys):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    # --kind still works (back-compat) and unions with positionals
    rc = notes.main(["bind", "--kind", "todo", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "shore-condition" in out  # binding
    assert "begging the question" in out  # todo
    assert "the lamp-room" not in out  # ref excluded


def test_unknown_kind_errors_with_suggestion(tmp_path, capsys):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    rc = notes.main(["bnd", "--path", str(tmp_path)])  # typo of "bind"
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown kind 'bnd'" in err
    assert "bind" in err  # did-you-mean + available list


def test_by_unit_groups_by_file(tmp_path, capsys):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    (tmp_path / "b.qmd").write_text("# The pier {#sec-pier}\n\n<!-- @todo x -->\n")
    rc = notes.main(["--by", "unit", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "a.qmd — Ch" in out  # a frontmatter title names the page, as Quarto does
    assert "b.qmd — The pier" in out  # else its first heading
    assert "chapter(s)" in out  # the summary counts in the format's own noun


def test_primer_is_bind_guidance_by_chapter(tmp_path, capsys):
    extra = "\n<!-- @guidance keep it cinematic -->\n"
    (tmp_path / "a.qmd").write_text(SAMPLE + extra)
    rc = notes.main(["--primer", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "shore-condition" in out  # binding
    assert "keep it cinematic" in out  # guidance
    assert "begging the question" not in out  # todo excluded
    assert "the lamp-room" not in out  # genesis/ref excluded
    assert "chapter(s)" in out  # grouped by chapter


def test_primer_skips_absent_preset_members(tmp_path, capsys):
    # SAMPLE has a binding but no guidance; --primer must not error on the
    # missing members, it just shows what is present.
    (tmp_path / "a.qmd").write_text(SAMPLE)
    rc = notes.main(["--primer", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "shore-condition" in out  # the one present preset member
    assert "begging the question" not in out  # todo still excluded


def test_list_kinds(tmp_path, capsys):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    rc = notes.main(["--list", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "bind" in out and "todo" in out
    assert "unit-to-spec" in out  # the gloss for @bind


def test_chapter_filter_no_match_errors(tmp_path, capsys):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    rc = notes.main(["-c", "nope", "--path", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "no chapter matches 'nope'" in err


def test_dev_guide_files_skipped_by_default(tmp_path):
    (tmp_path / "a.qmd").write_text(SAMPLE)
    (tmp_path / "AGENTS.md").write_text(
        "<!-- @guidance an example, not book content -->\n"
    )
    kinds = [a.kind for a in notes.scan([tmp_path])]
    assert "guidance" not in kinds  # AGENTS.md excluded from the dir glob
    # ...but an explicitly named path still reads it
    named = notes.scan([tmp_path / "AGENTS.md"])
    assert [a.kind for a in named] == ["guidance"]


# --------------------------------------------------------------------------- #
# sidecar model: marks live in _marks/<chapter>.md, attributed to the chapter
# --------------------------------------------------------------------------- #
def _mini_book(tmp_path):
    """A minimal docs tree: one canon chapter, its sidecar (mirroring the
    chapter's heading skeleton), and an empty book-wide manifest."""
    canon = tmp_path / "canon"
    (canon / "_marks").mkdir(parents=True)
    (canon / "ch.qmd").write_text(
        "# The shore {#sec-shore}\n\n## Swells {#sec-q}\n\nProse.\n"
    )
    (canon / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @bind(shore) -->\n\n"
        "## Swells {#sec-q}\n\n"
        "<!-- @guidance keep this section plain -->\n"
    )
    (canon / "_review-notes.md").write_text("")
    # every @bind is a binding, so the mini book ships the section it binds;
    # tests that call _spec() override this.
    (canon / SPEC_FILE).write_text("# The spec\n\n## The shore {#shore}\n\nStated.\n")
    return tmp_path


def test_sidecar_marks_attributed_to_chapter(tmp_path):
    _mini_book(tmp_path)
    annos = notes.scan([tmp_path])
    assert annos  # the sidecar was harvested
    # every mark is attributed to the chapter .qmd, never the sidecar .md
    assert all(a.file.endswith("canon/ch.qmd") for a in annos)
    assert not any("/_marks/" in a.file for a in annos)


def test_sidecar_marks_carry_a_resolvable_source_line(tmp_path):
    """`file` names the chapter and `line` counts within the sidecar, so the
    pair never resolved. `source:line` is the one that opens on the mark."""
    _mini_book(tmp_path)
    annos = notes.scan([tmp_path])
    assert all(a.source.endswith("canon/_marks/ch.md") for a in annos)
    assert all(a.file.endswith("canon/ch.qmd") for a in annos)  # unchanged
    swells = next(a for a in annos if a.body.startswith("keep this section"))
    side = (tmp_path / "canon" / "_marks" / "ch.md").read_text().splitlines()
    assert "keep this section plain" in side[swells.line - 1]


def test_scan_text_source_defaults_to_the_attributed_path(tmp_path):
    """redpen/variant call scan_text() with one path and no source; they must
    keep getting a usable `source` rather than an empty string."""
    (anno,) = notes.scan_text("<!-- @todo x -->\n", "docs/canon/ch.qmd")
    assert anno.source == "docs/canon/ch.qmd"


def test_sidecar_section_from_mirrored_heading(tmp_path):
    _mini_book(tmp_path)
    annos = notes.scan([tmp_path])
    shore = next(a for a in annos if a.kind == "bind")
    assert shore.section == "sec-shore"  # under the mirrored H1 anchor
    swells = next(a for a in annos if a.kind == "guidance")
    assert swells.section == "sec-q"  # under the mirrored ## anchor


def test_check_clean_layout_passes(tmp_path, capsys):
    _mini_book(tmp_path)
    assert notes.check(tmp_path) == 0


def test_check_flags_mark_left_in_qmd(tmp_path, capsys):
    _mini_book(tmp_path)
    (tmp_path / "canon" / "ch.qmd").write_text(
        "# The shore {#sec-shore}\n\n<!-- @todo left behind -->\n\nProse.\n"
    )
    assert notes.check(tmp_path) == 1
    assert "left in prose" in capsys.readouterr().err


def test_check_flags_orphan_sidecar(tmp_path, capsys):
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ghost.md").write_text(
        "<!-- @todo no chapter -->\n"
    )
    assert notes.check(tmp_path) == 1
    assert "orphan sidecar" in capsys.readouterr().err


def test_check_flags_drifted_mirror_heading(tmp_path, capsys):
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n## Gone {#sec-gone}\n\n<!-- @guidance x -->\n"
    )
    assert notes.check(tmp_path) == 1
    assert "mirror has drifted" in capsys.readouterr().err


def test_check_flags_unbound_binding_slug(tmp_path, capsys):
    """There is no unbound relationship layer: a slug the
    spec does not declare is a hard error, not a ratcheting advisory."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n<!-- @bind(no-such-section) -->\n"
    )
    assert notes.check(tmp_path) == 1
    assert "binds no section of" in capsys.readouterr().err


def test_check_flags_dangling_see(tmp_path, capsys):
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n<!-- @see(nope) points nowhere -->\n"
    )
    assert notes.check(tmp_path) == 1
    assert "resolves to no work-wide note" in capsys.readouterr().err


def test_check_flags_ai_register_in_block_surfaced_mark(tmp_path, capsys):
    """The three block kinds are what the author reads in the .docx, so contract
    shorthand landing in one is miscast. Advisory, never fatal: the fix is a
    recast, which is a judgment call."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @guidance  TWO MARKS DEPEND ON THIS and may not be moved -->\n"
    )
    assert notes.check(tmp_path) == 0  # advisory, not a failure
    err = capsys.readouterr().err
    assert "AI register" in err and "TWO MARKS DEPEND" in err


def test_check_leaves_plain_prose_and_ai_facing_kinds_alone(tmp_path, capsys):
    """No false positives: emphasis in plain prose stays under the three-word
    run, and a bodyless binding carries no register to lint."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @guidance  the crashed ship should read as haunting; DO NOT "
        "name the tide inside the story -->\n"
        "<!-- @bind(shore) -->\n"
    )
    assert notes.check(tmp_path) == 0
    assert "AI register" not in capsys.readouterr().err


def test_check_rejects_binding_body(tmp_path, capsys):
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n<!-- @bind(shore) preservation contract -->\n"
    )
    assert notes.check(tmp_path) == 1
    assert "Bindings are relationships" in capsys.readouterr().err


def test_check_rejects_binding_with_multiple_slugs(tmp_path, capsys):
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n<!-- @bind(shore, another-section) -->\n"
    )
    assert notes.check(tmp_path) == 1
    assert "exactly one slug" in capsys.readouterr().err


def test_check_flags_journaling_in_contract_marks(tmp_path, capsys):
    """A mark records current state, never history: dated stamps, authority
    citations and review-round references inside guidance are
    journaling. Advisory, never fatal — the fix is a rewrite of the mark."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @bind(shore) -->\n"
        "<!-- @guidance immerse the reader (author call, 2026-07-20) -->\n"
        "<!-- @guidance keep the image (restated in red-pen V6) -->\n"
    )
    assert notes.check(tmp_path) == 0  # advisory, not fatal
    err = capsys.readouterr().err
    assert "decision-journaling" in err
    assert "author call" in err  # the matched snippet
    assert "red-pen V6" in err


def test_check_journaling_advisory_spares_content_and_genesis(tmp_path, capsys):
    """No false positives: a year-range is a world-fact, 'retired' is the
    present-tense guard idiom, and @genesis dates are content (an origin
    story is history by design)."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @bind(shore) -->\n"
        "<!-- @guidance use the 2025-2026 frontier as the scene's horizon; "
        "wager language stays retired everywhere -->\n"
        "<!-- @genesis the author's request, in the session of 2026-07-04 -->\n"
    )
    assert notes.check(tmp_path) == 0
    assert "decision-journaling" not in capsys.readouterr().err


def test_check_flags_line_distance_pins(tmp_path, capsys):
    """A mark that locates something by counting lines is falsified by the
    next insertion above it, and nothing else checks it. Every kind is
    linted, and the wrapped case matters most: the sidecars hard-wrap at 72
    columns, so a pin routinely spans a line break and a line-based grep
    misses it."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @guidance the merge is stated thirty lines earlier -->\n"
        "<!-- @variant(swe) the coin is minted four\n"
        "     lines above, and spent here -->\n"
        "<!-- @todo the thesis sentence at 142-143 says POTENTIAL -->\n"
        "<!-- @guidance see line 22 for the naming -->\n"
    )
    assert notes.check(tmp_path) == 0  # advisory, not fatal
    err = capsys.readouterr().err
    assert "pin a line distance" in err
    assert "4 mark(s)/spec section(s)" in err
    assert "thirty lines earlier" in err
    assert "four lines above" in err  # matched across the wrap
    assert "142-143 says" in err
    assert "line 22" in err


def test_check_line_pin_advisory_spares_line_counts(tmp_path, capsys):
    """No false positives: a line COUNT is a budget, not a locator, and the
    marks use it constantly ("in one line", "three lines: a scene"). Only a
    count followed by a direction pins anything."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @guidance the opening is a scene and stays one, three lines: a\n"
        "     door, a sound, a cry; the close gets one line and no more -->\n"
        "<!-- @bind(shore) -->\n"
    )
    assert notes.check(tmp_path) == 0
    assert "pin a line distance" not in capsys.readouterr().err


def test_check_line_pin_advisory_catches_the_colon_address(tmp_path, capsys):
    """`preface:3`, `@unit(slug):69`, `where-it-leaks.qmd:71` are pins with no
    "line" keyword anywhere, so the first cut of the pattern could not see them
    at all. The one that was live had already drifted by two."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @guidance the word occurs twice, at preface:3 and\n"
        "     @unit(where-it-leaks):69 -->\n"
        "<!-- @todo the line is where-it-leaks.qmd:71, not the close -->\n"
    )
    assert notes.check(tmp_path) == 0
    err = capsys.readouterr().err
    assert "pin a line distance" in err
    assert "preface:3" in err
    assert "@unit(where-it-leaks):69" in err  # matched across the wrap
    assert "where-it-leaks.qmd:71" in err


def test_check_line_pin_advisory_spares_colon_shaped_non_addresses(tmp_path, capsys):
    """The left side has to look like a slug, a chapter pointer or a filename.
    A panel tally and a timestamp are colon-shaped and pin nothing, and an
    advisory that flags them is one the author learns to skip."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @figure(d) built: illustrations/d.svg. Reader panel: 5 of 6 -->\n"
        "<!-- @ref conv:abc123 -- the overshoot is the next turn at 15:40 -->\n"
    )
    assert notes.check(tmp_path) == 0
    assert "pin a line distance" not in capsys.readouterr().err


def test_check_line_pin_advisory_counts_past_twelve(tmp_path, capsys):
    """The number-word list stopped at twelve, so "thirteen lines later" walked
    past the lint. A word list is a hole with an edge."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @guidance lamp-not-switch, and thirteen lines later the\n"
        "     lamp has more than one needle -->\n"
    )
    assert notes.check(tmp_path) == 0
    assert "thirteen lines later" in capsys.readouterr().err


def test_check_flags_dangling_inline_spec_ref(tmp_path, capsys):
    """Inline spec #slug refs in marks can dangle after a merge or
    rename. They are resolved against the spec anchors."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @bind(shore) -->\n"
        "<!-- @todo re-check #gone-slug against #shore -->\n"
    )
    assert notes.check(tmp_path) == 0  # advisory, not fatal
    err = capsys.readouterr().err
    assert "resolve to no spec section" in err and "gone-slug" in err
    assert "1 inline spec #slug" in err  # resolving refs are silent


def test_check_rejects_bind_token_inside_another_mark(tmp_path, capsys):
    _mini_book(tmp_path)
    side = tmp_path / "canon" / "_marks" / "ch.md"
    side.write_text(
        "# The shore {#sec-shore}\n\n"
        "<!-- @bind(shore) -->\n"
        "<!-- @todo compare this with @bind(shore) -->\n"
    )
    assert notes.check(tmp_path) == 1
    err = capsys.readouterr().err
    assert "Use '#shore'" in err and "reserved for bodyless relationships" in err


# --------------------------------------------------------------------------- #
# the spec: @bind slugs bind level-2 sections of the spec file
# --------------------------------------------------------------------------- #
def _spec(tmp_path, text):
    (tmp_path / "canon" / SPEC_FILE).write_text(text)


def test_spec_sections_and_slugs(tmp_path):
    _mini_book(tmp_path)
    _spec(
        tmp_path,
        "# The spec\n\n"
        "## The shore {#shore}\n\nBody of the shore.\n\n"
        "### A sub-heading\n\nStill the shore's body.\n\n"
        "## The lamp {#lamp}\n\nBody of the lamp.\n",
    )
    secs = notes.spec_sections(tmp_path)
    assert [s[0] for s in secs] == ["shore", "lamp"]  # level-2 only
    assert "Still the shore's body." in secs[0][2]  # body runs to next ##
    assert notes.spec_slugs(tmp_path) == {"shore", "lamp"}


def test_check_binding_resolves_and_may_repeat(tmp_path, capsys):
    """Two chapters binding the same spec section is the point of the spec,
    not a duplicate; a bound slug is not legacy."""
    _mini_book(tmp_path)
    _spec(tmp_path, "# The spec\n\n## The shore {#shore}\n\nStated.\n")
    (tmp_path / "canon" / "ch2.qmd").write_text("# Two {#sec-two}\n\nProse.\n")
    (tmp_path / "canon" / "_marks" / "ch2.md").write_text(
        "# Two {#sec-two}\n\n<!-- @bind(shore) -->\n"
    )
    assert notes.check(tmp_path) == 0
    err = capsys.readouterr().err
    assert "duplicate @bind slug" not in err
    assert "legacy" not in err


def test_check_flags_unanchored_and_duplicate_spec_sections(tmp_path, capsys):
    _mini_book(tmp_path)
    _spec(
        tmp_path,
        "# The spec\n\n"
        "## No anchor here\n\nBody.\n\n"
        "## The shore {#shore}\n\nBody.\n\n"
        "## The shore again {#shore}\n\nBody.\n",
    )
    assert notes.check(tmp_path) == 1
    err = capsys.readouterr().err
    assert "no {#slug} anchor" in err
    assert "duplicate section anchor" in err


def test_check_repeated_binding_is_not_a_duplicate(tmp_path, capsys):
    """Two bodyless marks in one chapter may bind the same section."""
    _mini_book(tmp_path)
    (tmp_path / "canon" / "_marks" / "ch.md").write_text(
        "# The shore {#sec-shore}\n\n<!-- @bind(shore) -->\n<!-- @bind(shore) -->\n"
    )
    assert notes.check(tmp_path) == 0
    assert "duplicate" not in capsys.readouterr().err


def test_check_flags_imperative_register_in_spec(tmp_path, capsys):
    """The spec states and prices, never orders: an all-caps imperative is
    advisory, never fatal."""
    _mini_book(tmp_path)
    _spec(
        tmp_path,
        "# The spec\n\n"
        "## The lamp {#lamp}\n\nNEVER restore the temperature analogy.\n\n"
        "## The shore {#shore}\n\nTemperature has no zero; a rock has one.\n",
    )
    assert notes.check(tmp_path) == 0  # advisory, not fatal
    err = capsys.readouterr().err
    assert "states and prices" in err
    assert "'NEVER'" in err  # the spec section
    assert err.count("#shore") == 0  # the clean section is silent


def test_check_flags_journaling_in_spec_sections(tmp_path, capsys):
    """A spec section records current state, never history, under the same
    advisory as the contract marks."""
    _mini_book(tmp_path)
    _spec(
        tmp_path,
        "# The spec\n\n"
        "## The shore {#shore}\n\nStated.\n\n"
        "## The lamp {#lamp}\n\nSettled by author call in the last round.\n",
    )
    assert notes.check(tmp_path) == 0
    err = capsys.readouterr().err
    assert "decision-journaling" in err and "author call" in err


def test_primer_spec_toc(tmp_path, capsys):
    _mini_book(tmp_path)
    _spec(
        tmp_path,
        "# The spec\n\n## The shore {#shore}\n\nStated.\n\n"
        "## The lamp {#lamp}\n\nStated.\n",
    )
    notes._print_spec_toc(tmp_path)
    out = capsys.readouterr().out
    assert "#shore" in out and "#lamp" in out
    assert "2 section(s)" in out


def test_primer_with_path_omits_repo_spec_toc(tmp_path, capsys):
    """A --path scan is not the work: the project's own spec TOC stays out."""
    docs = tmp_path / "docs"  # the project's configured source
    _mini_book(docs)
    _spec(docs, "# The spec\n\n## The beacon {#beacon}\n\nStated.\n")
    assert notes.main(["--primer"]) == 0
    assert "#beacon" in capsys.readouterr().out  # the control: it is printed
    scanned = tmp_path / "elsewhere"
    scanned.mkdir()
    (scanned / "a.qmd").write_text(SAMPLE)
    rc = notes.main(["--primer", "--path", str(scanned)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "#beacon" not in out


# --------------------------------------------------------------------------- #
# the companion collections: exercises/ and cases/ resolve back into the spec,
# and their quotations still exist in the book they quote
# --------------------------------------------------------------------------- #
def _mini_repo(tmp_path, entry: str, canon_prose: str = "Prose."):
    """A docs/ tree with an exercises/ sibling, which is the real layout: the
    collections sit beside docs/, not inside it."""
    docs = tmp_path / "docs"
    _mini_book(docs)
    (docs / "canon" / "ch.qmd").write_text(
        f"# The shore {{#sec-shore}}\n\n## Swells {{#sec-q}}\n\n{canon_prose}\n"
    )
    (tmp_path / "exercises").mkdir()
    (tmp_path / "exercises" / "e.md").write_text(entry)
    return docs


def test_check_flags_companion_spec_slug_binding_nothing(tmp_path, capsys):
    """Invariant 7. The entries are canon's one licensed outward dependency
    and they cite back in by slug; a distillation that folds a section away
    strands the citation, and nothing read these files before."""
    docs = _mini_repo(tmp_path, "The position rests on `#folded-away`.\n")
    assert notes.check(docs) == 1
    err = capsys.readouterr().err
    assert "does not exist" in err and "folded-away" in err
    assert "exercises/e.md:1" in err


def test_check_companion_resolving_slug_is_silent(tmp_path, capsys):
    docs = _mini_repo(tmp_path, "The position rests on `#shore`.\n")
    assert notes.check(docs) == 0
    assert "does not exist" not in capsys.readouterr().err


def test_check_companion_ignores_markdown_link_fragments(tmp_path, capsys):
    entry = (
        "# Entry\n\n"
        "See [the local discussion](#discussion) and "
        "[the external method](https://example.test/paper/#methods).\n\n"
        "## Discussion {#discussion}\n\nNothing else.\n"
    )
    docs = _mini_repo(tmp_path, entry)
    assert notes.check(docs) == 0
    assert "does not exist" not in capsys.readouterr().err


def test_check_companion_ignores_backticked_url_fragments(tmp_path, capsys):
    entry = (
        "# Entry\n\nThe method is documented at "
        "`https://example.test/paper/#methods` and "
        "`https://example.test/wiki/Tide_(oceanography)#History`.\n"
    )
    docs = _mini_repo(tmp_path, entry)
    assert notes.check(docs) == 0
    assert "does not exist" not in capsys.readouterr().err


def test_check_rejects_multislug_bind_token_in_anchors(tmp_path, capsys):
    entry = (
        "# Entry\n\n**Anchors:** `ch` `@bind(shore)` `@bind(shore, absent)`.\n\nBody.\n"
    )
    docs = _mini_repo(tmp_path, entry)
    assert notes.check(docs) == 1
    err = capsys.readouterr().err
    assert "@bind takes exactly one slug" in err


def test_check_rejects_bind_token_outside_anchors(tmp_path, capsys):
    entry = (
        "# Entry\n\n**Anchors:** `ch` `@bind(shore)`.\n\n"
        "`@bind(shore)` states the position.\n"
    )
    docs = _mini_repo(tmp_path, entry)
    assert notes.check(docs) == 1
    err = capsys.readouterr().err
    assert "appears outside the **Anchors:** run" in err


def test_check_flags_companion_quote_absent_from_canon(tmp_path, capsys):
    """A companion quotation can drift from the current prose."""
    docs = _mini_repo(
        tmp_path,
        "There is an identity rule. `#shore` states it in one phrase:\n"
        "**same rulebook plus same state**. That settles it.\n",
    )
    assert notes.check(docs) == 0  # advisory, not fatal
    err = capsys.readouterr().err
    assert "appear nowhere in the work" in err
    assert "same rulebook plus same state" in err


def test_check_companion_quote_allows_case_and_trailing_punctuation(tmp_path, capsys):
    """Lifting a sentence into the middle of another lowercases its initial
    capital, and American style puts the comma inside the closing quote. Both
    are correct quotation; flagging them buried the one real hit three deep."""
    docs = _mini_repo(
        tmp_path,
        'As `#shore` has it, "these three do not gather into a '
        'single number," which settles it.\n',
        canon_prose="These three do not gather into a single number.",
    )
    assert notes.check(docs) == 0
    assert "appear nowhere in the work" not in capsys.readouterr().err


def test_check_companion_ignores_entry_subheadings(tmp_path, capsys):
    """A bold span opening a block or a list item is the entry's own
    subheading, never a quotation of the book."""
    docs = _mini_repo(
        tmp_path,
        "Reading `#shore`:\n\n"
        "**It costs the reader first.** Some argument follows here.\n\n"
        "- **The two-region immunity is stated.** And more argument.\n",
    )
    assert notes.check(docs) == 0
    assert "appear nowhere in the work" not in capsys.readouterr().err


def test_check_companion_ignores_unattributed_quotes(tmp_path, capsys):
    """The entries quote Nozick and Searle too. Attribution is read from the
    200 characters before the span, so only a quotation the entry hands to the
    book is measured against the book."""
    docs = _mini_repo(
        tmp_path,
        'Nozick puts it plainly: "a being with vastly greater capacity '
        'for satisfaction than anyone else has".\n',
    )
    assert notes.check(docs) == 0
    assert "appear nowhere in the work" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# heading attribute blocks, filters, and --check hygiene
# --------------------------------------------------------------------------- #


def test_heading_anchor_resolves_beside_classes():
    """{#id .class} keeps its anchor, and {.class} never leaks into the
    section label (Pandoc attribute blocks are not part of the heading)."""
    text = (
        "# Sources {#sec-sources .unnumbered}\n\n"
        "<!-- @todo under the anchored heading -->\n\n"
        "# Preface {.unnumbered}\n\n"
        "<!-- @ref under the class-only heading -->\n"
    )
    annos = notes.scan_text(text, "docs/x.qmd")
    assert annos[0].section == "sec-sources"
    assert annos[1].section == "Preface"


def test_headings_inside_comments_and_frontmatter_are_ignored():
    """A `# ...` line inside a banner comment or YAML frontmatter is not a
    heading; section attribution must not be shifted by one."""
    text = (
        "---\ntitle: T\n# not a heading\n---\n\n"
        "<!-- a banner comment\n# also not a heading\n-->\n\n"
        "# Real {#sec-real}\n\n<!-- @todo x -->\n"
    )
    annos = notes.scan_text(text, "docs/x.qmd")
    assert [a.section for a in annos] == ["sec-real"]


def test_slug_filter_answers_who_binds(tmp_path, capsys):
    """`notes bind --slug X` is the reverse lookup the three-tests workflow
    prescribes: which chapters bind spec section X."""
    _mini_book(tmp_path)
    rc = notes.main(["bind", "--slug", "shore", "--path", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "(shore)" in out


def test_slug_filter_typo_errors_with_close_match(tmp_path, capsys):
    _mini_book(tmp_path)
    rc = notes.main(["bind", "--slug", "shoer", "--path", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 2 and "shoer" in err and "shore" in err


def test_section_filter_typo_errors(tmp_path, capsys):
    _mini_book(tmp_path)
    rc = notes.main(["--section", "nonesuch", "--path", str(tmp_path)])
    assert rc == 2
    assert "nonesuch" in capsys.readouterr().err


def test_check_refuses_filters(capsys):
    """--check is the layout gate; combining it with a view filter would
    silently answer a different question, so it is a usage error."""
    rc = notes.main(["bind", "--check"])
    assert rc == 2
    assert "--check" in capsys.readouterr().err


def test_check_forwards_a_single_path(tmp_path, capsys):
    _mini_book(tmp_path)
    rc = notes.main(["--check", "--path", str(tmp_path)])
    assert rc == 0
    assert "sidecar layout clean" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# quotes: the phrases a mark cites must still exist in the book
# --------------------------------------------------------------------------- #


def _quotes_docs(tmp_path, chapter_prose, mark_body):
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon" / "_marks").mkdir(parents=True)
    (docs / "canon" / "01-a.qmd").write_text("# A {#sec-a}\n\n" + chapter_prose + "\n")
    (docs / "canon" / SPEC_FILE).write_text("# Spec\n\n## S {#s}\n\nbody.\n")
    (docs / "canon" / "_marks" / "01-a.md").write_text(
        "# A {#sec-a}\n\n<!-- @guidance " + mark_body + " -->\n"
    )
    return docs


def test_quotes_flags_a_citation_the_prose_no_longer_matches(tmp_path, monkeypatch):
    """A metadata quotation can drift from the prose by one word."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "By itself it explains nothing.",
        'the chapter says the sentence "by itself explains nothing"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    hits = _q.scan(docs)
    assert len(hits) == 1 and "by itself explains nothing" in hits[0].quote


def test_quotes_accepts_an_exact_citation(tmp_path, monkeypatch):
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "By itself it explains nothing.",
        'the chapter says the sentence "by itself it explains nothing"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_leaves_a_forbidden_variant_alone(tmp_path, monkeypatch):
    """Guards pin the wording that must NOT return; absence is the guard
    working, and flagging it is how an advisory teaches itself to be ignored."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "By itself it explains nothing.",
        'the line never reads "the machine drained the magic here"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_ignores_spans_that_are_not_offered_as_citations(tmp_path, monkeypatch):
    """No verbatim cue: a scare quote or a coined term, not a quotation."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "By itself it explains nothing.",
        'the whole move is a "state of states" argument here',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_checks_a_transcribed_figure_caption(tmp_path, monkeypatch):
    """`Caption: "…"` is how a @figure mark transcribes its plate, and it was
    outside the cue set, so a caption rewritten under a mark went unchecked.
    Three separate review passes had to find one such divergence by hand."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "The catching is not quiet, it is roaring.",
        'Caption: "The instrument is not quiet, it is roaring"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    hits = _q.scan(docs)
    assert len(hits) == 1 and "The instrument is not quiet" in hits[0].quote


def test_quotes_spares_wording_taken_out_of_a_caption(tmp_path, monkeypatch):
    """The LABEL form is cited; the bare noun appears in sentences ABOUT a
    caption, including ones recording wording a pass removed from it, where the
    absence is correct. Requiring the colon is what separates them."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "The catching is not quiet, it is roaring.",
        "the pass took the measured ratio out of the caption "
        '("drawn to the work each one cost")',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_reads_figure_labels_from_the_svg(tmp_path, monkeypatch):
    """A @figure mark quotes the plate as readily as the caption."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "Prose without the label.",
        'the plate reads "and not all of it" beneath',
    )
    (docs / "canon" / "illustrations").mkdir()
    (docs / "canon" / "illustrations" / "d.svg").write_text(
        "<svg><text>and not all of it</text></svg>"
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_corpus_excludes_the_marks_themselves(tmp_path, monkeypatch):
    """Folding the sidecars in would make every span match the mark it came
    from, which is what notes._canon_corpus does for the companion check."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "Unrelated prose entirely.",
        'the chapter says the sentence "a phrase only in this mark"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert len(_q.scan(docs)) == 1


# --------------------------------------------------------------------------- #
# the **Anchors:** join: an entry's anchors must name chapters that bind them
# --------------------------------------------------------------------------- #

_STEMS = {"08-the-weight-inside", "09-the-weight-outside"}
_SPEC = {"intrinsic-weight", "relational-weight"}


def test_anchor_pairs_attaches_slugs_to_the_chapter_named_before_them():
    from quirework.manuscript import notes as _n

    run = (
        "**Anchors:** `08-the-weight-inside` `@bind(intrinsic-weight)` (a\n"
        "gloss); `09-the-weight-outside` `@bind(relational-weight)`.\n"
    )
    assert _n.anchor_pairs(run, _STEMS, _SPEC) == (
        [
            ("08-the-weight-inside", "intrinsic-weight"),
            ("09-the-weight-outside", "relational-weight"),
        ],
        [],
    )


def test_anchor_pairs_reads_the_cases_bare_slug_form_with_no_chapter():
    from quirework.manuscript import notes as _n

    run = "**Anchors:** `#intrinsic-weight`, `#relational-weight`.\n"
    assert _n.anchor_pairs(run, _STEMS, _SPEC) == (
        [(None, "intrinsic-weight"), (None, "relational-weight")],
        [],
    )


def test_anchor_pairs_skips_tokens_that_are_neither():
    """An entry's run trails off into prior art; guessing at `_map.md` would
    invent an anchor nobody wrote."""
    from quirework.manuscript import notes as _n

    run = (
        "**Anchors:** `08-the-weight-inside` `@bind(intrinsic-weight)`. "
        "Prior art is filed: `_map.md` carries a row naming Nozick.\n"
    )
    assert _n.anchor_pairs(run, _STEMS, _SPEC) == (
        [("08-the-weight-inside", "intrinsic-weight")],
        [],
    )


def test_anchor_pairs_does_not_blame_a_chapter_nobody_named():
    """A slug before any stem is reported against the spec alone."""
    from quirework.manuscript import notes as _n

    run = "**Anchors:** `@bind(intrinsic-weight)` opens it.\n"
    assert _n.anchor_pairs(run, _STEMS, _SPEC) == ([(None, "intrinsic-weight")], [])


def test_anchor_pairs_stops_at_the_end_of_the_header_paragraph():
    from quirework.manuscript import notes as _n

    run = (
        "**Anchors:** `08-the-weight-inside` `@bind(intrinsic-weight)`.\n"
        "\n"
        "## The exercise\n\n`09-the-weight-outside` `@bind(relational-weight)`\n"
    )
    assert _n.anchor_pairs(run, _STEMS, _SPEC) == (
        [("08-the-weight-inside", "intrinsic-weight")],
        [],
    )


def test_stale_anchor_is_a_hard_problem(tmp_path):
    """The live defect this gates: an entry anchored to a chapter that does
    not bind the slug, which nothing detected before."""
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon" / "_marks").mkdir(parents=True)
    (docs / "canon" / "08-a.qmd").write_text("# A {#sec-a}\n\nprose.\n")
    (docs / "canon" / "09-b.qmd").write_text("# B {#sec-b}\n\nprose.\n")
    (docs / "canon" / SPEC_FILE).write_text(
        "# Spec\n\n## W {#intrinsic-weight}\n\nbody.\n"
    )
    # 08 binds it; 09 does not
    (docs / "canon" / "_marks" / "08-a.md").write_text(
        "# A {#sec-a}\n\n<!-- @bind(intrinsic-weight) -->\n"
    )
    (docs / "canon" / "_marks" / "09-b.md").write_text("# B {#sec-b}\n")
    ex = tmp_path / "exercises"
    ex.mkdir()
    (ex / "e.md").write_text(
        "# E\n\n**Anchors:** `09-b` `@bind(intrinsic-weight)`.\n\nbody.\n"
    )
    problems, _, _ = _n.companion_check(docs, _n.spec_slugs(docs), root=tmp_path)
    assert len(problems) == 1
    assert "09-b" in problems[0] and "does not bind" in problems[0]
    assert "08-a" in problems[0]  # names who does bind it


def test_correct_anchor_passes(tmp_path):
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon" / "_marks").mkdir(parents=True)
    (docs / "canon" / "08-a.qmd").write_text("# A {#sec-a}\n\nprose.\n")
    (docs / "canon" / SPEC_FILE).write_text(
        "# Spec\n\n## W {#intrinsic-weight}\n\nbody.\n"
    )
    (docs / "canon" / "_marks" / "08-a.md").write_text(
        "# A {#sec-a}\n\n<!-- @bind(intrinsic-weight) -->\n"
    )
    ex = tmp_path / "exercises"
    ex.mkdir()
    (ex / "e.md").write_text(
        "# E\n\n**Anchors:** `08-a` `@bind(intrinsic-weight)`.\n\nbody.\n"
    )
    problems, _, anchors = _n.companion_check(docs, _n.spec_slugs(docs), root=tmp_path)
    assert problems == [] and anchors == []


def test_readme_is_not_an_entry_and_owes_no_anchors(tmp_path):
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon").mkdir(parents=True)
    (docs / "canon" / SPEC_FILE).write_text("# Spec\n\n## W {#w}\n\nbody.\n")
    ex = tmp_path / "exercises"
    ex.mkdir()
    (ex / "README.md").write_text("# The collection\n\nAn index, not an entry.\n")
    _, _, anchors = _n.companion_check(docs, _n.spec_slugs(docs), root=tmp_path)
    assert anchors == []


def test_quotes_treats_past_tense_read_as_retired_wording(tmp_path, monkeypatch):
    """ "It read X and was fixed" cites wording the guard has RETIRED, so X is
    supposed to be absent. A live citation uses the present tense. Every
    standing false positive on the live book was this construction."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "By itself it explains nothing.",
        'It read "the older wording that was replaced" and was fixed',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_still_flags_the_present_tense_form(tmp_path, monkeypatch):
    """The same span under "reads" is a live claim about the prose, and false."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _quotes_docs(
        tmp_path,
        "By itself it explains nothing.",
        'the chapter reads "the older wording that was replaced"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert len(_q.scan(docs)) == 1


# --------------------------------------------------------------------------- #
# quotes: ... and in the chapter the mark says they are in
#
# Existing-in-the-book is the weaker half. A chapter split moves the prose and
# leaves the citation, so the quotation still resolves while the chapter naming
# it is wrong -- the dominant defect of two consecutive consistency rounds,
# found by hand both times.
# --------------------------------------------------------------------------- #


def _two_chapter_docs(tmp_path, mark_body, mark_in="01-a"):
    """Two chapters; the quoted line lives in `02-b` and nowhere else."""
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon" / "_marks").mkdir(parents=True)
    (docs / "canon" / "01-a.qmd").write_text("# A {#sec-a}\n\nNothing quotable here.\n")
    (docs / "canon" / "02-b.qmd").write_text(
        "# B {#sec-b}\n\nThe hull is the capacity, not the voyage.\n"
    )
    (docs / "canon" / SPEC_FILE).write_text("# Spec\n\n## S {#s}\n\nbody.\n")
    (docs / "canon" / "_marks" / f"{mark_in}.md").write_text(
        f"# {mark_in} \n\n<!-- @guidance " + mark_body + " -->\n"
    )
    return docs


def test_quotes_flags_a_line_attributed_to_the_wrong_chapter(tmp_path, monkeypatch):
    """The split defect: the words exist, but not where the mark says."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _two_chapter_docs(
        tmp_path, '@unit(01-a) reads "The hull is the capacity, not the voyage"'
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    hits = _q.scan(docs)
    assert len(hits) == 1
    assert hits[0].misattributed
    assert hits[0].target == "01-a" and hits[0].found_in == ("02-b",)


def test_quotes_accepts_a_line_attributed_to_the_right_chapter(tmp_path, monkeypatch):
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _two_chapter_docs(
        tmp_path, '@unit(02-b) reads "The hull is the capacity, not the voyage"'
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_reads_a_possessive_citation_as_naming_its_chapter(
    tmp_path, monkeypatch
):
    """`@unit(x)'s own caption reads Q` attributes Q to x exactly as `@unit(x) reads
    Q` does. Refusing the possessive form blinded the check to the live book's
    only real citation of this shape."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _two_chapter_docs(
        tmp_path,
        '@unit(02-b)\'s own caption reads "The hull is the capacity, not the voyage"',
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_does_not_read_a_displaced_subject_as_the_citation(
    tmp_path, monkeypatch
):
    """A comma and a preposition mean the sentence moved on to a new subject:
    "@unit(x)'s own positions, on the page that says Q" cites the PAGE -- this
    mark's own -- not x. Reading the nearest @unit() as the referent produced
    four candidates against the live book and every one was this mistake."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _two_chapter_docs(
        tmp_path,
        "@unit(02-b)'s own positions, on the page that says "
        '"The hull is the capacity, not the voyage"',
        mark_in="02-b",
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_quotes_attributes_a_chapterless_mark_to_the_chapter_it_annotates(
    tmp_path, monkeypatch
):
    """Naming no chapter, a mark speaks about its own -- which is what makes the
    check reach the marks a split damages without repointing them."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _two_chapter_docs(
        tmp_path, 'the chapter reads "The hull is the capacity, not the voyage"'
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    hits = _q.scan(docs)
    assert len(hits) == 1 and hits[0].target == "01-a"


def test_quotes_does_not_attribute_a_figure_label_to_a_chapter(tmp_path, monkeypatch):
    """An SVG label belongs to no chapter, so it is correct wherever it is cited
    from and must never be reported as misattributed."""
    from quirework.manuscript import notes as _n
    from quirework.manuscript import quotes as _q

    docs = _two_chapter_docs(
        tmp_path, '@unit(01-a) reads "and not all of it, only the part that signals"'
    )
    (docs / "canon" / "illustrations").mkdir()
    (docs / "canon" / "illustrations" / "d.svg").write_text(
        "<svg><text>and not all of it, only the part that signals</text></svg>"
    )
    monkeypatch.setattr(_n, "current_project", lambda: project_at(tmp_path))
    assert _q.scan(docs) == []


def test_unresolvable_chapter_reference_in_a_run_is_a_hard_problem(tmp_path):
    """Invariant 9. The old grammar ended `elif tok in stems`, so a stale
    chapter reference fell off the chain in silence: `current` kept its
    previous value and every @bind after it was attributed to the wrong
    chapter or to none, which turned invariant 8 into a no-op printing
    nothing. A rename could strand all 21 entries with every gate green."""
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon" / "_marks").mkdir(parents=True)
    (docs / "canon" / "a.qmd").write_text("# A {#sec-a}\n\nprose.\n")
    (docs / "canon" / SPEC_FILE).write_text(
        "# Spec\n\n## W {#intrinsic-weight}\n\nbody.\n"
    )
    (docs / "canon" / "_marks" / "a.md").write_text(
        "# A {#sec-a}\n\n<!-- @bind(intrinsic-weight) -->\n"
    )
    ex = tmp_path / "exercises"
    ex.mkdir()
    (ex / "e.md").write_text(
        "# E\n\n**Anchors:** `gone.qmd` `@bind(intrinsic-weight)`.\n\nbody.\n"
    )
    problems, _, _ = _n.companion_check(docs, {"intrinsic-weight"}, root=tmp_path)
    assert any("gone" in p and "no chapter" in p for p in problems)


def test_a_run_may_name_a_book_file_with_its_extension(tmp_path):
    """`sources.qmd`'s shore entry` is a real pointer, not a stale one."""
    from quirework.manuscript import notes as _n

    run = "**Anchors:** `sources.qmd`'s shore entry, which owns it.\n"
    pairs, unresolved = _n.anchor_pairs(run, {"sources"}, set())
    assert (pairs, unresolved) == ([], [])


def test_prior_art_in_a_run_stays_inert(tmp_path):
    """A run trails off into prose, and guessing at it would invent anchors.
    Only a chapter-SHAPED token that resolves to nothing is a problem."""
    from quirework.manuscript import notes as _n

    run = (
        "**Anchors:** `08-the-weight-inside` `@bind(intrinsic-weight)`. "
        "Prior art: `_map.md` carries a row naming Nozick.\n"
    )
    pairs, unresolved = _n.anchor_pairs(
        run, {"08-the-weight-inside"}, {"intrinsic-weight"}
    )
    assert unresolved == []
    assert pairs == [("08-the-weight-inside", "intrinsic-weight")]


def test_ch_is_rejected_as_a_mark_kind(tmp_path, capsys):
    """`@unit(...)` is a reference form. As a mark kind it would join the
    vocabulary silently, which is how the tag set grows by accident."""
    from quirework.manuscript import notes as _n

    docs = tmp_path / "docs"
    (docs / "canon" / "_marks").mkdir(parents=True)
    (docs / "_quarto.yml").write_text("book:\n  chapters:\n    - canon/a.qmd\n")
    (docs / "canon" / "a.qmd").write_text("# A {#sec-a}\n\nprose.\n")
    (docs / "canon" / SPEC_FILE).write_text("# Spec\n\n## W {#w}\n\nbody.\n")
    (docs / "canon" / BOOK_NOTES_FILE).write_text("# Notes\n")
    (docs / "canon" / "_marks" / "a.md").write_text(
        "# A {#sec-a}\n\n<!-- @unit(a) pointing at myself -->\n"
    )
    assert _n.check(docs) == 1
    assert "not a mark kind" in capsys.readouterr().err
