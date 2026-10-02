import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from find_h1b_boards import slug_candidates  # noqa: E402


def test_slugs_for_multiword_name():
    c = slug_candidates("Palo Alto Networks, Inc.")
    assert c[0] == "paloaltonetworks" and "palo-alto-networks" in c


def test_ampersand_and_short_names():
    assert "johnsonandjohnson" in slug_candidates("Johnson & Johnson")
    assert slug_candidates("X") == []


def test_no_single_first_word_slug_for_multiword_names():
    for name in ("Charles Schwab", "General Dynamics", "Pure Storage"):
        first = name.split()[0].lower()
        assert first not in slug_candidates(name)


def test_single_word_names_still_work():
    assert "stripe" in slug_candidates("Stripe Inc")
    assert "palantir" in slug_candidates("Palantir Technologies")
