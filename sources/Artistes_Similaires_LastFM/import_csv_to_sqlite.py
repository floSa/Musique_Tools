"""One-shot script: imports output_related.csv into the new SQLite database."""
import ast
import json
import pandas as pd
from pathlib import Path
from database import Database

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_LastFM"


def main():
    csv_path = DATA_DIR / "output_related.csv"
    if not csv_path.exists():
        print(f"Fichier non trouvé: {csv_path}")
        return

    df = pd.read_csv(csv_path)
    if "Source_Artist" not in df.columns or "Related_Data_Raw" not in df.columns:
        print("Colonnes manquantes dans le CSV.")
        return

    db = Database()
    imported = 0
    skipped = 0

    for _, row in df.iterrows():
        artist = str(row["Source_Artist"])
        if db.artist_exists(artist):
            skipped += 1
            continue
        try:
            similar = ast.literal_eval(str(row["Related_Data_Raw"]))
        except (ValueError, SyntaxError):
            similar = []
        db.save_result(artist, {"similar_artists": similar, "tags": []})
        imported += 1

    db.close()
    print(f"Import terminé : {imported} artistes importés, {skipped} déjà présents.")


if __name__ == "__main__":
    main()
