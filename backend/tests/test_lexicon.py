import pytest

from app.core.lexicon import match_names


@pytest.fixture(scope="module")
def cands(ds):
    return list(zip(ds.items["item_id"], ds.items["name"]))


@pytest.mark.parametrize("query,expected", [
    ("aata 5kg", "I001"),
    ("cheeni", "I009"),
    ("sarson ka tel", "I016"),
    ("toor daal", "I005"),
    ("arhar dal", "I005"),
    ("doodh", None),  # any milk
    ("kaju", "I062"),
    ("I015", "I015"),
])
def test_hinglish_matching(cands, query, expected):
    hits = match_names(query, cands)
    assert hits, query
    if expected:
        assert hits[0]["id"] == expected
    else:
        assert "Milk" in hits[0]["name"]


def test_nonsense_returns_nothing(cands):
    assert match_names("xyzzy qwerty", cands) == []
