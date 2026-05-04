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
) -> pd.DataFrame:
    results = []
    artist_choices = df_ressource['Artist'].tolist()

    for _, row in df_a_tester.iterrows():
        best_artist_match = process.extractOne(
            str(row['Artist']),
            artist_choices,
            scorer=fuzz.token_sort_ratio,
        )
        sim_artist = int(round(best_artist_match[1])) if best_artist_match else 0
        artist_match = best_artist_match[0] if best_artist_match else ""

        best_album_match = None
        sim_album = 0
        album_match = ""
        liste_albums_str = ""

        if sim_artist >= artist_similarity_threshold:
            df_filtered = df_ressource[df_ressource['Artist'] == artist_match]
            if not df_filtered.empty:
                album_choices = [str(x) for x in df_filtered['Album'].tolist()]
                liste_albums_str = " - ".join(album_choices)
                best_album_match = process.extractOne(
                    str(row['Album']),
                    album_choices,
                    scorer=fuzz.token_sort_ratio,
                )

        if best_album_match:
            sim_album = int(round(best_album_match[1]))
            album_match = best_album_match[0]

        results.append({
            f"Artist_{name_tester}": row['Artist'],
            f"Artist_{name_ressource}": artist_match,
            "Artist_sim": sim_artist,
            f"Album_{name_tester}": row['Album'],
            f"Album_{name_ressource}": album_match,
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
