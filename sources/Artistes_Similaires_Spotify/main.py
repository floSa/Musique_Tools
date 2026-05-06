"""
Scraper Spotify "Fans Also Like" — artistes similaires via Spotify web.

Usage:
    uv run python main.py

Stockage : SQLite (`data/Artistes_Similaires_Spotify/similar_artists.db`),
schéma aligné sur le service Last.fm (cf. `database.py`).

Reprend automatiquement là où il s'est arrêté (lit la DB au démarrage).
"""
import gc
import os
import random
import re
import sys
import time
import difflib
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from database import Database

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR    = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_Spotify"
INPUT_FILE  = Path(__file__).parent.parent.parent / "data" / "Ressources" / "artistes_liste.csv"
DEBUG_FILE  = DATA_DIR / "debug_selection.csv"

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTIST_ID_REGEX     = r"^[a-zA-Z0-9]{22}$"
NAME_MATCH_THRESHOLD = 0.8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check_internet() -> bool:
    import socket
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False


def wait_for_internet():
    print(f"[{time.strftime('%H:%M:%S')}] Connection lost. Waiting for internet...")
    while True:
        if check_internet():
            print(f"[{time.strftime('%H:%M:%S')}] Internet OK. Resuming...")
            return
        print(f"[{time.strftime('%H:%M:%S')}] No Internet. Sleeping 30s...")
        time.sleep(30)


def get_user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


def validate_artist_name(scraped: str, target: str) -> tuple[bool, float]:
    if not scraped or not target:
        return False, 0.0
    ratio = difflib.SequenceMatcher(None, scraped.lower(), target.lower()).ratio()
    return ratio >= NAME_MATCH_THRESHOLD, ratio


def validate_artist_id(artist_id: str) -> bool:
    return bool(re.match(ARTIST_ID_REGEX, artist_id or ""))


def apply_stealth(page):
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except Exception:
        pass


def extract_number(text: str):
    if not text:
        return None
    clean = re.sub(r"[^\d]", "", text)
    try:
        return int(clean)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core scraping
# ---------------------------------------------------------------------------

def get_related_artists(page, target_artist: str):
    """
    Scrape Spotify for an artist's "Fans Also Like" section.
    Returns (source_artist_id, list_of_related) or (None, None) on failure.
    """
    print(f"\n--- Processing: {target_artist} ---")
    gc.collect()

    search_url = f"https://open.spotify.com/search/{target_artist}/artists"
    page.goto(search_url)
    page.wait_for_load_state("networkidle", timeout=10000)

    artist_link_selector = 'a[href^="/artist/"]'
    try:
        page.wait_for_selector(artist_link_selector, state="visible", timeout=5000)
        candidates = page.locator(artist_link_selector).all()
    except Exception as e:
        print(f"Search failed: {e}")
        return None, None

    best_candidate = None
    best_score     = 0.0
    best_name      = None
    best_rank      = -1
    best_url       = None

    scan_limit = min(len(candidates), 30)
    print(f"Scanning top {scan_limit} candidates...")

    for i in range(scan_limit):
        candidate = candidates[i]
        try:
            name_el = candidate.locator('[dir="auto"]').first
            if name_el.count() == 0:
                continue
            name = name_el.inner_text()

            if name.lower() == target_artist.lower():
                print(f"  Candidate {i+1}: '{name}' — EXACT MATCH")
                best_candidate = candidate
                best_name  = name
                best_rank  = i + 1
                best_score = 1.0
                best_url   = candidate.get_attribute("href")
                break

            is_valid, score = validate_artist_name(name, target_artist)
            print(f"  Candidate {i+1}: '{name}' (score: {score:.2f})")
            if is_valid and score > best_score:
                best_candidate = candidate
                best_score = score
                best_name  = name
                best_rank  = i + 1
                best_url   = candidate.get_attribute("href")
        except Exception as e:
            print(f"  Error checking candidate {i}: {e}")

    # Log selection
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        log_exists = DEBUG_FILE.exists()
        with open(DEBUG_FILE, "a", encoding="utf-8") as f:
            if not log_exists:
                f.write("Input_Name,Selected_Name,Rank,Score,URL,Timestamp\n")
            log_name  = best_name  or "NOT_FOUND"
            log_score = best_score if best_name else 0.0
            log_url   = best_url   or "N/A"
            f.write(f'"{target_artist}","{log_name}",{best_rank},{log_score:.2f},"{log_url}","{time.strftime("%Y-%m-%d %H:%M:%S")}"\n')
    except Exception as e:
        print(f"Debug log error: {e}")

    if not best_candidate:
        print(f"No valid match found for '{target_artist}'.")
        return None, None

    print(f"Selected: '{best_name}' (rank {best_rank}, score {best_score:.2f}) → {best_url}")
    try:
        best_candidate.click()
    except Exception:
        if best_url:
            page.goto(f"https://open.spotify.com{best_url}")

    # Extract metadata from artist page
    page.wait_for_load_state("domcontentloaded")
    time.sleep(2)

    metadata = {"Followers": None, "Monthly_Listeners": None, "Genre": None}
    try:
        listeners_loc = page.locator('div:has-text("monthly listeners")').last
        if listeners_loc.count() > 0:
            metadata["Monthly_Listeners"] = extract_number(listeners_loc.inner_text())

        current_url = page.url
        artist_id = current_url.split("/artist/")[-1].split("?")[0]
        if not validate_artist_id(artist_id):
            print(f"Warning: invalid artist ID '{artist_id}'")
            return None, None

        json_ld = page.locator('script[type="application/ld+json"]').first
        if json_ld.count() > 0:
            data = json.loads(json_ld.inner_text())
            if "genre" in data:
                metadata["Genre"] = data["genre"]
            elif "@graph" in data:
                for item in data["@graph"]:
                    if item.get("@type") == "MusicGroup" and "genre" in item:
                        metadata["Genre"] = item["genre"]
                        break
    except Exception as e:
        print(f"Metadata warning: {e}")
        return None, None

    # Navigate to "Fans Also Like"
    try:
        page.goto(f"https://open.spotify.com/artist/{artist_id}/related")
        page.wait_for_load_state("networkidle")
    except Exception as e:
        print(f"Failed to reach related page: {e}")
        return None, None

    # Extract related artists
    try:
        related = []
        cards = page.locator('main a[href^="/artist/"]').all()
        print(f"Found {len(cards)} related artist cards.")

        seen_ids = set()
        for rank, card in enumerate(cards, 1):
            try:
                name_el = card.locator('[dir="auto"]').first
                if name_el.count() == 0:
                    continue
                r_name = name_el.inner_text()
                r_href = card.get_attribute("href")
                r_id   = r_href.split("/")[-1] if r_href else None
                if r_name and r_id and validate_artist_id(r_id) and r_id not in seen_ids:
                    seen_ids.add(r_id)
                    related.append({
                        "Rank":             rank,
                        "Name":             r_name,
                        "ID":               r_id,
                        "Source_Genre":     metadata["Genre"],
                        "Source_Listeners": metadata["Monthly_Listeners"],
                    })
            except Exception:
                continue

        return artist_id, related

    except Exception as e:
        print(f"Error extracting related artists: {e}")
        return None, None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Resume: charge depuis la DB SQLite
    db = Database()
    processed = db.get_processed_artists()
    print(f"Resuming: {len(processed)} artists already processed.")

    if not INPUT_FILE.exists():
        print(f"CRITICAL: input file not found: {INPUT_FILE}")
        sys.exit(1)

    df_input = pd.read_csv(INPUT_FILE)
    all_artists = df_input["Artist"].dropna().unique().tolist()
    remaining   = [a for a in all_artists if a not in processed]

    print(f"Total: {len(all_artists)} | Remaining: {len(remaining)}")
    if not remaining:
        print("All artists already processed.")
        db.close()
        return

    with sync_playwright() as p:
        while remaining:
            try:
                print("\nLaunching browser session...")
                browser = p.chromium.launch(
                    headless=HEADLESS,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(
                    user_agent=get_user_agent(),
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                )
                page = context.new_page()
                apply_stealth(page)

                session_limit = random.randint(10, 15)
                session_count = 0

                for artist in remaining[:]:
                    if session_count >= session_limit:
                        print("Session limit reached, restarting browser...")
                        break

                    try:
                        source_id, data = get_related_artists(page, artist)

                        if data and source_id:
                            similar = [
                                {"name": r["Name"], "id": r["ID"], "rank": i}
                                for i, r in enumerate(data, 1)
                            ]
                            db.save_result(artist, source_id, similar)
                        else:
                            # On enregistre quand même pour ne pas re-tenter en boucle
                            db.save_result(artist, "", [])

                        processed.add(artist)
                        session_count += 1

                        sleep_time = random.uniform(2, 5)
                        print(f"Sleeping {sleep_time:.1f}s...")
                        time.sleep(sleep_time)

                    except Exception as e:
                        print(f"Error processing '{artist}': {e}")
                        if "Target closed" in str(e) or "login" in str(e).lower():
                            raise

                remaining = [a for a in remaining if a not in processed]

                try:
                    page.close()
                    context.close()
                    browser.close()
                except Exception:
                    pass

                browser = context = page = None
                gc.collect()

                if not remaining:
                    break

            except Exception as e:
                print(f"Browser/network crash: {e}")
                try:
                    browser.close()
                except Exception:
                    pass
                wait_for_internet()

    db.close()
    print(f"\nDone. Results in {DATA_DIR / 'similar_artists.db'}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user.")
