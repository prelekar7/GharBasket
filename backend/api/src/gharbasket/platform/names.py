"""A person's name as other people see it: one rule for M1's profile name and M8's display name.

The name is trimmed and each run of whitespace becomes one space, then it must be 2 to 40
characters. Characters are Unicode code points, so a short Devanagari name such as "सु" (two
code points) passes. Names saved before this rule (M1 allowed 80) stay readable; they are only
checked when someone changes them (#492).
"""

NAME_MIN = 2
NAME_MAX = 40


def tidy_name(value: str) -> str:
    """Trimmed, with inner whitespace collapsed; empty when there was nothing but whitespace."""
    return " ".join(value.split())


def is_valid_name(tidy: str) -> bool:
    """Whether an already tidied name is 2 to 40 characters."""
    return NAME_MIN <= len(tidy) <= NAME_MAX


def check_name(value: str) -> str:
    """The tidied name, or ValueError (a 422 `request.invalid` inside a request body)."""
    tidy = tidy_name(value)
    if not is_valid_name(tidy):
        raise ValueError(f"a name is {NAME_MIN} to {NAME_MAX} characters")
    return tidy
