import re
import math
import pandas as pd
from unidecode import unidecode
from rapidfuzz import process, fuzz


def clean_albums(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = unidecode(text.lower())
    terms_to_remove = r'\b(?:deluxe|radio\s+edit|edit|ep)\b'
    text = re.sub(terms_to_remove, ' ', text)
    text = re.sub(r'\b(?:feat|disque)\b.*', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def clean_artist(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    text = text.split(',')[0]
    text = re.sub(r'\[.*?\]|\(.*?\)|\{.*?\}', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = unidecode(text.lower())
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def match_albums_with_fuzz(
    df_a_tester: pd.DataFrame,
    df_ressource: pd.DataFrame,
    name_tester: str,
    name_ressource: str,
    artist_similarity_threshold: int = 90,
    artist_match_col: str | None = None,
    album_match_col: str | None = None,
) -> pd.DataFrame:
    """Fuzzy matching artiste+album entre deux DataFrames.

    Par défaut, le scoring fuzzy s'applique sur les colonnes `Artist` et
    `Album` (noms bruts). Les paramètres optionnels `artist_match_col` et
    `album_match_col` permettent de matcher sur des versions normalisées
    (typiquement `Artist_clean`/`Album_clean` produites par `clean_artist`
    et `clean_albums`) tout en restituant les noms bruts dans la sortie.
    Sans cleaning, "Témé Tan" et "Teme Tan" ne matchent pas alors qu'ils
    désignent le même artiste.
    """
    a_col = artist_match_col or 'Artist'
    b_col = album_match_col  or 'Album'

    # Mapping (valeur fuzzy-cible) -> (valeur brute) pour le côté ressource :
    # une même valeur cleanée peut correspondre à plusieurs versions brutes,
    # on prend la première rencontrée pour l'affichage.
    artist_match_to_raw: dict[str, str] = {}
    for raw, match in zip(df_ressource['Artist'].astype(str), df_ressource[a_col].astype(str)):
        if match not in artist_match_to_raw:
            artist_match_to_raw[match] = raw

    artist_choices = df_ressource[a_col].astype(str).tolist()

    results = []
    for _, row in df_a_tester.iterrows():
        best_artist_match = process.extractOne(
            str(row[a_col]),
            artist_choices,
            scorer=fuzz.token_sort_ratio,
        )
        sim_artist = int(round(best_artist_match[1])) if best_artist_match else 0
        artist_match_value = best_artist_match[0] if best_artist_match else ""
        artist_display = artist_match_to_raw.get(artist_match_value, artist_match_value)

        best_album_match = None
        sim_album = 0
        album_display = ""
        liste_albums_str = ""

        if sim_artist >= artist_similarity_threshold:
            df_filtered = df_ressource[df_ressource[a_col] == artist_match_value]
            if not df_filtered.empty:
                album_choices = [str(x) for x in df_filtered[b_col].tolist()]
                # Mapping local cleaned->raw pour cet artiste (évite collisions
                # inter-artistes : un même nom d'album cleané peut exister
                # chez plusieurs artistes).
                album_match_to_raw_local = {
                    str(m): str(r)
                    for r, m in zip(df_filtered['Album'], df_filtered[b_col])
                }
                # Liste affichée : versions brutes
                liste_albums_str = " - ".join(str(x) for x in df_filtered['Album'].tolist())
                best_album_match = process.extractOne(
                    str(row[b_col]),
                    album_choices,
                    scorer=fuzz.token_sort_ratio,
                )
                if best_album_match:
                    sim_album = int(round(best_album_match[1]))
                    album_display = album_match_to_raw_local.get(
                        best_album_match[0], best_album_match[0]
                    )

        results.append({
            f"Artist_{name_tester}": row['Artist'],
            f"Artist_{name_ressource}": artist_display,
            "Artist_sim": sim_artist,
            f"Album_{name_tester}": row['Album'],
            f"Album_{name_ressource}": album_display,
            "Album_sim": sim_album,
            "Liste_albums_pos": liste_albums_str,
        })

    column_order = [
        f"Artist_{name_tester}", f"Artist_{name_ressource}", "Artist_sim",
        f"Album_{name_tester}", f"Album_{name_ressource}", "Album_sim",
        "Liste_albums_pos",
    ]
    return pd.DataFrame(results, columns=column_order)


def get_percentage(count: int, total: int) -> int:
    if total == 0:
        return 0
    return math.trunc((count / total) * 100)
