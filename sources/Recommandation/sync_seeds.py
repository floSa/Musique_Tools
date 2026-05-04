"""
Synchronise data/Ressources/artistes_liste.csv avec biblio + playlists.

Détecte les artistes présents dans la bibliothèque physique ou les playlists
mais absents de `artistes_liste.csv`, et les ajoute. Les scrapers
Artistes_Similaires_LastFM et Artistes_Similaires_Spotify les traiteront au
prochain run (ils reprennent où ils en sont).

Usage:
    uv run python sync_seeds.py --dry-run   # Affiche les ajouts sans écrire
    uv run python sync_seeds.py             # Applique l'ajout
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent.parent / "data"
BIBLIO_CSV = DATA_DIR / "Bibliotheque" / "bibliotheque.csv"
PLAYLISTS_DIR = DATA_DIR / "Playlists_Spotify"
ARTISTES_LISTE_CSV = DATA_DIR / "Ressources" / "artistes_liste.csv"


def load_biblio_artists() -> set[str]:
    if not BIBLIO_CSV.exists():
        print(f"⚠️  Bibliothèque non trouvée : {BIBLIO_CSV}")
        return set()
    df = pd.read_csv(BIBLIO_CSV)
    if "Artist" not in df.columns:
        return set()
    return set(df["Artist"].dropna().str.strip().unique())


def load_playlists_artists() -> set[str]:
    if not PLAYLISTS_DIR.exists():
        return set()
    artists = set()
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


def load_existing_seeds() -> set[str]:
    if not ARTISTES_LISTE_CSV.exists():
        return set()
    df = pd.read_csv(ARTISTES_LISTE_CSV)
    if "Artist" not in df.columns:
        return set()
    return set(df["Artist"].dropna().str.strip())


def sync(dry_run: bool = False) -> int:
    biblio = load_biblio_artists()
    playlists = load_playlists_artists()
    existing = load_existing_seeds()

    universe = biblio | playlists
    missing = sorted(universe - existing, key=str.lower)

    print(f"Bibliothèque : {len(biblio)} artistes")
    print(f"Playlists    : {len(playlists)} artistes")
    print(f"Union        : {len(universe)} artistes")
    print(f"Existants dans artistes_liste.csv : {len(existing)}")
    print(f"À ajouter    : {len(missing)}")

    if not missing:
        print("✓ Rien à synchroniser.")
        return 0

    print("\nExemples (50 premiers) :")
    for a in missing[:50]:
        print(f"  + {a}")
    if len(missing) > 50:
        print(f"  ... et {len(missing) - 50} autres")

    if dry_run:
        print("\n[dry-run] Aucune modification écrite.")
        return len(missing)

    merged = sorted(existing | set(missing), key=str.lower)
    df_out = pd.DataFrame({"Artist": merged})
    ARTISTES_LISTE_CSV.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(ARTISTES_LISTE_CSV, index=False)
    print(f"\n✓ {len(missing)} artistes ajoutés → {ARTISTES_LISTE_CSV}")
    print("→ Relance les scrapers Last.fm/Spotify pour traiter les nouveaux.")
    return len(missing)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche ce qui serait ajouté sans écrire le fichier.")
    args = parser.parse_args()
    sync(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
