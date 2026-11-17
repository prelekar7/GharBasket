"""The shared name rule (platform/names.py, #492): M1's profile name and M8's display name."""

import pytest

from gharbasket.platform.names import NAME_MAX, NAME_MIN, check_name, is_valid_name, tidy_name


def test_tidy_name_trims_and_collapses_inner_whitespace() -> None:
    assert tidy_name("  Priya \t  Sharma\n") == "Priya Sharma"
    assert tidy_name("   ") == ""


@pytest.mark.parametrize(
    ("raw", "tidy"),
    [
        ("Ra", "Ra"),
        ("सु", "सु"),  # two code points: स and the vowel sign ु
        ("मीना ताई", "मीना ताई"),
        (" " + "x" * NAME_MAX + " ", "x" * NAME_MAX),
        ("a  b", "a b"),
    ],
)
def test_names_of_2_to_40_characters_pass(raw: str, tidy: str) -> None:
    assert check_name(raw) == tidy
    assert is_valid_name(tidy)


@pytest.mark.parametrize("raw", ["", "   ", "P", " P ", "x" * (NAME_MAX + 1)])
def test_names_outside_the_bounds_are_refused(raw: str) -> None:
    with pytest.raises(ValueError, match=f"{NAME_MIN} to {NAME_MAX}"):
        check_name(raw)
