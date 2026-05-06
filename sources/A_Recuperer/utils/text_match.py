"""Helpers de matching strict (artiste/album) pour le scraper BM Lyon + Qobuz.

Aligné sur le service Artistes_Similaires_Qobuz : `difflib.SequenceMatcher`
avec normalisation NFKD + ASCII + lowercase, seuil 0.85 pour l'artiste.

Remplace l'ancien `check_artist_presence` qui acceptait un match dès qu'un
seul token de l'artiste apparaissait en substring du texte (cause de très
nombreux faux positifs : "Air" matchait "Air Supply", "Worakls" matchait
"Kevin Worakls", etc.).
"""
import difflib
import re
import unicodedata


ARTIST_MATCH_THRESHOLD = 0.85
ALBUM_MATCH_THRESHOLD = 0.65   # plus tolérant : suffixes "Deluxe", "Remastered", etc.


def normalize(s: str) -> str:
    """NFKD + ASCII + lowercase + whitespace compressé."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8")
    s = re.sub(r"\s+", " ", s).lower().strip()
    return s


def name_similarity(a: str, b: str) -> float:
    """Similarité entre deux noms : 1.0 si égalité après normalisation,
    sinon ratio difflib.SequenceMatcher."""
    a_n, b_n = normalize(a), normalize(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    return difflib.SequenceMatcher(None, a_n, b_n).ratio()


def parse_bm_lyon_title(text: str) -> dict:
    """Parse un libellé de résultat catalogue.bm-lyon.fr (format ISBD).

    Format observé sur catalogue.bm-lyon.fr :
        "Titre album / Auteur. - Mention de support - Année"
    Variantes :
        "Titre / Auteur ; collaborateur. - ..."
        "Titre album / Auteur 1, Auteur 2. - ..."

    Retourne {"title": ..., "author": ..., "raw": ...}. Si le parsing échoue,
    `title` et `author` sont vides — l'appelant peut alors se rabattre sur la
    similarité globale du texte.
    """
    raw = (text or "").strip()
    if not raw:
        return {"title": "", "author": "", "raw": ""}

    # Première ligne uniquement (les liens BM ont parfois 2-3 lignes : titre,
    # type de support, date)
    first_line = raw.split("\n")[0].strip()

    # Séparateur ISBD principal : " / "
    if " / " in first_line:
        title_part, rest = first_line.split(" / ", 1)
        # L'auteur s'arrête au premier "." ou ";" (collaborateurs séparés par ";")
        author_part = re.split(r"[.;]", rest, maxsplit=1)[0].strip()
        # Couper éventuels suffixes "[texte imprimé]", "[enregistrement sonore]"
        author_part = re.sub(r"\[.*?\]", "", author_part).strip()
        # Plusieurs auteurs séparés par virgule → on garde le premier
        author_part = author_part.split(",")[0].strip()
        return {
            "title": title_part.strip(),
            "author": author_part,
            "raw": raw,
        }

    # Pas de séparateur ISBD : on renvoie le texte brut comme titre, sans
    # auteur ; le scoring se rabattra sur la similarité globale.
    return {"title": first_line, "author": "", "raw": raw}


def score_bm_lyon_candidate(result_text: str, target_artist: str, target_album: str) -> tuple[float, dict]:
    """Score combiné d'un candidat BM Lyon contre une cible (artist, album).

    Retourne (score ∈ [0, 1], parsed_dict). Le score est une moyenne pondérée :
        0.55 × sim(album, parsed.title) + 0.45 × sim(artist, parsed.author)
    Si l'auteur n'a pas pu être parsé, on retombe sur :
        max(sim(album, raw), sim(artist+album, raw))
    """
    parsed = parse_bm_lyon_title(result_text)

    if parsed["author"]:
        s_album = name_similarity(target_album, parsed["title"])
        s_artist = name_similarity(target_artist, parsed["author"])
        score = 0.55 * s_album + 0.45 * s_artist
        return score, parsed

    # Fallback : pas de séparateur ISBD trouvé
    full_target = f"{target_artist} {target_album}".strip()
    s_full = name_similarity(full_target, parsed["raw"])
    s_album = name_similarity(target_album, parsed["raw"])
    return max(s_full, s_album), parsed
