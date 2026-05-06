"""
A_Recuperer - Pipeline complet de recherche d'albums.

Usage:
    python main.py --extract-artists  # Extrait les artistes uniques des playlists → artistes_liste.csv
    python main.py --scan-library     # Scan M:\\musiques → data/Bibliotheque/bibliotheque.csv
    python main.py --match            # Matching playlists vs biblio → albums_a_rechercher.csv
    python main.py --search           # Scraper Lyon + Qobuz sur albums_a_rechercher.csv
    python main.py --consolidate      # Fusionne matching + scraping → resultats_final.csv
    python main.py --all              # Enchaîne toutes les étapes

Variables d'environnement :
    PLAYLIST_FILTER  Si défini, restreint --match/--search/--consolidate à
                     une seule playlist (ex: PLAYLIST_FILTER=Partage). Les
                     fichiers générés sont suffixés `_<PlaylistName>` pour
                     ne pas écraser le run global. Vide/non défini = toutes.
    LIBRARY_PATH, LIBRARY_BO_PATH, LIBRARY_COMPILS_PATH, LIBRARY_JEUX_PATH
                     Chemins des 4 racines de la bibliothèque physique.
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

LIBRARY_PATH         = os.getenv("LIBRARY_PATH",         "/mnt/m/musiques/__Autres")
LIBRARY_BO_PATH      = os.getenv("LIBRARY_BO_PATH",      "/mnt/m/musiques/__B.O")
LIBRARY_COMPILS_PATH = os.getenv("LIBRARY_COMPILS_PATH", "/mnt/m/musiques/__COMPILS")
LIBRARY_JEUX_PATH    = os.getenv("LIBRARY_JEUX_PATH",    "/mnt/m/musiques/__JEUX")

# Filtre playlist : si défini, restreint --match/--search/--consolidate à une
# seule playlist. Les fichiers générés sont suffixés (cf. _suffixed). Vide ou
# non défini = comportement par défaut (toutes les playlists).
PLAYLIST_FILTER = os.getenv("PLAYLIST_FILTER") or None

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
    from utils.library import scan_all_libraries
    print("Scan des bibliothèques :")
    print(f"  __Autres  : {LIBRARY_PATH}")
    print(f"  __B.O     : {LIBRARY_BO_PATH}")
    print(f"  __COMPILS : {LIBRARY_COMPILS_PATH}")
    print(f"  __JEUX    : {LIBRARY_JEUX_PATH}")
    df = scan_all_libraries(
        autres=LIBRARY_PATH,
        bo=LIBRARY_BO_PATH,
        compils=LIBRARY_COMPILS_PATH,
        jeux=LIBRARY_JEUX_PATH,
        output_path=BIBLIOTHEQUE_CSV,
    )
    print(f"{len(df)} albums trouvés au total.")


def _suffixed(path: Path, playlist: str | None) -> Path:
    """Insère `_<playlist>` avant l'extension si une playlist est filtrée."""
    if not playlist:
        return path
    return path.with_name(f"{path.stem}_{playlist}{path.suffix}")


def cmd_match(playlist: str | None = None):
    from utils.data_loader import load_playlists, load_recherches_effectuees
    from utils.matching import clean_albums, clean_artist, match_albums_with_fuzz

    print("Chargement des playlists...")
    df_playlists = load_playlists(PLAYLISTS_DIR)
    if df_playlists.empty:
        print("Erreur: aucune playlist chargée.")
        return

    if playlist:
        available = sorted(df_playlists["Playlist"].unique())
        if playlist not in available:
            print(f"Playlist '{playlist}' introuvable. Disponibles : {', '.join(available)}")
            return
        df_playlists = df_playlists[df_playlists["Playlist"] == playlist].copy()
        print(f"Filtrage sur playlist '{playlist}' : {len(df_playlists)} titres")

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

    out_a_rechercher  = _suffixed(ALBUMS_A_RECHERCHER_CSV,  playlist)
    out_match_complet = _suffixed(ALBUMS_MATCH_COMPLET_CSV, playlist)

    out_a_rechercher.parent.mkdir(parents=True, exist_ok=True)
    not_in_biblio.to_csv(out_a_rechercher, index=False)
    print(f"{len(not_in_biblio)} albums à rechercher → {out_a_rechercher}")

    # Save full match data (with sim scores) for later consolidation
    match_complet = df_match[df_match['Album_sim'] < ALBUM_THRESHOLD].copy()
    match_complet = match_complet.rename(columns={
        'Artist_Playlist': 'Artist_A_rechercher',
        'Artist_Biblio':   'Artist_Possede',
        'Album_Playlist':  'Album_A_rechercher',
        'Album_Biblio':    'Album_Possede',
    })
    match_complet = match_complet.drop_duplicates(subset=['Artist_A_rechercher', 'Album_A_rechercher'])
    match_complet.to_csv(out_match_complet, index=False)
    print(f"Données de matching complètes → {out_match_complet}")


def cmd_consolidate(playlist: str | None = None):
    in_match     = _suffixed(ALBUMS_MATCH_COMPLET_CSV, playlist)
    in_resultats = _suffixed(RESULTATS_CSV,            playlist)
    out_final    = _suffixed(RESULTATS_FINAL_CSV,      playlist)

    if not in_match.exists():
        print(f"Fichier non trouvé : {in_match}. Lance d'abord --match.")
        return
    if not in_resultats.exists():
        print(f"Fichier non trouvé : {in_resultats}. Lance d'abord --search.")
        return

    df_match = pd.read_csv(in_match)
    df_resultats = pd.read_csv(in_resultats)

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

    out_final.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(out_final, index=False)
    print(f"{len(final)} lignes → {out_final}")


def cmd_search(playlist: str | None = None):
    from utils.scraper import run_scraper
    in_csv  = _suffixed(ALBUMS_A_RECHERCHER_CSV, playlist)
    out_csv = _suffixed(RESULTATS_CSV,           playlist)
    if not in_csv.exists():
        print(f"Fichier non trouvé : {in_csv}. Lance d'abord --match.")
        return
    print(f"Lancement du scraper sur {in_csv}...")
    run_scraper(in_csv, out_csv)


def main():
    parser = argparse.ArgumentParser(description="A_Recuperer - Pipeline de recherche d'albums")
    parser.add_argument('--extract-artists', action='store_true', help='Extrait les artistes uniques des playlists → data/Ressources/artistes_liste.csv')
    parser.add_argument('--scan-library',    action='store_true', help='Scan la bibliothèque locale')
    parser.add_argument('--match',           action='store_true', help='Matching playlists vs bibliothèque')
    parser.add_argument('--search',          action='store_true', help='Scraper Lyon + Qobuz')
    parser.add_argument('--consolidate',     action='store_true', help='Fusionne matching + scraping → resultats_final.csv')
    parser.add_argument('--all',             action='store_true', help='Pipeline complet')
    args = parser.parse_args()

    if PLAYLIST_FILTER:
        print(f"PLAYLIST_FILTER actif : run restreint à la playlist '{PLAYLIST_FILTER}'")

    if args.all or args.extract_artists:
        cmd_extract_artists()
    if args.all or args.scan_library:
        cmd_scan_library()
    if args.all or args.match:
        cmd_match(playlist=PLAYLIST_FILTER)
    if args.all or args.search:
        cmd_search(playlist=PLAYLIST_FILTER)
    if args.all or args.consolidate:
        cmd_consolidate(playlist=PLAYLIST_FILTER)
    if not any([args.extract_artists, args.scan_library, args.match, args.search, args.consolidate, args.all]):
        parser.print_help()


if __name__ == "__main__":
    main()
