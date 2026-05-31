"""Tests purs de text_match (aucune dépendance Playwright/pandas).

Lancement :
    cd sources/A_Recuperer/utils
    python3 -m pytest test_text_match.py -q
    # ou simplement : python3 test_text_match.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from text_match import (
    ARTIST_MATCH_THRESHOLD,
    artist_name_matches,
    name_similarity,
    parse_bm_lyon_title,
)


# ---------------------------------------------------------------------------
# artist_name_matches — cœur du correctif Autres_albums_biblio
# ---------------------------------------------------------------------------

def test_exact_match():
    assert artist_name_matches("ASPHALT", "ASPHALT")


def test_rejects_unrelated_real_world_false_positives():
    """Les faux positifs réels observés sous l'artiste 'ASPHALT' doivent être
    rejetés (c'est le bug d'origine de Autres_albums_biblio)."""
    assert not artist_name_matches("Amusement Parks on Fire", "ASPHALT")
    assert not artist_name_matches("Le Meilleur des mondes", "ASPHALT")
    assert not artist_name_matches("Demain le sud", "ASPHALT")


def test_token_order_insensitive():
    """Inversion prénom/nom du catalogue BM Lyon ('Cosma, Vladimir')."""
    assert artist_name_matches("Cosma, Vladimir", "Vladimir Cosma")


def test_subset_tolerance_when_allowed():
    """'Bourvil' ⊂ 'André Bourvil' accepté seulement si allow_subset=True."""
    assert artist_name_matches("Bourvil", "Andre Bourvil", allow_subset=True)
    assert not artist_name_matches("Bourvil", "Andre Bourvil", allow_subset=False)


def test_subset_rejected_for_short_tokens():
    """'Air' ⊂ 'Air Supply' refusé même avec allow_subset (token < 5 chars)."""
    assert not artist_name_matches("Air", "Air Supply", allow_subset=True)


def test_empty_inputs():
    assert not artist_name_matches("", "ASPHALT", allow_subset=True)
    assert not artist_name_matches("ASPHALT", "", allow_subset=True)


# ---------------------------------------------------------------------------
# parse_bm_lyon_title — non-régression (le trou venait des libellés sans " / ")
# ---------------------------------------------------------------------------

def test_parse_isbd_with_author():
    p = parse_bm_lyon_title("Lame de fond [Disque compact] / ASPHALT. - 2019")
    assert p["author"] == "ASPHALT"
    assert "Lame de fond" in p["title"]


def test_parse_without_separator_has_no_author():
    """Libellé sans ' / ' → author vide. C'est ce cas qui laissait fuiter des
    albums : le filtre amont ne pouvait pas trancher, d'où la re-vérification
    obligatoire sur la fiche détail (_bm_lyon_detail_artist_matches)."""
    p = parse_bm_lyon_title("Compilation jazz [Disque compact]")
    assert p["author"] == ""


if __name__ == "__main__":
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"  OK   {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}  {e}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
