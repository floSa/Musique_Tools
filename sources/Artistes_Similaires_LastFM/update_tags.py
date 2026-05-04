"""
Met à jour les tags manquants dans la DB sans retoucher aux artistes similaires.

Usage:
    uv run python update_tags.py
"""
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from api_client import LastFmAPIClient
from database import Database

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def get_artists_without_tags(db: Database) -> list[str]:
    cursor = db.conn.execute(
        "SELECT source_artist FROM artists WHERE status='success' AND (tags IS NULL OR tags='[]' OR tags='')"
    )
    return [row[0] for row in cursor.fetchall()]


def update_tags(db: Database, artist_name: str, tags: list):
    db.conn.execute(
        "UPDATE artists SET tags=? WHERE source_artist=?",
        (json.dumps(tags), artist_name)
    )
    db.conn.commit()


def main():
    if not os.getenv("LASTFM_API_KEY"):
        logging.error("Missing LASTFM_API_KEY env var.")
        return

    db = Database()
    artists = get_artists_without_tags(db)
    logging.info(f"{len(artists)} artistes sans tags à traiter.")

    try:
        client = LastFmAPIClient()
    except Exception as e:
        logging.error(f"API client init failed: {e}")
        db.close()
        return

    consecutive_errors = 0
    updated = 0
    legitimately_empty = 0

    try:
        for i, artist in enumerate(artists, 1):
            try:
                tags_data = client._make_request({
                    'method': 'artist.gettoptags',
                    'artist': artist,
                    'autocorrect': 1,
                })
                tags = []
                if 'toptags' in tags_data and 'tag' in tags_data['toptags']:
                    all_tags = tags_data['toptags']['tag']
                    if isinstance(all_tags, dict):
                        all_tags = [all_tags]
                    tags = [t['name'] for t in all_tags[:5] if 'name' in t]

                update_tags(db, artist, tags)

                if tags:
                    updated += 1
                    logging.info(f"[{i}/{len(artists)}] {artist}: {tags}")
                else:
                    legitimately_empty += 1
                    logging.info(f"[{i}/{len(artists)}] {artist}: aucun tag (normal)")

                consecutive_errors = 0
                time.sleep(0.5)

            except Exception as e:
                logging.error(f"Erreur pour {artist}: {e}")
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    logging.warning("3 erreurs consécutives, pause 5 min...")
                    time.sleep(300)
                    consecutive_errors = 0

    except KeyboardInterrupt:
        logging.info("Interrompu.")
    finally:
        db.close()
        logging.info(f"Terminé : {updated} mis à jour, {legitimately_empty} sans tags sur Last.fm.")


if __name__ == "__main__":
    main()
