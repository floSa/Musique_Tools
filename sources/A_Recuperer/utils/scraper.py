"""Lyon library + Qobuz scraper (adapted from Musique_recherches/scraper.py)."""
import csv
import time
import unicodedata
import os
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8').lower()


def check_match(result_text: str, artist: str, album: str) -> bool:
    normalized_result = normalize_text(result_text)
    normalized_album = normalize_text(album)
    if "disque compact" not in normalized_result:
        return False
    stop_words = {"the", "le", "la", "les", "un", "une", "de", "du", "des", "of", "and", "et", "a", "in", "on"}
    album_tokens = [t for t in normalized_album.split() if t not in stop_words]
    if not album_tokens:
        return True
    album_matches = sum(1 for t in album_tokens if len(t) >= 2 and t in normalized_result)
    return album_matches >= 1


def check_artist_presence(text: str, artist: str) -> bool:
    normalized_text = normalize_text(text)
    normalized_artist = normalize_text(artist)
    stop_words = {"the", "le", "la", "les", "un", "une", "de", "du", "des", "of", "and", "et", "a", "in", "on"}
    artist_tokens = [t for t in normalized_artist.split() if t not in stop_words]
    if not artist_tokens:
        return True
    artist_matches = sum(1 for t in artist_tokens if len(t) >= 2 and t in normalized_text)
    return artist_matches >= 1


def get_best_match(candidates: list, target_album: str) -> str | None:
    normalized_target = normalize_text(target_album)
    stop_words = {"the", "le", "la", "les", "un", "une", "de", "du", "des", "of", "and", "et", "a", "in", "on"}
    target_tokens = {t for t in normalized_target.split() if t not in stop_words and len(t) > 1}
    best_candidate = None
    best_score = 0
    for cand in candidates:
        cand_title = normalize_text(cand['title'])
        cand_tokens = {t for t in cand_title.split() if t not in stop_words and len(t) > 1}
        if not target_tokens:
            score = 1 if not cand_tokens else 0
        else:
            score = len(target_tokens & cand_tokens) / len(target_tokens)
        if normalized_target == cand_title:
            score += 0.5
        elif normalized_target in cand_title:
            score += 0.2
        if score > best_score:
            best_score = score
            best_candidate = cand
    return best_candidate['url'] if best_score >= 0.5 and best_candidate else None


def get_qobuz_link_via_artist(page, artist: str, album: str):
    print(f"   [Qobuz] Strategy: Artist traversal '{artist}' -> '{album}'")
    try:
        encoded_query = urllib.parse.quote(artist)
        page.goto(f"https://www.qobuz.com/fr-fr/search/artists/{encoded_query}")
        try:
            page.wait_for_selector("div.FollowingCard, div.artist-item", timeout=5000)
        except Exception:
            return None

        artist_link = page.locator("div.FollowingCard a.CoverModelOverlay").first
        if not artist_link.count():
            artist_link = page.locator("div.artist-item a").first
        if not artist_link.count():
            return None

        artist_href = artist_link.get_attribute("href")
        if not artist_href or "/interpreter/" not in artist_href:
            return None

        found_artist_name = artist_link.get_attribute("title") or ""
        if not found_artist_name:
            name_el = artist_link.locator("span.name, span.artist").first
            if name_el.count():
                found_artist_name = name_el.inner_text().strip()
            else:
                found_artist_name = artist_link.inner_text().split("\n")[0].strip()

        if not check_artist_presence(found_artist_name, artist):
            return None

        try:
            agree_btn = page.locator("#didomi-notice-agree-button")
            if agree_btn.is_visible(timeout=2000):
                agree_btn.click()
        except Exception:
            pass

        artist_link.click(force=True)
        try:
            page.wait_for_selector("main#artist", timeout=5000)
            time.sleep(1)
        except Exception:
            pass

        release_items = page.locator("div.product__item")
        count = release_items.count()
        candidates = []
        for i in range(min(count, 50)):
            item = release_items.nth(i)
            try:
                title_el = item.locator(".product__name").first
                if not title_el.is_visible():
                    title_el = item.locator(".album-meta__title").first
                title = title_el.inner_text().strip() if title_el.count() else ""
                link_el = item.locator("a[href*='/album/']").first
                url = link_el.get_attribute("href")
                if title and url:
                    if url.startswith("/"):
                        url = f"https://www.qobuz.com{url}"
                    if "/album/" in url:
                        album_id = url.split('/')[-1]
                        candidates.append({'title': title, 'url': f"https://play.qobuz.com/album/{album_id}"})
            except Exception:
                continue

        best_url = get_best_match(candidates, album)
        if best_url:
            best_title = next((c['title'] for c in candidates if c['url'] == best_url), album)
            return (best_url, found_artist_name, best_title)
        return None
    except Exception as e:
        print(f"   [Qobuz] Error in artist traversal: {e}")
        return None


def get_qobuz_play_url(page, artist: str, album: str):
    res = get_qobuz_link_via_artist(page, artist, album)
    if res:
        return res

    print("   [Qobuz] Fallback to direct search...")
    try:
        encoded_query = urllib.parse.quote(f"{artist} {album}")
        page.goto(f"https://www.qobuz.com/fr-fr/search?q={encoded_query}")
        try:
            page.wait_for_selector("div.album-item, a[href*='/album/']", timeout=5000)
        except Exception:
            pass

        album_items = page.locator("div.album-item")
        count = album_items.count()
        if count > 0:
            for i in range(min(count, 5)):
                item = album_items.nth(i)
                try:
                    artist_el = item.locator(".artist").first
                    found_artist = artist_el.inner_text().strip() if artist_el.count() else ""
                    title_el = item.locator(".title").first
                    found_album = title_el.inner_text().strip() if title_el.count() else ""
                    if found_artist and check_artist_presence(found_artist, artist):
                        link = item.locator("a[href*='/album/']").first
                        if link.count():
                            href = link.get_attribute("href")
                            if href:
                                album_id = href.split('/')[-1]
                                return (f"https://play.qobuz.com/album/{album_id}", found_artist, found_album)
                except Exception:
                    continue
        else:
            link = page.locator("a[href*='/fr-fr/album/']").first
            if link.count():
                href = link.get_attribute("href")
                found_album = link.get_attribute("title") or link.inner_text().strip()
                if "Plus de détails sur " in found_album and " par " in found_album:
                    found_album = found_album.split("Plus de détails sur ")[1].split(" par ")[0]
                if href:
                    album_id = href.split('/')[-1]
                    return (f"https://play.qobuz.com/album/{album_id}", "", found_album)

        encoded_query = urllib.parse.quote(f"{artist} {album}")
        return f"https://www.qobuz.com/fr-fr/search?q={encoded_query}"
    except Exception as e:
        print(f"   [Qobuz] Error in direct search: {e}")
        encoded_query = urllib.parse.quote(f"{artist} {album}")
        return f"https://www.qobuz.com/fr-fr/search?q={encoded_query}"


def run_scraper(
    input_csv: str | Path,
    output_csv: str | Path,
) -> None:
    """
    Run the Lyon library + Qobuz scraper.

    Args:
        input_csv: CSV with columns Artist, Album (albums to search).
        output_csv: Where to write results (appends to existing file).
    """
    input_csv = Path(input_csv)
    output_csv = Path(output_csv)

    processed_keys: set = set()
    if output_csv.exists():
        with open(output_csv, 'r', encoding='utf-8') as f:
            try:
                reader = csv.DictReader(f)
                if reader.fieldnames:
                    for row in reader:
                        a = row.get('Artist', '').strip()
                        b = row.get('Album', '').strip()
                        if a and b:
                            processed_keys.add((a, b))
            except Exception as e:
                print(f"Warning: could not read existing output: {e}")

    albums = []
    try:
        with open(input_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'Artist' in row:
                    row['Artist'] = row['Artist'].strip()
                if 'Album' in row:
                    row['Album'] = row['Album'].strip()
                albums.append(row)
    except FileNotFoundError:
        print(f"Fichier d'entrée non trouvé : {input_csv}")
        return

    fieldnames = ["Artist", "Album", "Status", "Cote", "Artiste_Bibliotheque",
                  "Artiste_Qobuz", "Album_Qobuz", "Disponibilité", "Qobuz_URL"]
    write_header = not output_csv.exists() or output_csv.stat().st_size == 0

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, 'a', newline='', encoding='utf-8') as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
            f_out.flush()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="fr-FR",
            )
            page = context.new_page()

            print("Ouverture du catalogue BM Lyon...")
            try:
                page.goto("https://catalogue.bm-lyon.fr/")
                try:
                    page.wait_for_selector("input", timeout=10000)
                except Exception:
                    pass
            except Exception as e:
                print(f"Erreur ouverture catalogue: {e}")

            for item in albums:
                artist = item['Artist']
                album = item['Album']

                if (artist, album) in processed_keys:
                    print(f"--- Skipping: {artist} - {album} ---")
                    continue

                print(f"--- Processing: {artist} - {album} ---")
                result_data = {
                    "Artist": artist, "Album": album, "Status": "Pending",
                    "Cote": "", "Artiste_Bibliotheque": "", "Artiste_Qobuz": "",
                    "Album_Qobuz": "", "Disponibilité": "", "Qobuz_URL": "",
                }

                try:
                    try:
                        search_input = page.get_by_placeholder("Recherche", exact=False).first
                        if not search_input.is_visible():
                            raise Exception("not visible")
                    except Exception:
                        page.goto("https://catalogue.bm-lyon.fr/")
                        page.wait_for_selector("input", timeout=10000)

                    search_input = page.get_by_placeholder("Recherche", exact=False).first
                    if not search_input.count():
                        search_input = page.locator("input[type='search']").first
                    if not search_input.count():
                        search_input = page.locator("input[type='text']").first

                    query = f"{artist} {album} Disque compact"
                    search_input.fill(query)
                    search_input.press("Enter")

                    found_results = False
                    try:
                        page.wait_for_selector("text=Disque compact", timeout=10000)
                        time.sleep(2)
                        found_results = True
                    except Exception:
                        time.sleep(2)

                    found_cote = False
                    if found_results:
                        MAX_CANDIDATES = 12
                        candidate_index = 0
                        while candidate_index < MAX_CANDIDATES:
                            try:
                                try:
                                    page.wait_for_selector("a", timeout=5000)
                                except Exception:
                                    pass
                                links = page.get_by_role("link").all()
                                matching_links = [
                                    link for link in links
                                    if link.is_visible()
                                    and len(link.inner_text().strip()) >= 10
                                    and check_match(link.inner_text().strip(), artist, album)
                                ]
                                if candidate_index >= len(matching_links):
                                    break
                                candidate = matching_links[candidate_index]
                                candidate_txt = candidate.inner_text().strip()
                                print(f"Checking candidate {candidate_index + 1}: {candidate_txt[:60]}...")
                                try:
                                    candidate.click()
                                    time.sleep(3)
                                    content_text = page.locator("body").inner_text()
                                    if not check_artist_presence(content_text, artist):
                                        page.go_back()
                                        candidate_index += 1
                                        try:
                                            page.wait_for_selector("div[class*='jss']", timeout=10000)
                                        except Exception:
                                            time.sleep(2)
                                        continue
                                    if "Part-Dieu" in content_text:
                                        lines = content_text.split('\n')
                                        cotes_found = []
                                        statuses_found = []
                                        for idx, line in enumerate(lines):
                                            if "Part-Dieu" in line and idx + 1 < len(lines):
                                                next_line = lines[idx + 1]
                                                if "-" in next_line:
                                                    parts = next_line.split('-')
                                                    cote = parts[0].strip()
                                                    status = parts[-1].strip() if len(parts) > 1 else "Voir dispo"
                                                    if cote not in cotes_found:
                                                        cotes_found.append(cote)
                                                        statuses_found.append(status)
                                        if cotes_found:
                                            parts_title = candidate_txt.split('\n')
                                            full_title = parts_title[0].strip() if parts_title else ""
                                            result_data["Artiste_Bibliotheque"] = full_title.split(" - ")[0].strip() if " - " in full_title else full_title
                                            result_data["Status"] = "Found"
                                            result_data["Cote"] = " - ".join(cotes_found)
                                            result_data["Disponibilité"] = " - ".join(statuses_found)
                                            found_cote = True
                                            break
                                        else:
                                            page.go_back()
                                            candidate_index += 1
                                            time.sleep(2)
                                    else:
                                        page.go_back()
                                        candidate_index += 1
                                        time.sleep(2)
                                except Exception as e:
                                    print(f"Error processing candidate: {e}")
                                    page.go_back()
                                    candidate_index += 1
                                    time.sleep(2)
                            except Exception as e:
                                print(f"Error in candidate loop: {e}")
                                break

                    if not found_cote:
                        result_data["Status"] = "Part-Dieu Not Listed"
                        qobuz_res = get_qobuz_play_url(page, artist, album)
                        if isinstance(qobuz_res, tuple):
                            result_data["Qobuz_URL"], result_data["Artiste_Qobuz"], result_data["Album_Qobuz"] = qobuz_res
                        else:
                            result_data["Qobuz_URL"] = qobuz_res

                except Exception as e:
                    print(f"Error processing {album}: {e}")
                    result_data["Status"] = "Error"

                writer.writerow(result_data)
                f_out.flush()

            browser.close()

    print(f"Terminé. Résultats dans : {output_csv}")
