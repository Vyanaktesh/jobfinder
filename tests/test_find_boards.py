import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from find_h1b_boards import slug_candidates  # noqa: E402


def test_slugs_for_multiword_name():
    c = slug_candidates("Palo Alto Networks, Inc.")
    assert c[0] == "paloaltonetworks" and "palo-alto-networks" in c and "palo" in c


def test_ampersand_and_short_names():
    assert "johnsonandjohnson" in slug_candidates("Johnson & Johnson")
    assert slug_candidates("X") == []
