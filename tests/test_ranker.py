"""clean_title — safe, conservative normalization of museum-supplied titles for display."""

from result_ranker import clean_title


def test_strips_leading_language_label():
    assert clean_title("Dutch: Meisje met de parel") == "Meisje met de parel"
    assert clean_title("Title: The Starry Night") == "The Starry Night"


def test_strips_wikidata_qs_suffix():
    assert clean_title('Gismonda QS:P1476,en:"Gismonda"') == "Gismonda"


def test_collapses_whitespace():
    assert clean_title("The   yellow    house") == "The yellow house"


def test_leaves_clean_and_foreign_titles_untouched():
    # No translation, no guessing — a legitimate non-English title is preserved verbatim.
    assert clean_title("La Nuit étoilée") == "La Nuit étoilée"
    assert clean_title("Girl with a Pearl Earring") == "Girl with a Pearl Earring"


def test_handles_empty_and_none():
    assert clean_title("") == ""
    assert clean_title(None) is None
