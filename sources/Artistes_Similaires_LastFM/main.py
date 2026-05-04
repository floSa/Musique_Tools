import pandas as pd
import time
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from api_client import LastFmAPIClient
from database import Database

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_LastFM"


def load_artists(csv_path: Path) -> list:
    df = pd.read_csv(csv_path)
    if "Artist" not in df.columns:
        raise ValueError("CSV must have an 'Artist' column")
    return df["Artist"].tolist()


def main():
    csv_path = Path(__file__).parent.parent.parent / "data" / "Ressources" / "artistes_liste.csv"
    if not csv_path.exists():
        logging.error(f"File {csv_path} not found.")
        return

    if not os.getenv("LASTFM_API_KEY"):
        logging.error("Missing LASTFM_API_KEY env var.")
        return

    artists = load_artists(csv_path)
    db = Database()

    try:
        api_client = LastFmAPIClient()
    except Exception as e:
        logging.error(f"Failed to initialize API client: {e}")
        return

    consecutive_errors = 0
    max_consecutive_errors = 3

    logging.info(f"Starting Last.fm API fetch for {len(artists)} artists...")

    try:
        for artist in artists:
            if db.artist_exists(artist):
                logging.info(f"Skipping {artist}, already processed.")
                continue

            try:
                logging.info(f"Processing {artist}...")
                data = api_client.get_artist_info(artist, similar_limit=20, tags_limit=5)

                similar_count = len(data.get('similar_artists', []))
                tags_count = len(data.get('tags', []))
                logging.info(f"Found {similar_count} similar artists and {tags_count} tags for {artist}")

                db.save_result(artist, data)
                consecutive_errors = 0
                time.sleep(0.5)

            except Exception as e:
                logging.error(f"Error processing {artist}: {e}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logging.error(f"Reached {max_consecutive_errors} consecutive errors. Pausing 5 minutes...")
                    time.sleep(300)
                    consecutive_errors = 0

    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    finally:
        db.close()
        logging.info("Service stopped.")


if __name__ == "__main__":
    main()
