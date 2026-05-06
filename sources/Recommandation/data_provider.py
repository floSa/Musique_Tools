"""
Chargement des données avec cache Streamlit.

Toutes les fonctions sont décorées `@st.cache_data` : Streamlit les exécute une
seule fois par session et garde le résultat en mémoire. Les caches sont invalidés
automatiquement si le code des fonctions change.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import (
    compute_artist_popularity,
    history_minutes,
    load_history,
    load_lastfm_similar,
    load_lastfm_tags,
    load_qobuz_portraits,
    load_qobuz_similar,
    load_spotify_id_index,
    load_spotify_similar,
)

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"


@st.cache_data
def get_history() -> pd.DataFrame:
    return load_history(DATA / "Historique_Spotify")


@st.cache_data
def get_history_minutes() -> dict[str, float]:
    return history_minutes(get_history())


@st.cache_data
def get_lastfm_similar() -> dict[str, list[dict]]:
    return load_lastfm_similar(
        DATA / "Artistes_Similaires_LastFM" / "similar_artists.db"
    )


@st.cache_data
def get_lastfm_tags() -> dict[str, list[str]]:
    return load_lastfm_tags(
        DATA / "Artistes_Similaires_LastFM" / "similar_artists.db"
    )


@st.cache_data
def get_spotify_similar() -> dict[str, list[dict]]:
    return load_spotify_similar(
        DATA / "Artistes_Similaires_Spotify" / "similar_artists.db"
    )


@st.cache_data
def get_qobuz_similar() -> dict[str, list[dict]]:
    return load_qobuz_similar(
        DATA / "Artistes_Similaires_Qobuz" / "similar_artists.db"
    )


@st.cache_data
def get_qobuz_portraits() -> dict[str, str]:
    """Index nom_artiste → portrait Qobuz (bio), pour affichage UI."""
    return load_qobuz_portraits(
        DATA / "Artistes_Similaires_Qobuz" / "similar_artists.db"
    )


@st.cache_data
def get_tag_similarity_index() -> dict[str, dict[str, float]]:
    """Index de similarité tags via co-occurrence sur les artistes Last.fm."""
    from tag_similarity import build_tag_cooccurrence
    return build_tag_cooccurrence(get_lastfm_tags())


@st.cache_data
def get_artist_popularity() -> dict[str, int]:
    """Popularité (nb de fois où l'artiste est cité comme similaire) — cache.

    Combine les trois sources (Last.fm + Spotify + Qobuz) pour un signal
    plus représentatif des "artistes génériques" cités partout.
    """
    return compute_artist_popularity(
        get_lastfm_similar(),
        get_spotify_similar(),
        get_qobuz_similar(),
    )


@st.cache_data
def get_spotify_id_index() -> dict[str, str]:
    """Index nom_artiste → spotify_id, pour le player embed."""
    return load_spotify_id_index(
        DATA / "Artistes_Similaires_Spotify" / "similar_artists.db"
    )


@st.cache_data
def get_biblio() -> set[str]:
    """Artistes en bibliothèque physique (tels que scannés par A_Recuperer --scan-library)."""
    p = DATA / "Bibliotheque" / "bibliotheque.csv"
    if not p.exists():
        return set()
    df = pd.read_csv(p)
    if 'Artist' not in df.columns:
        return set()
    return set(df['Artist'].dropna().unique())


@st.cache_data
def get_playlists_artists() -> set[str]:
    """Artistes présents dans toutes les playlists Spotify.

    Les pistes "featuring" exposent plusieurs artistes séparés par virgule
    (ex : "Daft Punk, Pharrell Williams"). On split et on garde tous les noms,
    pour que l'exclusion soit complète.
    """
    folder = DATA / "Playlists_Spotify"
    artists = set()
    if not folder.exists():
        return artists
    for f in folder.glob("*.csv"):
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if 'Artist' not in df.columns:
            continue
        for value in df['Artist'].dropna():
            for part in str(value).split(','):
                part = part.strip()
                if part:
                    artists.add(part)
    return artists


@st.cache_data
def get_seeds_pool() -> list[str]:
    """Pool d'artistes pour l'autocomplétion : biblio ∪ playlists, trié."""
    return sorted(get_biblio() | get_playlists_artists())


@st.cache_data
def get_excluded() -> set[str]:
    """Artistes à exclure des recommandations : biblio + playlists.

    NB : les dislikes (👎) viennent en plus mais sont gérés sans cache pour
    refléter immédiatement les votes d'une session.
    """
    return get_biblio() | get_playlists_artists()


@st.cache_data
def get_all_genres() -> list[str]:
    """Tous les genres présents dans les tags Last.fm, dédupliqués et triés."""
    tags = get_lastfm_tags()
    all_tags: set[str] = set()
    for t_list in tags.values():
        all_tags.update(t_list)
    return sorted(all_tags)
