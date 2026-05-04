import logging
import time
from pathlib import Path
from database import Database
from api_client import LastFmAPIClient

logging.getLogger().setLevel(logging.ERROR)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_LastFM"


def main():
    print("Etape 2 : Recherche d'artistes similaires manquants")

    missing_file = DATA_DIR / "artists_with_no_results.txt"
    if not missing_file.exists():
        print("Aucun fichier d'artistes manquants trouvé.")
        return

    with open(missing_file, 'r') as f:
        artists_to_rescue = [line.strip() for line in f if line.strip()]

    if not artists_to_rescue:
        print("Aucun artiste à traiter.")
        return

    db = Database()
    try:
        api_client = LastFmAPIClient()
    except Exception:
        db.close()
        return

    for artist in artists_to_rescue:
        candidates = api_client.search_artist(artist)

        if candidates:
            found = False
            for candidate in candidates:
                try:
                    data = api_client.get_artist_info(candidate, similar_limit=20, tags_limit=5)
                    if len(data.get('similar_artists', [])) > 0:
                        print(f"{artist} : {len(data['similar_artists'])} artistes similaires trouvés.")
                        db.save_result(artist, data)
                        found = True
                        break
                except Exception:
                    pass
            if not found:
                print(f"{artist} : Trouvé mais pas d'artiste similaire.")
        else:
            print(f"{artist} : Non trouvé sur Last.fm")

        time.sleep(0.5)

    db.close()


if __name__ == "__main__":
    main()
