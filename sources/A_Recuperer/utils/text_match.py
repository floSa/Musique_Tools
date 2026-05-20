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
    sinon max entre SequenceMatcher direct ET version "tokens triés".

    La version tokens-triés gère l'inversion prénom/nom (catalogue BM Lyon
    écrit souvent "Cosma, Vladimir" alors que Spotify dit "Vladimir Cosma").
    Sans ce fallback, `SequenceMatcher.ratio("vladimir cosma", "cosma
    vladimir") ≈ 0.50`, et l'artiste serait rejeté à tort.
    """
    a_n, b_n = normalize(a), normalize(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    direct = difflib.SequenceMatcher(None, a_n, b_n).ratio()
    # Match insensible à l'ordre des tokens
    a_sorted = " ".join(sorted(a_n.split()))
    b_sorted = " ".join(sorted(b_n.split()))
    if a_sorted == b_sorted:
        return 1.0
    token_sorted = difflib.SequenceMatcher(None, a_sorted, b_sorted).ratio()
    return max(direct, token_sorted)


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
        # Titre : retirer mentions de support type "[Disque compact]",
        # "[Disque microsillon]", "[enregistrement sonore]"… (utiles pour
        # filtrer en amont, mais pas pour matcher le titre Spotify).
        title_clean = re.sub(r"\[.*?\]", "", title_part)
        # Retirer aussi les annotations entre parenthèses dans le titre
        # ("(Bande originale du film)", "(Edition Deluxe)") car Spotify ne
        # les a généralement pas exactement à l'identique.
        title_clean = re.sub(r"\(.*?\)", "", title_clean)
        title_clean = re.sub(r"\s+", " ", title_clean).strip()

        # L'auteur s'arrête au premier "." ou ";" (collaborateurs séparés par ";")
        author_part = re.split(r"[.;]", rest, maxsplit=1)[0].strip()
        # Retirer "[texte imprimé]" etc.
        author_part = re.sub(r"\[.*?\]", "", author_part).strip()
        # Retirer "(groupe)", "(chanteur)", "(compositeur)"… qui suivent
        # parfois le nom dans le catalogue BM Lyon.
        author_part = re.sub(r"\(.*?\)", "", author_part).strip()
        # Plusieurs auteurs séparés par virgule → on garde le premier
        author_part = author_part.split(",")[0].strip()
        return {
            "title": title_clean,
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
