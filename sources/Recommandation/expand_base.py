"""
Étend la base de similarités en ajoutant les artistes les plus cités mais pas
encore scrapés comme source.

Stratégie de crawl en largeur (breadth-first) :
    1. Compter la popularité de chaque artiste (nb de fois cité comme similaire
       dans Last.fm + Spotify).
    2. Identifier ceux qui ne sont pas encore scrapés comme `source_artist`
       (Last.fm) ni `Source_Artist` (Spotify).
    3. Filtrer ceux déjà dans `artistes_liste.csv` (en attente de scraping)
       et ceux à exclure (biblio + playlists + dislikes).
    4. Garder le top N par popularité.
    5. Les ajouter à `data/Ressources/artistes_liste.csv`.

Au prochain run des scrapers Artistes_Similaires_LastFM et
Artistes_Similaires_Spotify, ces artistes seront traités et enrichiront la
base — élargissant ainsi le graphe de similarité vers ses artistes "centraux".

Usage :
    uv run python expand_base.py                   # 200 artistes, seuil 10
    uv run python expand_base.py --dry-run         # Preview seulement
    uv run python expand_base.py --top-n 500       # Plus d'artistes
    uv run python expand_base.py --min-citations 5 # Seuil plus bas (plus de bruit)
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from engine import (
    compute_artist_popularity,
    load_lastfm_similar,
    load_spotify_similar,
    normalize_artist,
)
import feedback


DATA = Path(__file__).parent.parent.parent / "data"
ARTISTES_LISTE_CSV = DATA / "Ressources" / "artistes_liste.csv"
LASTFM_DB = DATA / "Artistes_Similaires_LastFM" / "similar_artists.db"
SPOTIFY_CSV = DATA / "Artistes_Similaires_Spotify" / "output_related.csv"
BIBLIO_CSV = DATA / "Bibliotheque" / "bibliotheque.csv"
PLAYLISTS_DIR = DATA / "Playlists_Spotify"

DEFAULT_TOP_N = 200
DEFAULT_MIN_CITATIONS = 10


def _load_lastfm_sources() -> set[str]:
    if not LASTFM_DB.exists():
        return set()
    conn = sqlite3.connect(str(LASTFM_DB))
    cur = conn.execute("SELECT source_artist FROM artists WHERE status='success'")
    sources = {row[0] for row in cur.fetchall()}
    conn.close()
    return sources


def _load_spotify_sources() -> set[str]:
    if not SPOTIFY_CSV.exists():
        return set()
    df = pd.read_csv(SPOTIFY_CSV)
    if "Source_Artist" not in df.columns:
        return set()
    return set(df["Source_Artist"].dropna().astype(str))


def _load_existing_seeds() -> set[str]:
    if not ARTISTES_LISTE_CSV.exists():
        return set()
    df = pd.read_csv(ARTISTES_LISTE_CSV)
    if "Artist" not in df.columns:
        return set()
    return set(df["Artist"].dropna().str.strip())


def _load_biblio() -> set[str]:
    if not BIBLIO_CSV.exists():
        return set()
    df = pd.read_csv(BIBLIO_CSV)
    if "Artist" not in df.columns:
        return set()
    return set(df["Artist"].dropna().str.strip())


def _load_playlists() -> set[str]:
    artists = set()
    if not PLAYLISTS_DIR.exists():
        return artists
    for f in PLAYLISTS_DIR.glob("*.csv"):
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        if "Artist" not in df.columns:
            continue
        for value in df["Artist"].dropna():
            for part in str(value).split(","):
                part = part.strip()
                if part:
                    artists.add(part)
    return artists


def expand(top_n: int, min_citations: int, dry_run: bool) -> int:
    print(f"Chargement des bases de similarité...")
    lastfm_sim = load_lastfm_similar(LASTFM_DB)
    spotify_sim = load_spotify_similar(SPOTIFY_CSV)
    pop = compute_artist_popularity(lastfm_sim, spotify_sim)
    print(f"  {len(pop)} artistes uniques cités comme similaires")

    sources = _load_lastfm_sources() | _load_spotify_sources()
    existing = _load_existing_seeds()
    excluded = _load_biblio() | _load_playlists() | feedback.get_disliked()

    # Comparaison normalisée (insensible à la casse + espaces)
    sources_norm = {normalize_artist(s) for s in sources}
    existing_norm = {normalize_artist(s) for s in existing}
    excluded_norm = {normalize_artist(s) for s in excluded}

    print(f"  {len(sources)} déjà scrapés comme source")
    print(f"  {len(existing)} en attente dans artistes_liste.csv")
    print(f"  {len(excluded)} à exclure (biblio + playlists + dislikes)")

    # Candidats : pas source, pas en attente, pas exclu, citations ≥ seuil
    candidates = []
    for artist, citations in pop.items():
        if citations < min_citations:
            continue
        norm = normalize_artist(artist)
        if norm in sources_norm or norm in existing_norm or norm in excluded_norm:
            continue
        candidates.append((artist, citations))

    candidates.sort(key=lambda x: x[1], reverse=True)
    top = candidates[:top_n]

    print(f"\n{len(candidates)} candidats éligibles (citations ≥ {min_citations})")
    print(f"Top {len(top)} sélectionnés :\n")
    for i, (artist, citations) in enumerate(top[:30], 1):
        print(f"  {i:>3}. {artist:<40} ({citations} citations)")
    if len(top) > 30:
        print(f"  ... et {len(top) - 30} autres")

    if not top:
        print("\nRien à ajouter.")
        return 0

    if dry_run:
        print("\n[dry-run] Aucune modification.")
        return len(top)

    new_seeds = sorted(existing | {a for a, _ in top}, key=str.lower)
    df_out = pd.DataFrame({"Artist": new_seeds})
    ARTISTES_LISTE_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(ARTISTES_LISTE_CSV, index=False)
    print(f"\n✓ {len(top)} artistes ajoutés → {ARTISTES_LISTE_CSV}")
    print("→ Relance les scrapers Last.fm/Spotify pour les traiter.")
    return len(top)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N,
                        help=f"Nombre d'artistes à ajouter (défaut : {DEFAULT_TOP_N}).")
    parser.add_argument("--min-citations", type=int, default=DEFAULT_MIN_CITATIONS,
                        help=f"Seuil minimum de citations pour être éligible (défaut : {DEFAULT_MIN_CITATIONS}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche ce qui serait ajouté sans modifier le CSV.")
    args = parser.parse_args()
    expand(args.top_n, args.min_citations, args.dry_run)


if __name__ == "__main__":
    main()
