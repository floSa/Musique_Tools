"""Tests purs de text_match (aucune dépendance Playwright/pandas).

Lancement :
    cd sources/A_Recuperer/utils
    python3 -m pytest test_text_match.py -q
    # ou simplement : python3 test_text_match.py
"""
import ast
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
# Garde-fou anti-régression : aucune fonction appelée sans être définie dans
# scraper.py. Cause du bug "biblio catastrophique" (commit 3b7054d) :
# `_bm_lyon_detail_artist_matches` était appelée mais jamais définie ; le
# NameError était avalé par les `try/except`, donc 100% des albums BM Lyon
# étaient rejetés en silence. On vérifie par AST (sans importer Playwright).
# ---------------------------------------------------------------------------

def test_scraper_no_phantom_function_calls():
    import builtins
    src = (Path(__file__).parent / "scraper.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            imported |= {a.asname or a.name for a in n.names}
        elif isinstance(n, ast.Import):
            imported |= {(a.asname or a.name).split(".")[0] for a in n.names}
    # noms assignés au niveau module/local (variables qui pourraient être des callables)
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    known = defined | imported | assigned | set(dir(builtins))
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    phantom = sorted(called - known)
    assert not phantom, f"Fonctions appelées mais jamais définies dans scraper.py : {phantom}"


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
