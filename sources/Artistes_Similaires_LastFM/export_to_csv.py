import pandas as pd
import json
from pathlib import Path
from database import Database

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_LastFM"


def main():
    print("Export SQLite → CSV")

    db = Database()
    rows = db.get_all_results()
    db.close()

    data_list = []
    for source_artist, similar_json, tags_json in rows:
        try:
            similar = json.loads(similar_json) if similar_json else []
        except (json.JSONDecodeError, TypeError):
            similar = []
        data_list.append({
            "Source_Artist": source_artist,
            "Related_Data_Raw": str(similar)
        })

    df = pd.DataFrame(data_list)
    output_path = DATA_DIR / "output_related.csv"
    df.to_csv(output_path, index=False)
    print(f"Export terminé : {len(data_list)} enregistrements → {output_path}")


if __name__ == "__main__":
    main()
