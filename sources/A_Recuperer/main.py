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
PLAYLISTS_DIR  = DATA_DIR / "Playlists_Spotify"
RESSOURCES_DIR = DATA_DIR / "Ressources"   # tenu à la main (recherches_effectuees, artistes_liste)
PIPELINE_DIR   = DATA_DIR / "Pipeline"     # fichiers intermédiaires regénérables
RESULTATS_DIR  = DATA_DIR / "Resultats"    # fichier final pour consultation

BIBLIOTHEQUE_CSV = DATA_DIR / "Bibliotheque" / "bibliotheque.csv"
RECHERCHES_XLSX  = RESSOURCES_DIR / "recherches_effectuees.xlsx"
ARTISTES_LISTE_CSV = RESSOURCES_DIR / "artistes_liste.csv"  # partagé avec les autres services

ALBUMS_A_RECHERCHER_CSV  = PIPELINE_DIR / "albums_a_rechercher.csv"
ALBUMS_MATCH_COMPLET_CSV = PIPELINE_DIR / "albums_match_complet.csv"
RESULTATS_CSV            = PIPELINE_DIR / "resultats_cotes.csv"
RESULTATS_FINAL_CSV      = RESULTATS_DIR / "resultats_final.csv"

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


def _strip_owned_from_autres(autres: str, liste_pos: str,
                             threshold: int = ALBUM_THRESHOLD) -> str:
    """Retire de `Autres_albums_biblio` les albums déjà possédés en local.

    `autres`     : "Titre - Cote, Titre - Cote" (albums du même artiste à la BM Lyon)
    `liste_pos`  : "Album - Album - Album"       (albums de l'artiste possédés localement)

    On ne garde dans `autres` que les albums qu'on n'a PAS déjà : pour chaque
    segment, on compare son titre (partie avant le dernier " - ", la cote étant
    après) aux albums possédés, en fuzzy `token_sort_ratio` sur les titres
    normalisés (`clean_albums`), au même seuil que le matching biblio
    (`ALBUM_THRESHOLD`). Titre possédé (>= seuil) → segment retiré.
    """
    from utils.matching import clean_albums
    from rapidfuzz import fuzz

    autres = "" if pd.isna(autres) else str(autres).strip()
    if not autres:
        return ""
    owned = [clean_albums(t) for t in str(liste_pos).split(" - ")] if pd.notna(liste_pos) else []
    owned = [o for o in owned if o]
    if not owned:
        return autres

    kept = []
    for seg in autres.split(", "):
        seg = seg.strip()
        if not seg:
            continue
        # split (pas rsplit) : la cote peut elle-même contenir " - " (ex.
        # "782.ARC 61 - Prêté" pour un CD emprunté) ; le titre, lui, jamais.
        # rsplit coupait alors le titre en 2 ("My god is blue - D 59179" au
        # lieu de "My god is blue"), ce qui faisait échouer le fuzzy-match
        # et laissait des albums déjà possédés dans Autres_albums_biblio.
        title = seg.split(" - ", 1)[0] if " - " in seg else seg
        t_norm = clean_albums(title)
        if t_norm and any(fuzz.token_sort_ratio(t_norm, o) >= threshold for o in owned):
            continue  # album déjà possédé → on l'enlève de la liste
        kept.append(seg)
    return ", ".join(kept)


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
    """Scanne les 4 racines de la bibliothèque physique.

    Si M: n'est pas accessible (lecteur non monté, network share down…),
    on **n'écrase pas** `bibliotheque.csv` existante : on garde l'ancien
    snapshot et on prévient l'utilisateur. Évite de perdre la biblio
    quand on lance le pipeline depuis une machine sans accès au M:.
    """
    from utils.library import scan_all_libraries

    # On juge l'accès à M: par la racine principale __Autres : si elle
    # n'existe pas, le drive n'est pas monté.
    if not Path(LIBRARY_PATH).exists():
        if BIBLIOTHEQUE_CSV.exists():
            existing = pd.read_csv(BIBLIOTHEQUE_CSV)
            print(f"⚠ M: non accessible ({LIBRARY_PATH} introuvable).")
            print(f"  Conservation de la bibliothèque existante : "
                  f"{BIBLIOTHEQUE_CSV} ({len(existing)} albums)")
        else:
            print(f"⚠ M: non accessible ({LIBRARY_PATH}) ET pas de "
                  f"bibliotheque.csv existante. Rien à faire.")
        return

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
        df_to_match,
        df_biblio_clean,
        name_tester="Playlist",
        name_ressource="Biblio",
        artist_similarity_threshold=ARTIST_THRESHOLD,
        # Match sur les versions normalisées (sans accents, sans casse, sans
        # parenthèses) pour éviter "Témé Tan" ≠ "Teme Tan". Les colonnes
        # Artist/Album restent en brut pour l'affichage.
        artist_match_col='Artist_clean',
        album_match_col='Album_clean',
    )

    # Keep albums NOT in library (album_sim < threshold) and NOT already researched
    not_in_biblio = df_match[df_match['Album_sim'] < ALBUM_THRESHOLD].copy()
    not_in_biblio = not_in_biblio.rename(columns={
        'Artist_Playlist': 'Artist',
        'Album_Playlist': 'Album',
    })[['Artist', 'Album']]

    # Remove already researched.
    # Le fichier recherches_effectuees.xlsx est tenu à la main avec des noms
    # déjà normalisés (minuscules, sans accents, sans parenthèses). Les noms
    # côté playlist sont bruts. On compare les deux côtés via clean_* pour
    # éviter les faux négatifs (ex: "Lost in Paradise" vs "lost in paradise").
    if not df_recherches.empty:
        already_done = set(zip(
            df_recherches['Artist'].astype(str).map(clean_artist),
            df_recherches['Album'].astype(str).map(clean_albums),
        ))
        not_in_biblio = not_in_biblio[
            ~not_in_biblio.apply(
                lambda r: (clean_artist(str(r['Artist'])), clean_albums(str(r['Album']))) in already_done,
                axis=1,
            )
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

    # Cohérence : exclure aussi les albums déjà recherchés (mêmes critères
    # que not_in_biblio) pour que match_complet ↔ albums_a_rechercher restent
    # alignés (sinon le merge dans cmd_consolidate produit des lignes orphelines).
    if not df_recherches.empty:
        already_done = set(zip(
            df_recherches['Artist'].astype(str).map(clean_artist),
            df_recherches['Album'].astype(str).map(clean_albums),
        ))
        match_complet = match_complet[
            ~match_complet.apply(
                lambda r: (clean_artist(str(r['Artist_A_rechercher'])), clean_albums(str(r['Album_A_rechercher']))) in already_done,
                axis=1,
            )
        ]

    # Propager le chemin physique de l'album possédé (colonne Path) si la
    # biblio scannée le fournit. Permet, dans le fichier final, d'ouvrir
    # directement le dossier de l'album possédé pour comparaison.
    if 'Path' in df_biblio.columns:
        match_complet = match_complet.merge(
            df_biblio.rename(columns={
                'Artist': 'Artist_Possede',
                'Album':  'Album_Possede',
                'Path':   'Path_Possede',
            })[['Artist_Possede', 'Album_Possede', 'Path_Possede']],
            on=['Artist_Possede', 'Album_Possede'],
            how='left',
        )
    else:
        match_complet['Path_Possede'] = ''

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

    # On inclut Autres_albums_biblio si la colonne est présente dans
    # resultats_cotes (générée par --search version >= refactor BM Lyon).
    cols_from_resultats = ['Artist', 'Album', 'Cote', 'Disponibilité', 'Qobuz_URL']
    if 'Autres_albums_biblio' in df_resultats.columns:
        cols_from_resultats.append('Autres_albums_biblio')
    df = df_match.merge(
        df_resultats[cols_from_resultats],
        left_on=['Artist_A_rechercher', 'Album_A_rechercher'],
        right_on=['Artist', 'Album'],
        how='left',
    ).drop(columns=['Artist', 'Album'])

    # Retirer de Autres_albums_biblio les albums qu'on possède déjà en local
    # (présents dans Liste_albums_pos) : on ne veut y voir que des albums
    # empruntables qu'on n'a PAS encore.
    if 'Autres_albums_biblio' in df.columns and 'Liste_albums_pos' in df.columns:
        df['Autres_albums_biblio'] = df.apply(
            lambda r: _strip_owned_from_autres(
                r.get('Autres_albums_biblio', ''), r.get('Liste_albums_pos', '')
            ),
            axis=1,
        )

    def build_sources_qobuz(row):
        qobuz = str(row['Qobuz_URL']).strip() if pd.notna(row.get('Qobuz_URL')) else ''
        return '' if not qobuz or qobuz == 'nan' else qobuz

    def build_sources_bibli(row):
        cote = str(row['Cote']).strip() if pd.notna(row.get('Cote')) else ''
        dispo = str(row['Disponibilité']).strip() if pd.notna(row.get('Disponibilité')) else ''
        if not cote or cote == 'nan':
            return ''
        if dispo and dispo != 'nan':
            return f"{cote} ({dispo})"
        return cote

    df['Sources Qobuz'] = df.apply(build_sources_qobuz, axis=1)
    df['Sources Bibli'] = df.apply(build_sources_bibli, axis=1)

    # Path_Possede : retirer le préfixe filesystem "/mnt/m/musiques/" pour
    # n'avoir qu'un chemin relatif lisible (ex: "__Autres/Daft Punk/Discovery")
    if 'Path_Possede' in df.columns:
        df['Path_Possede'] = df['Path_Possede'].astype(str).str.replace(
            r'^/mnt/m/musiques/', '', regex=True
        ).replace({'nan': ''})

    # Ordre final demandé
    cols = [
        'Sources Qobuz', 'Sources Bibli',
        'Artist_A_rechercher', 'Artist_Possede', 'Artist_sim',
        'Album_A_rechercher',  'Album_Possede',  'Album_sim',
        'Liste_albums_pos',
    ]
    if 'Path_Possede' in df.columns:
        cols.append('Path_Possede')
    if 'Autres_albums_biblio' in df.columns:
        cols.append('Autres_albums_biblio')

    final = df[cols]

    out_final.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(out_final, index=False)
    # Excel à côté pour consultation directe
    xlsx_path = out_final.with_suffix('.xlsx')
    try:
        final.to_excel(xlsx_path, index=False)
        print(f"{len(final)} lignes → {out_final} + {xlsx_path.name}")
    except Exception as e:
        print(f"{len(final)} lignes → {out_final}")
        print(f"  (xlsx non généré : {e})")


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
