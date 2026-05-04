"""
A_Recuperer - Pipeline complet de recherche d'albums.

Usage:
    python main.py --extract-artists  # Extrait les artistes uniques des playlists → artistes_liste.csv
    python main.py --scan-library     # Scan M:\\musiques → data/Bibliotheque/bibliotheque.csv
    python main.py --match            # Matching playlists vs biblio → albums_a_rechercher.csv
    python main.py --search           # Scraper Lyon + Qobuz sur albums_a_rechercher.csv
    python main.py --consolidate      # Fusionne matching + scraping → resultats_final.csv
    python main.py --all              # Enchaîne toutes les étapes
"""
import argparse
import os
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
DATA_DIR = ROOT / "data"
PLAYLISTS_DIR = DATA_DIR / "Playlists_Spotify"
RESSOURCES_DIR = DATA_DIR / "Ressources"
BIBLIOTHEQUE_CSV = DATA_DIR / "Bibliotheque" / "bibliotheque.csv"
RECHERCHES_XLSX = RESSOURCES_DIR / "recherches_effectuees.xlsx"
ALBUMS_A_RECHERCHER_CSV  = RESSOURCES_DIR / "albums_a_rechercher.csv"
ALBUMS_MATCH_COMPLET_CSV = RESSOURCES_DIR / "albums_match_complet.csv"
RESULTATS_CSV            = RESSOURCES_DIR / "resultats_cotes.csv"
RESULTATS_FINAL_CSV      = DATA_DIR / "Resultats" / "resultats_final.csv"
ARTISTES_LISTE_CSV       = RESSOURCES_DIR / "artistes_liste.csv"

LIBRARY_PATH = os.getenv("LIBRARY_PATH", "/mnt/m/musiques/__Autres")

# Similarity thresholds for matching
ARTIST_THRESHOLD = 90
ALBUM_THRESHOLD = 80


def cmd_extract_artists():
    from utils.data_loader import load_playlists
    df = load_playlists(PLAYLISTS_DIR)
    if df.empty:
        print("Aucune playlist chargée.")
        return
    artists = df['Artist'].dropna().unique()
    artists = sorted(set(a.split(',')[0].strip() for a in artists))
    df_out = pd.DataFrame({'Artist': artists})
    ARTISTES_LISTE_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(ARTISTES_LISTE_CSV, index=False)
    print(f"{len(df_out)} artistes uniques → {ARTISTES_LISTE_CSV}")


def cmd_scan_library():
    from utils.library import scan_library
    print(f"Scan de la bibliothèque : {LIBRARY_PATH}")
    df = scan_library(LIBRARY_PATH, output_path=BIBLIOTHEQUE_CSV)
    print(f"{len(df)} albums trouvés.")


def cmd_match():
    from utils.data_loader import load_playlists, load_recherches_effectuees
    from utils.matching import clean_albums, clean_artist, match_albums_with_fuzz

    print("Chargement des playlists...")
    df_playlists = load_playlists(PLAYLISTS_DIR)
    if df_playlists.empty:
        print("Erreur: aucune playlist chargée.")
        return

    print("Chargement de la bibliothèque locale...")
    if BIBLIOTHEQUE_CSV.exists():
        df_biblio = pd.read_csv(BIBLIOTHEQUE_CSV)
    else:
        print(f"Bibliothèque non trouvée ({BIBLIOTHEQUE_CSV}). Lance d'abord --scan-library.")
        return

    print("Chargement des recherches déjà effectuées...")
    if RECHERCHES_XLSX.exists():
        df_recherches = load_recherches_effectuees(RECHERCHES_XLSX)
    else:
        df_recherches = pd.DataFrame(columns=['Artist', 'Album'])
        print("Pas de fichier recherches_effectuees.xlsx, on part de zéro.")

    # Build unique album list from playlists
    df_playlists_clean = df_playlists[['Song', 'Artist', 'Album']].drop_duplicates().copy()
    df_playlists_clean['Artist_clean'] = df_playlists_clean['Artist'].apply(clean_artist)
    df_playlists_clean['Album_clean'] = df_playlists_clean['Album'].apply(clean_albums)

    df_biblio_clean = df_biblio.copy()
    df_biblio_clean['Artist_clean'] = df_biblio_clean['Artist'].apply(clean_artist)
    df_biblio_clean['Album_clean'] = df_biblio_clean['Album'].apply(clean_albums)

    # Albums to match: unique (Artist, Album) from playlists
    df_to_match = df_playlists_clean[['Artist', 'Album', 'Artist_clean', 'Album_clean']].drop_duplicates()

    print(f"Matching {len(df_to_match)} albums vs {len(df_biblio_clean)} albums en bibliothèque...")
    df_match = match_albums_with_fuzz(
        df_to_match.rename(columns={'Artist': 'Artist', 'Album': 'Album'}),
        df_biblio_clean.rename(columns={'Artist': 'Artist', 'Album': 'Album'}),
        name_tester="Playlist",
        name_ressource="Biblio",
        artist_similarity_threshold=ARTIST_THRESHOLD,
    )

    # Keep albums NOT in library (album_sim < threshold) and NOT already researched
    not_in_biblio = df_match[df_match['Album_sim'] < ALBUM_THRESHOLD].copy()
    not_in_biblio = not_in_biblio.rename(columns={
        'Artist_Playlist': 'Artist',
        'Album_Playlist': 'Album',
    })[['Artist', 'Album']]

    # Remove already researched
    if not df_recherches.empty:
        already_done = set(zip(df_recherches['Artist'], df_recherches['Album']))
        not_in_biblio = not_in_biblio[
            ~not_in_biblio.apply(lambda r: (r['Artist'], r['Album']) in already_done, axis=1)
        ]

    not_in_biblio = not_in_biblio.drop_duplicates().sort_values(['Artist', 'Album'])

    ALBUMS_A_RECHERCHER_CSV.parent.mkdir(parents=True, exist_ok=True)
    not_in_biblio.to_csv(ALBUMS_A_RECHERCHER_CSV, index=False)
    print(f"{len(not_in_biblio)} albums à rechercher → {ALBUMS_A_RECHERCHER_CSV}")

    # Save full match data (with sim scores) for later consolidation
    match_complet = df_match[df_match['Album_sim'] < ALBUM_THRESHOLD].copy()
    match_complet = match_complet.rename(columns={
        'Artist_Playlist': 'Artist_A_rechercher',
        'Artist_Biblio':   'Artist_Possede',
        'Album_Playlist':  'Album_A_rechercher',
        'Album_Biblio':    'Album_Possede',
    })
    match_complet = match_complet.drop_duplicates(subset=['Artist_A_rechercher', 'Album_A_rechercher'])
    match_complet.to_csv(ALBUMS_MATCH_COMPLET_CSV, index=False)
    print(f"Données de matching complètes → {ALBUMS_MATCH_COMPLET_CSV}")


def cmd_consolidate():
    if not ALBUMS_MATCH_COMPLET_CSV.exists():
        print(f"Fichier non trouvé : {ALBUMS_MATCH_COMPLET_CSV}. Lance d'abord --match.")
        return
    if not RESULTATS_CSV.exists():
        print(f"Fichier non trouvé : {RESULTATS_CSV}. Lance d'abord --search.")
        return

    df_match = pd.read_csv(ALBUMS_MATCH_COMPLET_CSV)
    df_resultats = pd.read_csv(RESULTATS_CSV)

    df = df_match.merge(
        df_resultats[['Artist', 'Album', 'Cote', 'Disponibilité', 'Qobuz_URL']],
        left_on=['Artist_A_rechercher', 'Album_A_rechercher'],
        right_on=['Artist', 'Album'],
        how='left',
    ).drop(columns=['Artist', 'Album'])

    def build_sources(row):
        parts = []
        qobuz = str(row['Qobuz_URL']).strip() if pd.notna(row.get('Qobuz_URL')) else ''
        cote = str(row['Cote']).strip() if pd.notna(row.get('Cote')) else ''
        dispo = str(row['Disponibilité']).strip() if pd.notna(row.get('Disponibilité')) else ''
        if qobuz and qobuz != 'nan':
            parts.append(qobuz)
        if cote and cote != 'nan':
            parts.append(f"{cote} ({dispo})" if dispo and dispo != 'nan' else cote)
        return ' | '.join(parts)

    df['Sources'] = df.apply(build_sources, axis=1)
    df['Reference'] = df['Cote'].where(df['Cote'].notna() & (df['Cote'].astype(str) != 'nan'), '')

    final = df[[
        'Sources', 'Reference',
        'Artist_A_rechercher', 'Artist_Possede', 'Artist_sim',
        'Album_A_rechercher',  'Album_Possede',  'Album_sim',
        'Liste_albums_pos',
    ]]

    RESULTATS_FINAL_CSV.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(RESULTATS_FINAL_CSV, index=False)
    print(f"{len(final)} lignes → {RESULTATS_FINAL_CSV}")


def cmd_search():
    from utils.scraper import run_scraper
    if not ALBUMS_A_RECHERCHER_CSV.exists():
        print(f"Fichier non trouvé : {ALBUMS_A_RECHERCHER_CSV}. Lance d'abord --match.")
        return
    print(f"Lancement du scraper sur {ALBUMS_A_RECHERCHER_CSV}...")
    run_scraper(ALBUMS_A_RECHERCHER_CSV, RESULTATS_CSV)


def main():
    parser = argparse.ArgumentParser(description="A_Recuperer - Pipeline de recherche d'albums")
    parser.add_argument('--extract-artists', action='store_true', help='Extrait les artistes uniques des playlists → data/Ressources/artistes_liste.csv')
    parser.add_argument('--scan-library',    action='store_true', help='Scan la bibliothèque locale')
    parser.add_argument('--match',           action='store_true', help='Matching playlists vs bibliothèque')
    parser.add_argument('--search',          action='store_true', help='Scraper Lyon + Qobuz')
    parser.add_argument('--consolidate',     action='store_true', help='Fusionne matching + scraping → resultats_final.csv')
    parser.add_argument('--all',             action='store_true', help='Pipeline complet')
    args = parser.parse_args()

    if args.all or args.extract_artists:
        cmd_extract_artists()
    if args.all or args.scan_library:
        cmd_scan_library()
    if args.all or args.match:
        cmd_match()
    if args.all or args.search:
        cmd_search()
    if args.all or args.consolidate:
        cmd_consolidate()
    if not any([args.extract_artists, args.scan_library, args.match, args.search, args.consolidate, args.all]):
        parser.print_help()


if __name__ == "__main__":
    main()
