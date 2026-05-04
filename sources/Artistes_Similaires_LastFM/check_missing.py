import pandas as pd
import json
import sys
from pathlib import Path
from database import Database

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_LastFM"


def main():
    print("Etape 1 : Vérification des artistes")

    csv_path = Path(__file__).parent.parent.parent / "data" / "Ressources" / "artistes_liste.csv"
    if not csv_path.exists():
        print(f"Erreur: Fichier {csv_path} introuvable.")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    if "Artist" not in df.columns:
        print("Erreur: Colonne 'Artist' manquante.")
        sys.exit(1)

    all_artists = set(df["Artist"].dropna().astype(str).tolist())

    db = Database()
    rows = db.get_all_results()
    db.close()

    found_artists = set()
    for source_artist, similar_json, tags_json in rows:
        try:
            similar = json.loads(similar_json) if similar_json else []
        except (json.JSONDecodeError, TypeError):
            similar = []
        has_results = (isinstance(similar, list) and len(similar) > 0)
        if has_results:
            found_artists.add(source_artist)

    missing_or_empty = all_artists - found_artists
    print(f"- {len(missing_or_empty)} artistes non trouvés en base")

    output_path = DATA_DIR / "artists_with_no_results.txt"
    with open(output_path, "w") as f:
        for artist in sorted(missing_or_empty):
            f.write(f"{artist}\n")

    print(f"Liste écrite dans {output_path}")


if __name__ == "__main__":
    main()
