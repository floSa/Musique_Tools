"""
Export DB SQLite → `output_related.csv`.

La DB SQLite est la source de vérité ; ce CSV est un dérivé pour lecture
humaine et compatibilité avec d'éventuels scripts externes.

Usage :
    cd sources/Artistes_Similaires_Qobuz
    uv run python export_to_csv.py
"""
import json
from pathlib import Path

import pandas as pd

from database import Database

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_Qobuz"
OUTPUT_CSV = DATA_DIR / "output_related.csv"


def main() -> None:
    db = Database()
    rows = db.get_all_results()
    db.close()

    print(f"Export : {len(rows)} artistes")

    records = []
    for source_artist, source_id, similar_json, portrait in rows:
        try:
            similar = json.loads(similar_json) if similar_json else []
        except (json.JSONDecodeError, TypeError):
            similar = []
        formatted = [{"name": s.get("name"), "id": s.get("id", "")} for s in similar]
        records.append({
            "Source_Artist":    source_artist,
            "Source_Artist_ID": source_id or "",
            "Related_Data_Raw": str(formatted),
            "Portrait":         portrait or "",
        })

    df = pd.DataFrame(records)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"✅ Écrit dans {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
