"""A review report's quotations, looked for in the sources they cite.

Every source here is fiction written for the test, in a synthetic repository.
"""

import json
import subprocess

import pytest

from quirework.project import Project, project_scope
from quirework.sops import quotes, review

GUIDE = """\
# The kettle guide

A kettle boils when the water inside it reaches the boiling point, and
the whistle sounds once the steam finds the spout (`#steam-path`).

- **Ada Kettle**: the "first" kettle of the house, kept on the back burner
  and filled every morning.
"""

NOTES = """\
The second kettle was never filled at all.
It sat on the shelf all winter.
"""


def git(repo, *argv):
    out = subprocess.run(
        ["git", "-C", str(repo), *argv],
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "HOME": str(repo),
            "PATH": "/usr/bin:/bin",
        },
    )
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "book"
    (root / "docs" / "deep").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "docs" / "guide.md").write_text(GUIDE)
    (root / "docs" / "deep" / "notes.md").write_text(NOTES)
    git(root, "add", ".")
    git(
        root,
        "commit",
        "-qm",
        "Add the kettle guide\n\nThe kettle is the house's only clock.",
    )
    return root


def check(repo, body, **kw):
    return quotes.check(body, repo, **kw)


def test_a_blockquote_its_file_holds_is_found_and_one_it_does_not_is_a_lead(repo):
    body = """\
1. **CONFIRMED.** The guide says when a kettle boils.
   - `docs/guide.md:3-4`:
     > A kettle boils when the water inside it reaches the boiling point, and the whistle sounds once the steam finds the spout (`#steam-path`).
   - `docs/guide.md:5`:
     > A kettle sings before it boils, as every cook knows.
"""
    result = check(repo, body)
    assert (result["checked"], result["found"], result["unchecked"]) == (2, 1, 0)
    (lead,) = result["leads"]
    assert lead["line"] == 5 and lead["kind"] == "block"
    assert lead["cited"] == ["docs/guide.md"]
    assert lead["why"] == "not in the source it cites"
    assert lead["quote"].startswith("A kettle sings before it boils")


def test_inline_quotations_take_every_source_their_paragraph_cites(repo):
    # A bare `:5` is a line in the file the report named last; the source
    # may follow the quotation; curly and straight marks both quote.
    body = (
        "The guide at `docs/guide.md:3` says, “the whistle sounds once the "
        'steam finds the spout”, and at `:5` it calls Ada "the first kettle '
        'of the house" (`docs/guide.md:6`). It also says “the kettle is '
        "always cold in the morning”.\n"
    )
    result = check(repo, body)
    assert result["checked"] == 3 and result["found"] == 2
    (lead,) = result["leads"]
    assert lead["kind"] == "inline" and lead["line"] == 1
    assert lead["quote"] == "the kettle is always cold in the morning"


@pytest.mark.parametrize(
    "quoted",
    [
        # The source hard-wrapped, the report on one line.
        "the water inside it reaches the boiling point, and the whistle sounds",
        # A section anchor backticked in one and plain in the other.
        "the steam finds the spout (#steam-path)",
        # Inner double marks swapped for single ones, emphasis dropped.
        "Ada Kettle: the 'first' kettle of the house",
        "**Ada Kettle**: the “first” kettle of the house",
        # An anchor the reviewer appended to say where the sentence came from.
        "kept on the back burner and filled every morning (`#kettles`)",
        # Pieces around an ellipsis, a bracketed insertion and a line break.
        "A kettle boils when the water ... finds the spout",
        "A kettle boils when [the] water inside it reaches the boiling point",
        "kept on the back burner / and filled every morning",
    ],
)
def test_a_report_that_reformats_what_it_quotes_still_finds_it(repo, quoted):
    body = f"- `docs/guide.md`:\n  > {quoted}\n"
    result = check(repo, body)
    assert result["leads"] == [], result
    assert result["found"] == 1


def test_a_blockquote_of_two_places_is_two_pieces(repo):
    body = """\
- `docs/guide.md:3` and `docs/deep/notes.md:1`:
  > A kettle boils when the water inside it reaches the boiling point
  >
  > The second kettle was never filled at all.
"""
    assert check(repo, body)["found"] == 1
    lone = body.replace(
        "The second kettle was never filled", "The third kettle was always full"
    )
    (lead,) = check(repo, lone)["leads"]
    assert lead["why"] == "not in any of the 2 sources it cites (1 of 2 pieces found)"


def test_a_quotation_of_text_the_delta_removed_says_so(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "docs" / "deep" / "notes.md").write_text("The second kettle is gone.\n")
    git(repo, "commit", "-qam", "Retire the second kettle")
    body = "`docs/deep/notes.md:1` says “The second kettle was never filled at all.”\n"
    (lead,) = check(repo, body, base=base)["leads"]
    assert lead["why"].startswith(f"in what it cites as it stood at {base[:9]}")
    # Read at the revision the review read, it is simply found.
    assert check(repo, body, at=base)["leads"] == []


def test_a_commit_is_a_source_and_a_bare_name_finds_its_one_file(repo):
    sha = git(repo, "rev-parse", "--short", "HEAD")
    body = (
        f"Commit `{sha}` says “The kettle is the house's only clock.”\n\n"
        "`notes.md` says “It sat on the shelf all winter.”\n"
    )
    result = check(repo, body)
    assert result["found"] == 2 and result["leads"] == []
    extracted = quotes.extract(body, repo, quotes.Sources(repo).resolve)
    assert extracted[0].cited == [f"commit:{sha}"]
    assert extracted[1].cited == ["docs/deep/notes.md"]


def test_what_cannot_be_looked_for_is_counted_and_never_a_lead(repo):
    body = (
        # A short quoted term is a term, not a quotation.
        "`docs/guide.md` calls it the “first” kettle.\n\n"
        # Neither "e.g" nor a web address is a file in the checkout.
        "As e.g. example.org/kettles.pdf has it, “every kettle in the street "
        "boiled at once”.\n\n"
        "```\n> A fenced kettle that no file holds at all, ever.\n```\n"
    )
    result = check(repo, body)
    assert result == {"checked": 0, "found": 0, "unchecked": 1, "leads": []}
    assert quotes.summary(result) == (
        "quotations: 0 checked against the sources their paragraphs cite; "
        "1 cite no source and were not checked"
    )


def test_a_cited_path_that_names_no_file_is_a_lead(repo):
    body = "`docs/missing.md:2` says “the kettle was sold in the spring market”.\n"
    (lead,) = check(repo, body)["leads"]
    assert lead["why"] == "cites no source that exists: docs/missing.md"


def test_the_summary_says_leads_are_not_verdicts():
    assert quotes.summary({"checked": 3, "found": 3, "unchecked": 0, "leads": []}) == (
        "quotations: 3 checked against the sources their paragraphs cite, all found"
    )
    line = quotes.summary(
        {"checked": 3, "found": 2, "unchecked": 0, "leads": [{"line": 1}]}
    )
    assert "1 NOT FOUND there" in line and "never verdicts" in line
    assert quotes.summary({"checked": 0, "found": 0, "unchecked": 0, "leads": []}) == (
        "quotations: none in the report"
    )
    assert quotes.summary({"error": "OSError: gone"}) == (
        "QUOTATIONS NOT CHECKED: OSError: gone"
    )


RUN = "2026-01-01-review-kettles"


def _bank(repo, report):
    """Collect one completed codex turn whose report quotes the guide."""
    record = repo / ".state" / RUN
    (repo / "records" / RUN).mkdir(parents=True)
    turn = record / "turns" / "01"
    (turn / "report").mkdir(parents=True)
    (turn / "report" / "report.md").write_text(report)
    (turn / "events.jsonl").write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"type": "thread.started", "thread_id": "s-1"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "done"},
                },
                {"type": "turn.completed", "usage": {}},
            )
        )
    )
    (record / "run.json").write_text(
        json.dumps(
            {
                "run": RUN,
                "config": {"harness": "codex", "model": "demo-model", "effort": "high"},
                "delta": {"kind": "worktree", "paths": ["docs/guide.md"]},
                "turns": [],
            }
        )
    )
    tree = {"head": git(repo, "rev-parse", "HEAD"), "status": ""}
    return review._collect(
        RUN,
        "01",
        tree,
        tree,
        0.0,
        store=record.parent,
        jailed=False,
        report_dir=turn / "report",
    )


def test_a_banked_turn_lists_the_quotations_it_could_not_find(repo, capsys):
    report = (
        "STATUS: complete\n\n## F1 The whistle\n\n"
        "`docs/guide.md:4` says “the whistle sounds once the steam finds the spout”"
        " and “the whistle never sounds on a cold morning”.\n"
    )
    with project_scope(Project.from_settings(repo, {"runs": {"reviews": "records"}})):
        out = _bank(repo, report)
        assert (
            "quotations: 2 checked against the sources their paragraphs cite, "
            "1 NOT FOUND there"
        ) in " | ".join(out["checks"])
        assert "Start with the 1 quotation the report carries" in out["next"]
        assert f"quirework sop review quotes {RUN}" in out["next"]
        err = capsys.readouterr().err
        assert (
            "report line 5: not in the source it cites: the whistle never sounds" in err
        )
        (turn,) = review.status(RUN, store=repo / ".state")["turns"]
        assert "1 NOT FOUND there" in turn["quotations"]
        record = json.loads((repo / ".state" / RUN / "run.json").read_text())
        assert record["turns"][0]["quotations"]["leads"][0]["line"] == 5
        # Checked again later, against the revision the review read.
        again = review.requote(RUN, at="HEAD", store=repo / ".state")
        assert again["read_at"] == "HEAD"
        assert again["turns"][0]["checked"] == 2
        assert len(again["turns"][0]["leads"]) == 1
        with pytest.raises(ValueError, match="no banked report"):
            review.requote(RUN, turn="09", store=repo / ".state")


def test_a_banked_turn_whose_quotations_all_hold_asks_nothing_extra(repo, capsys):
    report = (
        "STATUS: complete\n\n"
        "`docs/guide.md:4` says “the whistle sounds once the steam finds the spout”.\n"
    )
    with project_scope(Project.from_settings(repo, {"runs": {"reviews": "records"}})):
        out = _bank(repo, report)
    assert (
        "quotations: 1 checked against the sources their paragraphs cite, all found"
        in out["checks"]
    )
    assert "Start with" not in out["next"]
    assert "NOT FOUND" not in capsys.readouterr().err
