"""Neutral grayscale conversion, over the hex forms SVG actually carries.

The e-ink edition is generated from the color set at build time, so a color
that survives the transform reaches a reader and nothing in the source looks
wrong. The check that catches it reads the same forms as the transform, which
is the property these tests hold: whatever the transform cannot convert, the
check can still see.
"""

from quirework.book import graybuild


def gray(text):
    return graybuild.to_gray_svg(f'<svg xmlns="http://www.w3.org/2000/svg">{text}</svg>')


def test_six_digit_color_becomes_neutral():
    assert 'fill="#292929"' in gray('<rect fill="#2a2732"/>')


def test_three_digit_color_becomes_neutral():
    """`#fff` used to pass through the transform and the check alike."""
    assert 'fill="#ffffff"' in gray('<rect fill="#fff"/>')
    assert 'fill="#4c4c4c"' in gray('<rect fill="#f00"/>')


def test_alpha_survives_the_conversion():
    assert 'stop-color="#4f4f4f80"' in gray('<stop stop-color="#8a3b1f80"/>')
    # The short form doubles every digit, alpha included: #f008 is #ff000088.
    assert 'stop-color="#4c4c4c88"' in gray('<stop stop-color="#f008"/>')


def test_something_that_is_not_a_color_is_left_alone():
    assert 'href="#abcde"' in gray('<use href="#abcde"/>')


def test_the_check_sees_every_form_the_transform_does():
    assert graybuild.non_gray_hexes('fill="#f00" stroke="#fff"') == {"#f00"}
    assert graybuild.non_gray_hexes('fill="#2a2732" stroke="#292929"') == {"#2a2732"}
    assert graybuild.non_gray_hexes('fill="#8a3b1f80"') == {"#8a3b1f80"}
    assert graybuild.non_gray_hexes('href="#abcde"') == set()
