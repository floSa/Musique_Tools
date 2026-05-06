"""
Migration one-shot : `output_related.csv` → `similar_artists.db` (SQLite).

Lit l'ancien CSV (format historique avec `Source_Artist`, `Source_Artist_ID`,
`Related_Data_Raw` parsé via `ast.literal_eval`) et insère dans la nouvelle DB
SQLite.

Idempotent : peut être relancé sans dupliquer (ON CONFLICT DO UPDATE).

Usage :
    cd sources/Artistes_Similaires_Spotify
    uv run python import_csv_to_sqlite.py
    uv run python import_csv_to_sqlite.py --dry-run
"""
import argparse
import ast
from pathlib import Path

import pandas as pd

from database import Database

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_Spotify"
CSV_PATH = DATA_DIR / "output_related.csv"


def parse_related(raw: str) -> list[dict]:
    """Parse `str(list[dict])` Python (gère les apostrophes type 'N'to')."""
    try:
        data = ast.literal_eval(str(raw))
    except (ValueError, SyntaxError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for i, r in enumerate(data, 1):
        if not isinstance(r, dict) or "name" not in r:
            continue
        out.append({
            "name": str(r["name"]),
            "id": str(r.get("id", "")),
            "rank": i,
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Migration CSV → SQLite Spotify.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Affiche les stats sans insérer.")
    args = parser.parse_args()

    if not CSV_PATH.exists():
        print(f"⚠️  CSV introuvable : {CSV_PATH}")
        print("Rien à migrer (la DB SQLite existe peut-être déjà — c'est OK).")
        return

    df = pd.read_csv(CSV_PATH)
    print(f"📄 CSV : {len(df)} lignes")

    expected_cols = {"Source_Artist", "Source_Artist_ID", "Related_Data_Raw"}
    missing = expected_cols - set(df.columns)
    if missing:
        print(f"❌ Colonnes manquantes : {missing}")
        return

    rows: list[tuple[str, str, list[dict]]] = []
    n_empty = 0
    for _, row in df.iterrows():
        name = row["Source_Artist"]
        sid = row["Source_Artist_ID"] if pd.notna(row["Source_Artist_ID"]) else ""
        related = parse_related(row["Related_Data_Raw"])
        if not related:
            n_empty += 1
        rows.append((str(name), str(sid), related))

    print(f"   {len(rows) - n_empty} avec similaires, {n_empty} sans")

    if args.dry_run:
        print("\n--- DRY RUN — exemples ---")
        for name, sid, related in rows[:3]:
            print(f"  {name} (id={sid[:8]}...) → {len(related)} similaires")
            if related:
                print(f"    top: {related[0]}")
        return

    db = Database()
    n_inserted = 0
    for name, sid, related in rows:
        db.save_result(name, sid, related)
        n_inserted += 1

    db.close()
    print(f"\n✅ {n_inserted} artistes insérés/mis à jour dans similar_artists.db")
    print(f"   DB : {DATA_DIR / 'similar_artists.db'}")


if __name__ == "__main__":
    main()
