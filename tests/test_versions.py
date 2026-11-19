import pytest

from morning_brew.domain import Bump
from morning_brew.versions import classify_bump, newest_installed

FR_020 = [
    ("2.40.3_1", "2.40.3_2", Bump.REVISION),
    ("1.5.2_2", "1.5.2_3", Bump.REVISION),
    ("1.0.4_1", "1.1.0", Bump.MINOR),
    ("7.1.2-35", "7.1.2-36", Bump.REVISION),
    ("2201.14.1", "2201.14.2", Bump.PATCH),
    ("29.9.3", "29.10.0", Bump.MINOR),
    ("7.2.7", "8.10.3", Bump.MAJOR),
    ("32.1.0", "33.0.0", Bump.MAJOR),
    ("0.61.0", "0.62.0", Bump.ZERO_MAJOR_MINOR),
    ("0.121.3", "0.122.0", Bump.ZERO_MAJOR_MINOR),
    ("0.26.0", "0.26.1", Bump.PATCH),
    ("26.3.2-3", "26.9.4-2", Bump.MINOR),
    ("1.5.9,4515810895593472", "1.5.10,5253791805734912", Bump.PATCH),
    ("2.54", "3.10.0", Bump.MAJOR),
    (
        "0.2025.12.17.17.17.stable_02",
        "0.2026.11.11.08.17.stable_01",
        Bump.UNPARSEABLE,
    ),
    ("latest", "1.0", Bump.UNPARSEABLE),
]


@pytest.mark.parametrize(("installed", "current", "expected"), FR_020)
def test_fr020_table(installed, current, expected):
    assert classify_bump(installed, current) is expected


def test_empty_and_garbage_never_raise():
    assert classify_bump("", "1.0") is Bump.UNPARSEABLE
    assert classify_bump("HEAD-abc", "1.0") is Bump.UNPARSEABLE
    assert classify_bump("1.0", "") is Bump.UNPARSEABLE


def test_newest_installed():  # Review Focus 4
    assert newest_installed(["1.0.0", "1.2.0"]) == "1.2.0"
    assert newest_installed([]) is None


def test_newest_installed_numeric_not_lexicographic():
    assert newest_installed(["1.9.0", "1.10.0"]) == "1.10.0"


def test_newest_installed_falls_back_to_last_when_unparseable():
    assert newest_installed(["latest", "HEAD-abc"]) == "HEAD-abc"


def test_huge_digit_string_is_unparseable():
    huge = "1" * 5000
    assert classify_bump(huge, "1.0") is Bump.UNPARSEABLE
    assert newest_installed([huge]) == huge


def test_unicode_digits_unparseable():
    assert classify_bump("١.٠", "1.0") is Bump.UNPARSEABLE


def test_trailing_newline_unparseable():
    assert classify_bump("1.0\n", "1.0") is Bump.UNPARSEABLE


def test_padded_equal_numbers_is_revision():
    assert classify_bump("1.0", "1.0.0") is Bump.REVISION


def test_newest_installed_orders_by_suffix():
    assert newest_installed(["1.0_1", "1.0_2"]) == "1.0_2"
