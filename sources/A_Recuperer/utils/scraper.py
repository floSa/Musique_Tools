"""Lyon library + Qobuz scraper.

Matching strict (vs l'ancienne version "1 token commun suffit") basé sur
`difflib.SequenceMatcher` et un parsing structuré du format ISBD utilisé par
catalogue.bm-lyon.fr. Voir `text_match.py` pour les helpers communs.
"""
import csv
import re
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
# CONVENTION URL QOBUZ — IMPORTANT
# ---------------------------------------------------------------------------
# Toute URL Qobuz **retournée à l'utilisateur** (qui finira dans
# resultats_cotes.csv et resultats_final.csv) DOIT pointer sur
# `play.qobuz.com`, jamais `www.qobuz.com`.
#
# - URL d'album trouvé : https://play.qobuz.com/album/<id>
# - URL de recherche fallback : https://play.qobuz.com/search/<query>
#
# Les URLs `www.qobuz.com/...` peuvent apparaître DANS LE CODE mais
# uniquement pour la NAVIGATION INTERNE du scraper (Playwright doit y
# aller pour parser les pages publiques car play.qobuz.com est une SPA
# login-walled). Ces URLs internes ne doivent jamais sortir vers
# l'utilisateur.
# ---------------------------------------------------------------------------

def _qobuz_search_url(artist: str, album: str) -> str:
    """URL Qobuz à renvoyer quand on n'a pas trouvé l'album précis.

    Format : `https://play.qobuz.com/search/<query>` (jamais www.qobuz.com).
    """
    return f"https://play.qobuz.com/search/{urllib.parse.quote(f'{artist} {album}')}"


# Regex pour extraire le slug et l'id Qobuz d'une URL /interpreter/<slug>/<id>
INTERPRETER_RE = re.compile(r"/interpreter/([^/?#]+)/(\d+)")


def _primary_artist(name: str) -> str:
    """Retourne l'artiste principal d'une chaîne potentiellement multi-artistes.

    Spotify écrit parfois "Ghostpoet,Paul Smith" ou "-M-,Jordan Cauvin,Thibault
    Cauvin" pour les collaborations. Pour les recherches catalogue (BM Lyon,
    Qobuz), on lance la recherche avec UNIQUEMENT le premier artiste, sinon
    on ne matche aucun artiste réel (puisque "Ghostpoet,Paul Smith" n'est le
    nom d'aucune entité dans les catalogues).

    Split sur `,` exact (sans espace requis avant/après), strip de chaque
    résultat, retour du premier non-vide. Si pas de virgule, retourne tel
    quel.
    """
    if not name:
        return ""
    for part in str(name).split(","):
        p = part.strip()
        if p:
            return p
    return str(name).strip()


def _clean_qobuz_display(s: str) -> str:
    """Nettoie le texte d'un lien artiste Qobuz pour ne garder que le nom.

    Le textContent d'un `<a>` artiste sur Qobuz inclut souvent du bruit
    en plus du nom : "Worakls\\n21 albums", "L'Impératrice • Suivre", etc.
    Sans nettoyage, on retrouve "21 albums" ou "Suivre" en `Artiste_Qobuz`.
    """
    if not s:
        return ""
    # Première ligne uniquement
    s = s.split("\n")[0]
    # Retirer compteurs "X albums", "X album"
    s = re.sub(r"\b\d+\s+albums?\b", "", s, flags=re.IGNORECASE)
    # Retirer indicateurs UI courants
    s = re.sub(r"\b(suivre|follow|artiste|artist)\b", "", s, flags=re.IGNORECASE)
    # Couper sur les séparateurs verticaux
    s = s.split("|")[0].split("•")[0]
    # Compresser whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


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

def _find_other_bm_lyon_albums(page, artist_q: str, exclude_title: str,
                                debug_path: Path, artist_brut: str,
                                max_extra: int = 8) -> str:
    """Cherche les autres albums "Disque compact" du même artiste à la BM Lyon.

    Stratégie :
    1. Recherche `{artist_q} Disque compact` (artiste seul, sans album)
    2. Récolte les liens dont le libellé contient `[Disque compact]` ET dont
       le `parsed.author` matche l'artiste cible.
    3. Pour chaque candidat (top `max_extra`, hors `exclude_title`), clique,
       lit `Auteur :` + cote Part-Dieu.
    4. Retourne une chaîne "Album1 - Cote1, Album2 - Cote2" (string vide
       si rien trouvé d'autre).

    `exclude_title` est l'album principal déjà trouvé — on ne le re-liste pas.
    """
    print(f"   [BM-extras] Recherche autres albums de {artist_q!r} (exclut {exclude_title!r})")
    if not artist_q:
        print("   [BM-extras] -> skip (artist_q vide)")
        return ""

    out_parts: list[str] = []
    try:
        # On part toujours d'une page propre (catalogue d'accueil) pour
        # éviter les comportements imprévus depuis la page de détail.
        page.goto("https://catalogue.bm-lyon.fr/")
        try:
            page.wait_for_selector("input", timeout=10000)
        except Exception:
            time.sleep(2)
        search_input = page.get_by_placeholder("Recherche", exact=False).first
        if not search_input.count():
            search_input = page.locator("input[type='search']").first
        if not search_input.count():
            search_input = page.locator("input[type='text']").first
        if not search_input.count():
            print("   [BM-extras] -> no search input found")
            return ""

        # Vider d'abord (input peut contenir une recherche précédente)
        search_input.fill("")
        search_input.fill(f"{artist_q} Disque compact")
        search_input.press("Enter")
        try:
            page.wait_for_selector("text=Disque compact", timeout=10000)
            time.sleep(2)
        except Exception:
            time.sleep(2)

        # Collecter les liens-candidats avec leur texte (libellé ISBD)
        links = page.get_by_role("link").all()
        candidates = []
        seen_titles = set()
        exclude_norm = normalize(exclude_title)
        seen_count = 0
        author_match_count = 0
        for link in links:
            try:
                if not link.is_visible():
                    continue
                txt = link.inner_text().strip()
                if len(txt) < 10 or "disque compact" not in normalize(txt):
                    continue
                seen_count += 1
                parsed = parse_bm_lyon_title(txt)
                # Filtre : auteur doit matcher l'artiste cible
                if parsed["author"]:
                    sim = name_similarity(parsed["author"], artist_q)
                    if sim < ARTIST_MATCH_THRESHOLD:
                        continue
                else:
                    # Pas d'auteur parsable dans le libellé → on continue
                    # (la re-vérification sur la page de détail tranchera)
                    pass
                author_match_count += 1
                # Titre propre : retirer "[Disque compact]" et autres "[...]"
                # même si parse_bm_lyon_title n'avait pas de ` / ` pour cleaner.
                clean_title = re.sub(r"\[.*?\]", "", parsed["title"])
                clean_title = re.sub(r"\s+", " ", clean_title).strip()
                t_norm = normalize(clean_title)
                if not t_norm or t_norm == exclude_norm or t_norm in seen_titles:
                    continue
                seen_titles.add(t_norm)
                candidates.append({"title": clean_title, "link": link})
                if len(candidates) >= max_extra:
                    break
            except Exception:
                continue

        # Pour chaque candidat, cliquer et lire la cote Part-Dieu
        for cand in candidates:
            try:
                cand["link"].click()
                time.sleep(2)
                content_text = page.locator("body").inner_text()
                if "Part-Dieu" in content_text:
                    cotes, _ = _extract_part_dieu_cote(content_text)
                    if cotes:
                        out_parts.append(f"{cand['title']} - {cotes[0]}")
                page.go_back()
                try:
                    page.wait_for_selector("a", timeout=10000)
                except Exception:
                    time.sleep(2)
            except Exception:
                try:
                    page.go_back()
                    time.sleep(1)
                except Exception:
                    pass
                continue

        print(f"   [BM-extras] {artist_q!r}: {seen_count} disque compact dans la liste, "
              f"{author_match_count} match auteur, {len(candidates)} candidats, "
              f"{len(out_parts)} cotes recuperees")
        _log_selection(debug_path, {
            "Timestamp": datetime.now().isoformat(timespec="seconds"),
            "Source": "BM_Lyon_OtherAlbums",
            "Artist_input": artist_brut, "Album_input": exclude_title,
            "Selected_text": f"seen={seen_count}, matched={author_match_count}, kept={len(candidates)}",
            "Score": "",
            "URL": "", "Status": f"{len(out_parts)} extra albums",
        })
    except Exception as e:
        print(f"   [BM] Error in other-albums search: {e}")

    return ", ".join(out_parts)


def _extract_bm_lyon_detail_author(content_text: str) -> str:
    """Extrait le champ `Auteur : <nom>` depuis le texte brut de la page.

    Le catalogue affiche au-dessus de l'éditeur :
        Auteur : Daft Punk (groupe) [30]
        Éditeur : Music Brokers, 2015 [8]
    On lit ce label sur le texte déjà extrait via `page.locator("body").inner_text()`,
    plus robuste que de chasser le DOM avec un TreeWalker.

    On retire les annotations type "(groupe)", "(compositeur)" et les
    compteurs "[30]". S'il y a plusieurs auteurs séparés par virgule, on
    garde le premier. La virgule de fin (cas "Bourvil,") est aussi
    nettoyée. Retourne "" si pas de label trouvé.
    """
    if not content_text:
        return ""
    # Match "Auteur :" en début de ligne ou après newline ; capture jusqu'à
    # newline ou prochain label (Éditeur, Date, Type, etc.)
    m = re.search(r"Auteur\s*:\s*(.+?)(?:\n|$)", content_text)
    if not m:
        return ""
    raw = m.group(1)
    # Retirer parenthèses et crochets
    s = re.sub(r"\([^)]*\)", "", raw)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Si plusieurs auteurs séparés par ",", garder le premier
    s = s.split(",")[0].strip()
    # Retirer points/tirets parasites en fin
    return s.rstrip(".,;:- ").strip()


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
    # Pour les collabs "Ghostpoet,Paul Smith" → on ne cherche que "Ghostpoet"
    artist_q = _primary_artist(artist)
    print(f"   [Qobuz] Strategy: Artist traversal '{artist_q}' -> '{album}'")
    try:
        encoded_query = urllib.parse.quote(artist_q)
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
            # Nettoyer le displayName (retirer "21 albums", "Suivre", etc.)
            cleaned_display = _clean_qobuz_display(c.get("displayName", ""))
            # Si après nettoyage il ne reste rien d'utile, on retombe sur le slug
            display_for_score = cleaned_display or slug_as_name
            score = max(
                name_similarity(display_for_score, artist_q),
                name_similarity(slug_as_name, artist_q),
            )
            if score > best_artist_score:
                best_artist_score = score
                best_artist = dict(c, score=score, displayName=display_for_score)

        if not best_artist or best_artist_score < ARTIST_MATCH_THRESHOLD:
            print(f"   [Qobuz] No strict artist match (best={best_artist_score:.2f})")
            return None

        href = best_artist["href"]
        # Extraire l'id artiste pour pouvoir renvoyer un lien artist-only
        # si on ne trouve pas l'album précis dans la discographie.
        m_id = INTERPRETER_RE.search(href)
        qobuz_artist_id = m_id.group(2) if m_id else None
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
        # Fallback artist-only : on a trouvé l'artiste mais pas l'album précis.
        # On renvoie le lien `play.qobuz.com/artist/<id>` pour que l'utilisateur
        # puisse parcourir la discographie manuellement (cas Ghostpoet, AaRON).
        if qobuz_artist_id:
            artist_url = f"https://play.qobuz.com/artist/{qobuz_artist_id}"
            print(f"   [Qobuz] -> fallback artist link: {artist_url}")
            return (artist_url, best_artist["displayName"], "", 0.0)
        return None
    except Exception as e:
        print(f"   [Qobuz] Error in artist traversal: {e}")
        return None


def get_qobuz_play_url(page, artist: str, album: str):
    """Point d'entrée Qobuz : artist-traversal puis fallback recherche directe."""
    res = get_qobuz_link_via_artist(page, artist, album)
    if res:
        return res

    # Fallback : recherche directe Qobuz (idem split artiste primaire)
    artist_q = _primary_artist(artist)
    print("   [Qobuz] Fallback to direct search...")
    try:
        encoded_query = urllib.parse.quote(f"{artist_q} {album}")
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
                    found_artist_raw = artist_el.inner_text().strip() if artist_el.count() else ""
                    found_artist = _clean_qobuz_display(found_artist_raw)
                    title_el = item.locator(".title").first
                    found_album = title_el.inner_text().strip() if title_el.count() else ""
                    link = item.locator("a[href*='/album/']").first
                    href = link.get_attribute("href") if link.count() else ""
                    if not (found_artist and href):
                        continue
                    s_artist = name_similarity(found_artist, artist_q)
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

        # Pas de match : on renvoie l'URL de recherche play.qobuz.com pour que
        # l'utilisateur tranche manuellement. IMPORTANT : on utilise toujours
        # play.qobuz.com (jamais www.qobuz.com) dans les URLs retournees.
        return _qobuz_search_url(artist, album)
    except Exception as e:
        print(f"   [Qobuz] Error in direct search: {e}")
        return _qobuz_search_url(artist, album)


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
        "Autres_albums_biblio": "",
    }

    # Pour les collabs "Ghostpoet,Paul Smith" → recherche sur "Ghostpoet" seul.
    # Le matching ultérieur compare aussi sur l'artiste primaire.
    artist_q = _primary_artist(artist)

    try:
        # Toujours partir d'un état propre (catalogue d'accueil) pour éviter
        # que le state laissé par l'itération précédente (page de résultats,
        # détail album, page artiste) ne pollue la recherche courante.
        try:
            page.goto("https://catalogue.bm-lyon.fr/", wait_until="domcontentloaded", timeout=15000)
            try:
                page.wait_for_selector("input", timeout=10000)
            except Exception:
                time.sleep(2)
        except Exception:
            pass

        search_input = page.get_by_placeholder("Recherche", exact=False).first
        if not search_input.count():
            search_input = page.locator("input[type='search']").first
        if not search_input.count():
            search_input = page.locator("input[type='text']").first

        query = f"{artist_q} {album} Disque compact"
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
                score, parsed = score_bm_lyon_candidate(txt, artist_q, album)
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

                # Re-vérification stricte de l'artiste sur la page de détail.
                # On compare contre l'artiste primaire (post-split virgule).
                # Idéalement on lit le champ "Auteur :" de la page (label
                # explicite du catalogue), avec fallback sur le parsing ISBD
                # du libellé du lien puis sur le h1.
                page_artist_ok = False
                detail_author = _extract_bm_lyon_detail_author(content_text)
                sim_detail = name_similarity(detail_author, artist_q) if detail_author else 0.0
                if detail_author:
                    page_artist_ok = sim_detail >= ARTIST_MATCH_THRESHOLD
                    # Fallback : sur la page de détail (contexte déjà filtré
                    # par la recherche initiale), on accepte le cas où le nom
                    # BM Lyon est un sous-ensemble strict de l'artiste Spotify
                    # (ex: "Bourvil" ⊂ "Andre Bourvil"). On exige des tokens
                    # ≥ 5 chars pour éviter "Air" ⊂ "Air Supply".
                    if not page_artist_ok:
                        d_tokens = set(normalize(detail_author).split())
                        a_tokens = set(normalize(artist_q).split())
                        if (d_tokens and a_tokens and d_tokens.issubset(a_tokens)
                                and all(len(t) >= 5 for t in d_tokens)):
                            page_artist_ok = True
                            sim_detail = 0.99  # marqueur pour le log
                sim_parsed = name_similarity(parsed["author"], artist_q) if parsed["author"] else 0.0
                if not page_artist_ok and parsed["author"]:
                    page_artist_ok = sim_parsed >= ARTIST_MATCH_THRESHOLD
                h1_text = ""
                sim_h1 = 0.0
                if not page_artist_ok:
                    try:
                        h1 = page.locator("h1").first
                        h1_text = h1.inner_text() if h1.count() else ""
                        sim_h1 = name_similarity(h1_text, artist_q)
                        page_artist_ok = sim_h1 >= ARTIST_MATCH_THRESHOLD
                    except Exception:
                        pass
                if not page_artist_ok:
                    print(f"   [BM] Artist mismatch: target={artist_q!r}; "
                          f"detail_author={detail_author!r}(sim={sim_detail:.2f}); "
                          f"parsed_author={parsed['author']!r}(sim={sim_parsed:.2f}); "
                          f"h1={h1_text[:60]!r}(sim={sim_h1:.2f})")

                if not page_artist_ok:
                    page.go_back()
                    try:
                        page.wait_for_selector("a", timeout=10000)
                    except Exception:
                        time.sleep(2)
                    continue

                if "Part-Dieu" in content_text:
                    cotes, statuses = _extract_part_dieu_cote(content_text)
                    if cotes:
                        # Préférer le `Auteur :` lu sur la page de détail
                        # (plus fiable que le parsing du libellé du lien)
                        out["Artiste_Bibliotheque"] = (
                            detail_author or parsed["author"] or cand_text.split("\n")[0]
                        )
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
                        # Maintenant on cherche les AUTRES albums du même
                        # artiste à la BM Lyon (max 8 albums supplémentaires
                        # avec leur cote, séparés par ", ")
                        out["Autres_albums_biblio"] = _find_other_bm_lyon_albums(
                            page, artist_q,
                            exclude_title=parsed["title"] or album,
                            debug_path=debug_path,
                            artist_brut=artist,
                        )
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
        "Autres_albums_biblio",
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
                    "Cote": "", "Artiste_Bibliotheque": "", "Autres_albums_biblio": "",
                    "Artiste_Qobuz": "", "Album_Qobuz": "", "Disponibilité": "", "Qobuz_URL": "",
                }

                try:
                    # BM Lyon = source primaire (Cote + Disponibilité)
                    bm_res = _process_bm_lyon(page, artist, album, debug_csv)
                    result_data.update(bm_res)

                    # Qobuz = source secondaire, TOUJOURS interrogé (même si
                    # BM Lyon a trouvé) — l'utilisateur veut les deux infos
                    # côte à côte dans le fichier final.
                    qobuz_res = get_qobuz_play_url(page, artist, album)
                    if isinstance(qobuz_res, tuple):
                        # Match précis : (url album, artist, album, score)
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
                        # Pas de match précis Qobuz → on ne stocke PAS une URL
                        # /search/ (l'utilisateur ne veut pas de faux liens dans
                        # le xlsx). On laisse Qobuz_URL vide.
                        result_data["Qobuz_URL"] = ""
                        _log_selection(debug_csv, {
                            "Timestamp": datetime.now().isoformat(timespec="seconds"),
                            "Source": "Qobuz", "Artist_input": artist, "Album_input": album,
                            "Selected_text": "", "URL": qobuz_res or "",
                            "Status": "no_match_qobuz",
                        })

                except Exception as e:
                    print(f"Error processing {album}: {e}")
                    result_data["Status"] = "Error"

                writer.writerow(result_data)
                f_out.flush()

            browser.close()

    print(f"Terminé. Résultats dans : {output_csv}")
    print(f"Debug log     : {debug_csv}")
