"""Hermetic tests for the EPUBCheck output reader (no java, no jar).

The validator itself is the W3C reference implementation and is not this
project's to test. What IS this project's is the reading of its output, and
that has one job worth proving: a systematic defect reports once per document,
so the report has to COLLAPSE those rows or the count that identifies the kind
of problem is buried under thirty-eight copies of it. A real book found the
case that motivated this -- one bad font url reported in every content
document at once.

The fixtures are real EPUBCheck lines, trimmed only in the message body.
"""

import io
from contextlib import redirect_stdout

from quirework.publish import epubcheck as ec

FONT_ERR = (
    "ERROR(RSC-007): /b/book.epub/EPUB/text/ch007.xhtml(331,5): "
    'Referenced resource "EPUB/text/fonts/Serif-Regular.woff2" '
    "could not be found in the EPUB."
)
FONT_ERR_OTHER_FILE = FONT_ERR.replace("ch007", "ch008")
NAV_ERR = (
    "ERROR(RSC-007): /b/book.epub/EPUB/nav.xhtml(331,5): "
    'Referenced resource "fonts/Serif-Regular.woff2" '
    "could not be found in the EPUB."
)
WARNING = (
    "WARNING(HTM-054): /b/book.epub/EPUB/text/ch001.xhtml(12,3): "
    "Custom attribute without a declared prefix."
)


def test_parses_severity_code_location_and_message():
    ((severity, code, where, message),) = ec.parse(FONT_ERR)
    assert severity == "ERROR"
    assert code == "RSC-007"
    assert where == "EPUB/text/ch007.xhtml"
    assert message.startswith('Referenced resource "EPUB/text/fonts/')
    # The line:column is part of the location, never of the message.
    assert "331" not in message


def test_ignores_non_finding_lines():
    noise = "Validating using EPUB version 3.3 rules.\n\nCheck finished\n"
    assert ec.parse(noise) == []


def test_same_defect_in_two_files_groups_to_one_row_with_a_count():
    rows = ec.parse("\n".join([FONT_ERR, FONT_ERR_OTHER_FILE]))
    assert len(rows) == 2  # two findings ...
    out = io.StringIO()
    with redirect_stdout(out):
        ec.report(rows)
    text = out.getvalue()
    assert "x2" in text  # ... reported as one row
    assert text.count("RSC-007") == 1


def test_same_code_different_message_stays_separate():
    """The nav and text documents fail on DIFFERENT urls; that distinction is
    the whole diagnosis, so a group key of code alone would destroy it."""
    rows = ec.parse("\n".join([FONT_ERR, NAV_ERR]))
    out = io.StringIO()
    with redirect_stdout(out):
        ec.report(rows)
    assert out.getvalue().count("RSC-007") == 2


def test_report_orders_errors_before_warnings():
    rows = ec.parse("\n".join([WARNING, FONT_ERR]))
    out = io.StringIO()
    with redirect_stdout(out):
        ec.report(rows)
    text = out.getvalue()
    assert text.index("ERROR") < text.index("WARNING")


def test_long_message_is_elided():
    """RSC-005 quotes the entire permitted-attribute list -- about 2 KB."""
    long_msg = (
        "ERROR(RSC-005): /b/book.epub/EPUB/text/ch007.xhtml(511,464): "
        'attribute "alt" not allowed here; expected ' + "x " * 900
    )
    out = io.StringIO()
    with redirect_stdout(out):
        ec.report(ec.parse(long_msg))
    line = out.getvalue().splitlines()[0]
    assert line.endswith("...")
    assert len(line) < 200


def test_only_fatal_and_error_are_failing_severities():
    assert set(ec.FAIL) == {"FATAL", "ERROR"}
    rows = ec.parse("\n".join([WARNING]))
    assert [r for r in rows if r[0] in ec.FAIL] == []


def test_verify_is_a_noop_when_the_site_has_no_epub(tmp_path):
    assert ec.verify(tmp_path) == 0
