"""Lyon library + Qobuz scraper.

Matching strict (vs l'ancienne version "1 token commun suffit") basé sur
`difflib.SequenceMatcher` et un parsing structuré du format ISBD utilisé par
catalogue.bm-lyon.fr. Voir `text_match.py` pour les helpers communs.
"""
import csv
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

from .text_match import (
    ARTIST_MATCH_THRESHOLD,
    name_similarity,
    normalize,
    parse_bm_lyon_title,
    score_bm_lyon_candidate,
)


# Seuils
BM_CANDIDATE_THRESHOLD = 0.55   # score combiné (album+auteur) pour qu'un résultat de liste soit retenu
BM_TOP_K_CANDIDATES = 5         # on clique au plus sur les K meilleurs si le 1er n'est pas Part-Dieu
QOBUZ_ALBUM_THRESHOLD = 0.55    # score titre album dans la discographie d'un artiste


# Compat : utilisé par d'éventuels imports externes
def normalize_text(text: str) -> str:
    return normalize(text)


# ---------------------------------------------------------------------------
# Logging debug
# ---------------------------------------------------------------------------

def _log_selection(debug_path: Path, row: dict) -> None:
    """Append une ligne au CSV debug_selection.csv (créé au besoin)."""
    fieldnames = [
        "Timestamp", "Source", "Artist_input", "Album_input",
        "Selected_text", "Score", "Score_artist", "Score_album",
        "URL", "Status",
    ]
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not debug_path.exists() or debug_path.stat().st_size == 0
    with open(debug_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            w.writeheader()
        # Force toutes les clés
        w.writerow({k: row.get(k, "") for k in fieldnames})


# ---------------------------------------------------------------------------
# BM Lyon — extraction de la cote sur la page de détail
# ---------------------------------------------------------------------------

def _extract_part_dieu_cote(content_text: str) -> tuple[list[str], list[str]]:
    """Parse les blocs "Part-Dieu\\n<cote> - <statut>" du texte d'une page de
    détail catalogue. Retourne (cotes, statuts) alignés."""
    cotes_found: list[str] = []
    statuses_found: list[str] = []
    lines = content_text.split("\n")
    for idx, line in enumerate(lines):
        if "Part-Dieu" in line and idx + 1 < len(lines):
            next_line = lines[idx + 1].strip()
            if not next_line:
                continue
            # Le séparateur fiable est " - " (espaces autour). Splitter sur "-"
            # nu casse les cotes type "782.42-AIR".
            if " - " in next_line:
                parts = next_line.rsplit(" - ", 1)
                cote = parts[0].strip()
                status = parts[1].strip() if len(parts) > 1 else "Voir dispo"
            else:
                cote = next_line
                status = "Voir dispo"
            if cote and cote not in cotes_found:
                cotes_found.append(cote)
                statuses_found.append(status)
    return cotes_found, statuses_found


# ---------------------------------------------------------------------------
# Qobuz — recherche artiste puis discographie (strict)
# ---------------------------------------------------------------------------

def _pick_best_qobuz_album(candidates: list[dict], target_album: str) -> dict | None:
    """Choisit le meilleur album d'une liste de {title, url}.

    Score = name_similarity(target_album, candidate.title), bonus de +0.15 si
    le candidat est un préfixe / sur-ensemble du target (gère "Album X (Deluxe Edition)").
    Retourne le dict candidat ou None.
    """
    if not candidates:
        return None
    target_n = normalize(target_album)
    best = None
    best_score = 0.0
    for c in candidates:
        cand_n = normalize(c["title"])
        score = name_similarity(target_album, c["title"])
        if cand_n and target_n and (cand_n in target_n or target_n in cand_n):
            score = min(1.0, score + 0.15)
        if score > best_score:
            best_score = score
            best = dict(c, score=score)
    if best and best_score >= QOBUZ_ALBUM_THRESHOLD:
        return best
    return None


def get_qobuz_link_via_artist(page, artist: str, album: str):
    """Stratégie 1 : trouver la page artiste, parcourir sa discographie."""
    print(f"   [Qobuz] Strategy: Artist traversal '{artist}' -> '{album}'")
    try:
        encoded_query = urllib.parse.quote(artist)
        page.goto(f"https://www.qobuz.com/fr-fr/search/artists/{encoded_query}")
        try:
            page.wait_for_selector("div.FollowingCard, div.artist-item, a[href*='/interpreter/']", timeout=5000)
        except Exception:
            return None

        # Récolter TOUS les artistes-candidats et garder le meilleur match (vs
        # `.first` qui prenait n'importe quoi, en particulier pour les noms
        # ambigus type "Air", "M83", "Worakls").
        artist_candidates = page.evaluate(
            """() => {
                const links = Array.from(document.querySelectorAll("a[href*='/interpreter/']"));
                const seen = new Set();
                const out = [];
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    if (seen.has(href)) continue;
                    seen.add(href);
                    const titleAttr = a.getAttribute('title') || '';
                    const nameSpan = a.querySelector('span.name, .artist-name, .catalog-heading__name');
                    const nameTxt = (nameSpan && nameSpan.textContent.trim()) || a.textContent.trim().split('\\n')[0];
                    out.push({href, displayName: titleAttr || nameTxt});
                }
                return out;
            }"""
        )

        if not artist_candidates:
            return None

        best_artist = None
        best_artist_score = 0.0
        for c in artist_candidates:
            href = c.get("href", "")
            if "/interpreter/" not in href:
                continue
            slug_as_name = href.split("/interpreter/")[1].split("/")[0].replace("-", " ")
            score = max(
                name_similarity(c["displayName"], artist),
                name_similarity(slug_as_name, artist),
            )
            if score > best_artist_score:
                best_artist_score = score
                best_artist = dict(c, score=score)

        if not best_artist or best_artist_score < ARTIST_MATCH_THRESHOLD:
            print(f"   [Qobuz] No strict artist match (best={best_artist_score:.2f})")
            return None

        href = best_artist["href"]
        if href.startswith("/"):
            href = f"https://www.qobuz.com{href}"

        try:
            agree_btn = page.locator("#didomi-notice-agree-button")
            if agree_btn.is_visible(timeout=2000):
                agree_btn.click()
        except Exception:
            pass

        page.goto(href, wait_until="domcontentloaded", timeout=15000)
        try:
            page.wait_for_selector("main#artist, div.product__item, div.album-meta", timeout=6000)
            time.sleep(1)
        except Exception:
            pass

        release_items = page.locator("div.product__item")
        count = release_items.count()
        candidates: list[dict] = []
        for i in range(min(count, 50)):
            item = release_items.nth(i)
            try:
                title_el = item.locator(".product__name").first
                if not title_el.count():
                    title_el = item.locator(".album-meta__title").first
                title = title_el.inner_text().strip() if title_el.count() else ""
                link_el = item.locator("a[href*='/album/']").first
                url = link_el.get_attribute("href") if link_el.count() else ""
                if title and url:
                    if url.startswith("/"):
                        url = f"https://www.qobuz.com{url}"
                    if "/album/" in url:
                        album_id = url.split('/')[-1]
                        candidates.append({
                            "title": title,
                            "url": f"https://play.qobuz.com/album/{album_id}",
                        })
            except Exception:
                continue

        best_album = _pick_best_qobuz_album(candidates, album)
        if best_album:
            return (best_album["url"], best_artist["displayName"], best_album["title"], best_album["score"])
        print(f"   [Qobuz] No album match in discography ({len(candidates)} candidates)")
        return None
    except Exception as e:
        print(f"   [Qobuz] Error in artist traversal: {e}")
        return None


def get_qobuz_play_url(page, artist: str, album: str):
    """Point d'entrée Qobuz : artist-traversal puis fallback recherche directe."""
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
            scored = []
            for i in range(min(count, 10)):
                item = album_items.nth(i)
                try:
                    artist_el = item.locator(".artist").first
                    found_artist = artist_el.inner_text().strip() if artist_el.count() else ""
                    title_el = item.locator(".title").first
                    found_album = title_el.inner_text().strip() if title_el.count() else ""
                    link = item.locator("a[href*='/album/']").first
                    href = link.get_attribute("href") if link.count() else ""
                    if not (found_artist and href):
                        continue
                    s_artist = name_similarity(found_artist, artist)
                    s_album = name_similarity(found_album, album) if found_album else 0.0
                    score = 0.5 * s_artist + 0.5 * s_album
                    if s_artist >= ARTIST_MATCH_THRESHOLD:
                        scored.append({
                            "score": score, "s_artist": s_artist, "s_album": s_album,
                            "artist": found_artist, "album": found_album, "href": href,
                        })
                except Exception:
                    continue

            if scored:
                scored.sort(key=lambda x: x["score"], reverse=True)
                top = scored[0]
                album_id = top["href"].split('/')[-1]
                return (
                    f"https://play.qobuz.com/album/{album_id}",
                    top["artist"], top["album"], top["score"],
                )

        # Pas de div.album-item → on tente le 1er lien /fr-fr/album/, mais sans
        # garantie d'artiste (on ne peut pas valider). On renvoie l'URL de
        # recherche pour que l'utilisateur tranche manuellement.
        encoded_query = urllib.parse.quote(f"{artist} {album}")
        return f"https://www.qobuz.com/fr-fr/search?q={encoded_query}"
    except Exception as e:
        print(f"   [Qobuz] Error in direct search: {e}")
        encoded_query = urllib.parse.quote(f"{artist} {album}")
        return f"https://www.qobuz.com/fr-fr/search?q={encoded_query}"


# ---------------------------------------------------------------------------
# BM Lyon — orchestrateur
# ---------------------------------------------------------------------------

def _process_bm_lyon(page, artist: str, album: str, debug_path: Path) -> dict:
    """Cherche un album sur catalogue.bm-lyon.fr et tente d'extraire la cote
    Part-Dieu. Retourne un dict partiel à fusionner dans `result_data`."""
    out = {
        "Artiste_Bibliotheque": "",
        "Cote": "",
        "Disponibilité": "",
        "Status": "Part-Dieu Not Listed",
    }

    try:
        # S'assurer qu'on a un input de recherche
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

        try:
            page.wait_for_selector("text=Disque compact", timeout=10000)
            time.sleep(2)
        except Exception:
            time.sleep(2)

        # Phase 1 — récolter les liens-candidats et les scorer SANS cliquer
        try:
            page.wait_for_selector("a", timeout=5000)
        except Exception:
            pass

        links = page.get_by_role("link").all()
        scored_candidates = []
        seen_texts = set()
        for link in links:
            try:
                if not link.is_visible():
                    continue
                txt = link.inner_text().strip()
                if len(txt) < 10:
                    continue
                # Pré-filtre : les entrées catalogue ont systématiquement
                # "Disque compact" dans le libellé
                if "disque compact" not in normalize(txt):
                    continue
                if txt in seen_texts:
                    continue
                seen_texts.add(txt)
                score, parsed = score_bm_lyon_candidate(txt, artist, album)
                if score >= BM_CANDIDATE_THRESHOLD:
                    scored_candidates.append({
                        "score": score, "text": txt, "parsed": parsed, "link": link,
                    })
            except Exception:
                continue

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top = scored_candidates[:BM_TOP_K_CANDIDATES]

        if not top:
            _log_selection(debug_path, {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Source": "BM_Lyon", "Artist_input": artist, "Album_input": album,
                "Selected_text": "", "Score": 0.0, "Status": "no_candidate_above_threshold",
            })
            return out

        # Phase 2 — cliquer sur les top-K dans l'ordre, jusqu'à trouver Part-Dieu
        for cand in top:
            try:
                cand_text = cand["text"]
                parsed = cand["parsed"]
                print(f"   [BM] Trying candidate (score={cand['score']:.2f}): {cand_text[:80]!r}")
                cand["link"].click()
                time.sleep(3)
                content_text = page.locator("body").inner_text()

                # Re-vérification stricte de l'artiste sur la page de détail
                # (au cas où le parsing de la liste s'est trompé)
                page_artist_ok = False
                if parsed["author"]:
                    page_artist_ok = name_similarity(parsed["author"], artist) >= ARTIST_MATCH_THRESHOLD
                if not page_artist_ok:
                    # Fallback : le nom de l'artiste apparaît-il dans le titre h1 / breadcrumb ?
                    try:
                        h1 = page.locator("h1").first
                        h1_text = h1.inner_text() if h1.count() else ""
                        page_artist_ok = name_similarity(h1_text, artist) >= ARTIST_MATCH_THRESHOLD
                    except Exception:
                        pass

                if not page_artist_ok:
                    print(f"   [BM] Artist mismatch on detail page, skipping")
                    page.go_back()
                    try:
                        page.wait_for_selector("a", timeout=10000)
                    except Exception:
                        time.sleep(2)
                    continue

                if "Part-Dieu" in content_text:
                    cotes, statuses = _extract_part_dieu_cote(content_text)
                    if cotes:
                        out["Artiste_Bibliotheque"] = parsed["author"] or cand_text.split("\n")[0]
                        out["Status"] = "Found"
                        out["Cote"] = " - ".join(cotes)
                        out["Disponibilité"] = " - ".join(statuses)
                        _log_selection(debug_path, {
                            "Timestamp": datetime.now().isoformat(timespec="seconds"),
                            "Source": "BM_Lyon", "Artist_input": artist, "Album_input": album,
                            "Selected_text": cand_text[:200], "Score": round(cand["score"], 3),
                            "Score_artist": "", "Score_album": "",
                            "URL": page.url, "Status": "found_part_dieu",
                        })
                        return out
                # Pas de Part-Dieu sur ce candidat → suivant
                page.go_back()
                try:
                    page.wait_for_selector("a", timeout=10000)
                except Exception:
                    time.sleep(2)
            except Exception as e:
                print(f"   [BM] Error on candidate: {e}")
                try:
                    page.go_back()
                    time.sleep(2)
                except Exception:
                    pass
                continue

        # Aucun des top-K n'avait Part-Dieu
        _log_selection(debug_path, {
            "Timestamp": datetime.now().isoformat(timespec="seconds"),
            "Source": "BM_Lyon", "Artist_input": artist, "Album_input": album,
            "Selected_text": top[0]["text"][:200] if top else "",
            "Score": round(top[0]["score"], 3) if top else 0.0,
            "Status": "no_part_dieu_in_top_candidates",
        })
        return out

    except Exception as e:
        print(f"   [BM] Error: {e}")
        out["Status"] = "Error"
        return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_scraper(input_csv: str | Path, output_csv: str | Path) -> None:
    """Run the Lyon library + Qobuz scraper.

    Args:
        input_csv: CSV with columns Artist, Album.
        output_csv: Where to write results (appends to existing file).
    """
    input_csv = Path(input_csv)
    output_csv = Path(output_csv)
    debug_csv = output_csv.with_name("debug_selection.csv")

    processed_keys: set = set()
    if output_csv.exists():
        with open(output_csv, "r", encoding="utf-8") as f:
            try:
                reader = csv.DictReader(f)
                if reader.fieldnames:
                    for row in reader:
                        a = row.get("Artist", "").strip()
                        b = row.get("Album", "").strip()
                        if a and b:
                            processed_keys.add((a, b))
            except Exception as e:
                print(f"Warning: could not read existing output: {e}")

    albums = []
    try:
        with open(input_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "Artist" in row:
                    row["Artist"] = row["Artist"].strip()
                if "Album" in row:
                    row["Album"] = row["Album"].strip()
                albums.append(row)
    except FileNotFoundError:
        print(f"Fichier d'entrée non trouvé : {input_csv}")
        return

    fieldnames = [
        "Artist", "Album", "Status", "Cote", "Artiste_Bibliotheque",
        "Artiste_Qobuz", "Album_Qobuz", "Disponibilité", "Qobuz_URL",
    ]
    write_header = not output_csv.exists() or output_csv.stat().st_size == 0

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "a", newline="", encoding="utf-8") as f_out:
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
                artist = item["Artist"]
                album = item["Album"]

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
                    bm_res = _process_bm_lyon(page, artist, album, debug_csv)
                    result_data.update(bm_res)

                    if result_data["Status"] != "Found":
                        qobuz_res = get_qobuz_play_url(page, artist, album)
                        if isinstance(qobuz_res, tuple):
                            # Nouveau format : (url, artist, album, score)
                            if len(qobuz_res) == 4:
                                url, q_art, q_alb, q_score = qobuz_res
                            else:
                                url, q_art, q_alb = qobuz_res
                                q_score = None
                            result_data["Qobuz_URL"] = url
                            result_data["Artiste_Qobuz"] = q_art
                            result_data["Album_Qobuz"] = q_alb
                            _log_selection(debug_csv, {
                                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                                "Source": "Qobuz", "Artist_input": artist, "Album_input": album,
                                "Selected_text": f"{q_art} — {q_alb}",
                                "Score": round(q_score, 3) if q_score else "",
                                "URL": url, "Status": "found",
                            })
                        else:
                            result_data["Qobuz_URL"] = qobuz_res
                            _log_selection(debug_csv, {
                                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                                "Source": "Qobuz", "Artist_input": artist, "Album_input": album,
                                "Selected_text": "", "URL": qobuz_res or "",
                                "Status": "search_url_only",
                            })

                except Exception as e:
                    print(f"Error processing {album}: {e}")
                    result_data["Status"] = "Error"

                writer.writerow(result_data)
                f_out.flush()

            browser.close()

    print(f"Terminé. Résultats dans : {output_csv}")
    print(f"Debug log     : {debug_csv}")
