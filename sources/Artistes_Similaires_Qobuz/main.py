"""
Scraper Qobuz — artistes similaires + portrait via les pages publiques
www.qobuz.com/fr-fr/interpreter/{slug}/{id}.

Pourquoi pas play.qobuz.com ? Parce que play est une SPA login-walled. Les
mêmes artistes sont accessibles publiquement sur www.qobuz.com (le miroir
SEO) — c'est ce que fait déjà le scraper de A_Recuperer pour trouver les
URLs d'albums. On suit le même principe.

Usage :
    cd sources/Artistes_Similaires_Qobuz
    uv run python main.py
    HEADLESS=false uv run python main.py    # mode visible pour debug

Stockage : SQLite (`data/Artistes_Similaires_Qobuz/similar_artists.db`),
schéma aligné sur Last.fm + Spotify (cf. `database.py`).

Reprend automatiquement là où il s'est arrêté (lit la DB au démarrage).

Matching artiste : la coquille du scraper existant (`check_artist_presence`)
acceptait un match dès 1 token commun, ce qui mélangeait les homonymes
(ex. "Worakls" → matchait aussi "Kevin Worakls"). On utilise ici un fuzzy
strict (`difflib.SequenceMatcher`, seuil 0.85) avec priorité au match exact.
"""
import difflib
import gc
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse
from pathlib import Path

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from database import Database

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR   = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_Qobuz"
INPUT_FILE = Path(__file__).parent.parent.parent / "data" / "Ressources" / "artistes_liste.csv"

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEARCH_URL_TPL = "https://www.qobuz.com/fr-fr/search/artists/{q}"
NAME_MATCH_THRESHOLD = 0.85
INTERPRETER_RE = re.compile(r"/interpreter/([^/]+)/(\d+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_text(s: str) -> str:
    """Minuscules + ASCII (gère 'L'Impératrice', 'N'to'…)."""
    if not s:
        return ""
    return unicodedata.normalize("NFKD", s).encode("ASCII", "ignore").decode("utf-8").lower().strip()


def name_similarity(a: str, b: str) -> float:
    a_n, b_n = normalize_text(a), normalize_text(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    return difflib.SequenceMatcher(None, a_n, b_n).ratio()


def get_user_agent() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


def apply_stealth(context):
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(context)
    except Exception:
        pass


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
        time.sleep(30)


def dismiss_cookie_banner(page):
    try:
        page.locator("#didomi-notice-agree-button").click(timeout=2000)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Étape 1 : trouver la page artiste sur www.qobuz.com
# ---------------------------------------------------------------------------

def find_artist_page(page, target_artist: str) -> tuple[str, str, str] | None:
    """Cherche un artiste par nom, retourne (artist_url, slug, qobuz_id) ou None.

    On ramasse tous les liens /interpreter/, on calcule la similarité de nom
    pour chacun (basée sur le slug et le texte affiché), on garde le meilleur
    avec un seuil strict (0.85). Cette logique évite les coquilles des
    versions précédentes du scraper où "Worakls" matchait aussi "Kevin Worakls".
    """
    url = SEARCH_URL_TPL.format(q=urllib.parse.quote(target_artist))
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    except PlaywrightTimeoutError:
        print(f"  Timeout sur la recherche de '{target_artist}'")
        return None

    time.sleep(1.5)
    dismiss_cookie_banner(page)
    time.sleep(1)

    # Récupérer tous les liens /interpreter/ avec le texte affiché
    candidates = page.evaluate(
        """() => {
            const links = Array.from(document.querySelectorAll("a[href*='/interpreter/']"));
            const seen = new Set();
            const out = [];
            for (const a of links) {
                const href = a.getAttribute('href') || '';
                if (seen.has(href)) continue;
                seen.add(href);
                // Extraire le nom : title attribute > .name span > textContent
                const titleAttr = a.getAttribute('title') || '';
                const nameSpan = a.querySelector('span.name, .artist-name, .catalog-heading__name');
                const nameTxt = (nameSpan && nameSpan.textContent.trim()) || a.textContent.trim().split('\\n')[0];
                out.push({href, displayName: titleAttr || nameTxt});
            }
            return out;
        }"""
    )

    if not candidates:
        return None

    # Scoring : on calcule la similarité du nom affiché ET du slug, on garde le max
    best = None
    best_score = 0.0
    for c in candidates:
        m = INTERPRETER_RE.search(c["href"])
        if not m:
            continue
        slug, qobuz_id = m.group(1), m.group(2)
        # Similarité sur le slug (transformé en mots) et sur le nom affiché
        slug_as_name = slug.replace("-", " ")
        score = max(
            name_similarity(c["displayName"], target_artist),
            name_similarity(slug_as_name, target_artist),
        )
        if score > best_score:
            best_score = score
            best = (c["href"], slug, qobuz_id, c["displayName"], score)

    if not best or best_score < NAME_MATCH_THRESHOLD:
        if best:
            print(f"  Aucun match strict pour '{target_artist}' (meilleur : '{best[3]}' @ {best_score:.2f})")
        return None

    href, slug, qobuz_id, display, score = best
    if not href.startswith("http"):
        href = "https://www.qobuz.com" + href
    print(f"  Match : '{display}' (slug={slug}, id={qobuz_id}, score={score:.2f})")
    return href, slug, qobuz_id


# ---------------------------------------------------------------------------
# Étape 2 : extraire bio + similaires depuis la page artiste
# ---------------------------------------------------------------------------

def extract_artist_data(page, artist_url: str) -> dict:
    """Retourne {portrait: str, similar: list[{name, slug, qobuz_id, rank}]}."""
    try:
        page.goto(artist_url, wait_until="domcontentloaded", timeout=20_000)
    except PlaywrightTimeoutError:
        print(f"  Timeout sur la page artiste {artist_url}")
        return {"portrait": "", "similar": []}

    time.sleep(2)
    dismiss_cookie_banner(page)
    time.sleep(1)

    # 1) Portrait — le texte complet est dans #catalog-heading__text (id, pas
    # classe). Il est tronqué visuellement par CSS via la checkbox #expand-toggle
    # mais entièrement présent dans le DOM. On utilise text_content qui ignore
    # la visibilité.
    portrait = ""
    try:
        bio_el = page.locator("#catalog-heading__text").first
        if bio_el.count():
            raw = bio_el.text_content() or ""
            # Compresser les whitespaces (le HTML a beaucoup de \n et d'indentation)
            portrait = re.sub(r"\s+", " ", raw).strip()
    except Exception as e:
        print(f"  Erreur extraction portrait : {e}")

    # 2) Similaires — section h3.catalog-heading__subtitle "Artistes similaires"
    # On scope strictement au .catalog-heading parent pour ne pas capturer
    # d'autres carrousels d'artistes ailleurs sur la page.
    similar: list[dict] = []
    try:
        items = page.evaluate(
            """() => {
                const headers = Array.from(document.querySelectorAll('h3.catalog-heading__subtitle'));
                const target = headers.find(h => h.textContent.trim().toLowerCase().startsWith('artistes similaires'));
                if (!target) return [];
                const section = target.closest('.catalog-heading') || target.parentElement;
                const links = Array.from(section.querySelectorAll('a.catalog-heading__item'));
                return links.map(a => {
                    const nameEl = a.querySelector('span.catalog-heading__name');
                    return {
                        name: (nameEl ? nameEl.textContent : a.textContent).trim(),
                        href: a.getAttribute('href') || '',
                    };
                });
            }"""
        )
        for i, it in enumerate(items, 1):
            m = INTERPRETER_RE.search(it["href"])
            if not m or not it["name"]:
                continue
            similar.append({
                "name": it["name"],
                "slug": m.group(1),
                "qobuz_id": m.group(2),
                "rank": i,
            })
    except Exception as e:
        print(f"  Erreur extraction similaires : {e}")

    return {"portrait": portrait, "similar": similar}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = Database()
    processed = db.get_processed_artists()
    print(f"Resuming: {len(processed)} artists already processed.")

    if not INPUT_FILE.exists():
        print(f"CRITICAL: input file not found: {INPUT_FILE}")
        sys.exit(1)

    df_input = pd.read_csv(INPUT_FILE)
    all_artists = df_input["Artist"].dropna().unique().tolist()
    remaining = [a for a in all_artists if a not in processed]

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
                    locale="fr-FR",
                )
                apply_stealth(context)
                page = context.new_page()

                session_limit = random.randint(15, 25)
                session_count = 0

                for artist in remaining[:]:
                    if session_count >= session_limit:
                        print("Session limit reached, restarting browser...")
                        break

                    try:
                        print(f"\n[{time.strftime('%H:%M:%S')}] {artist}")
                        match = find_artist_page(page, artist)
                        if not match:
                            # On enregistre quand même pour ne pas retomber dessus
                            db.save_result(artist, "", [], portrait="")
                            processed.add(artist)
                            session_count += 1
                            time.sleep(random.uniform(2, 4))
                            continue

                        artist_url, slug, qobuz_id = match
                        data = extract_artist_data(page, artist_url)

                        similar_dicts = [
                            {"name": s["name"], "id": s["qobuz_id"], "rank": s["rank"]}
                            for s in data["similar"]
                        ]
                        db.save_result(artist, qobuz_id, similar_dicts, portrait=data["portrait"])
                        print(f"  → {len(similar_dicts)} similaires, portrait {len(data['portrait'])} chars")

                        processed.add(artist)
                        session_count += 1
                        time.sleep(random.uniform(2, 5))

                    except Exception as e:
                        print(f"Error processing '{artist}': {e}")
                        if "Target closed" in str(e):
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
        print("\nInterrupted.")
