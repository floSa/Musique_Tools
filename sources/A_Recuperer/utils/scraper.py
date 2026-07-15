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
    artist_name_matches,
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

BM_HOME = "https://catalogue.bm-lyon.fr"

# Cache par artiste des notices CD enrichies (title/author/href/cote/statut),
# rempli au fil d'un run. `albums_a_rechercher` étant trié par artiste, les
# albums d'un même artiste se suivent : on évite ainsi de re-scraper ses CD
# (1 recherche + N notices) pour chacun de ses albums. Réinitialisé à chaque
# process (pas de persistance disque).
_BM_ARTIST_CACHE: dict = {}


def _bm_search_input(page):
    """Retourne le champ de recherche du catalogue (plusieurs fallbacks)."""
    for loc in [page.get_by_placeholder("Recherche", exact=False).first,
                page.locator("input[type='search']").first,
                page.locator("input[type='text']").first,
                page.locator("input").first]:
        try:
            if loc.count():
                return loc
        except Exception:
            pass
    return None


def _parse_notice_text(txt: str) -> tuple[str, str]:
    """Découpe un libellé de résultat 'Titre [support] / Auteur' en (titre, auteur).

    Sur la page de RÉSULTATS, le libellé est en ordre NATUREL
    ('Trafic [Disque compact] / Gaëtan Roussel'), contrairement à la fiche
    détail qui inverse ('Roussel, Gaëtan'). C'est donc la source la plus fiable
    pour filtrer par auteur.
    """
    if " / " not in txt:
        return txt.split("[")[0].strip(), ""
    left, right = txt.split(" / ", 1)
    title = left.split("[")[0].strip()
    author = re.split(r"[;.]", right)[0].strip()
    return title, author


def _harvest_artist_cd_notices(page, artist_q: str, max_notices: int = 25) -> list[dict]:
    """Récolte tous les CD d'un artiste à la BM Lyon via navigation par facette.

    Chercher un album précis sur catalogue.bm-lyon.fr est peu fiable : l'artiste
    est souvent noyé/dispersé (films, compilations, autres artistes) et pas en
    tête. On reproduit le geste manuel :

    1. rechercher l'ARTISTE seul ;
    2. filtrer la facette 'CD musicaux' (retire DVD/livres/partitions) ;
    3. dérouler toute la page (lazy-load) ;
    4. garder les notices '[Disque compact]' dont l'auteur (ordre naturel du
       libellé) matche l'artiste — robuste à l'inversion et à la position.

    Retourne [{title, author, href}] dédoublonné par titre.
    """
    if not artist_q:
        return []
    try:
        page.goto(f"{BM_HOME}/", wait_until="domcontentloaded", timeout=20000)
        try:
            page.wait_for_selector("input", timeout=10000)
        except Exception:
            time.sleep(2)
        inp = _bm_search_input(page)
        if inp is None:
            print("   [BM] -> no search input found")
            return []
        inp.fill("")
        inp.fill(artist_q)
        inp.press("Enter")
        time.sleep(4)

        # Facette 'CD musicaux' (best-effort) : réduit le bruit DVD/livres.
        try:
            cd = page.get_by_text(re.compile(r"CD musicaux", re.I)).first
            if cd.count():
                cd.click()
                time.sleep(2.5)
        except Exception:
            pass

        # Dérouler jusqu'à stabiliser le nombre de notices (lazy-load).
        prev = -1
        for _ in range(15):
            n = page.locator("a[href*='/notice']").count()
            if n == prev:
                break
            prev = n
            page.mouse.wheel(0, 6000)
            time.sleep(0.8)

        pairs = page.eval_on_selector_all(
            "a[href*='/notice']",
            "els => els.map(e => ({t:(e.textContent||'').replace(/\\s+/g,' ').trim(), href:e.getAttribute('href')||''})).filter(o => o.t)"
        )
    except Exception as e:
        print(f"   [BM] Error harvesting notices for {artist_q!r}: {e}")
        return []

    out: list[dict] = []
    seen: set = set()
    for o in pairs:
        txt = o["t"]
        if "disque compact" not in normalize(txt):
            continue
        title, author = _parse_notice_text(txt)
        # allow_subset=True : le catalogue écrit souvent un nom de scène seul
        # ("Bourvil") quand l'artiste cible est le nom complet ("André
        # Bourvil"), sim=0.70 < seuil strict. Sans cette tolérance (perdue
        # dans le passage à la navigation par artiste), de nombreux artistes
        # bien réels sont déclarés absents à tort (ex. Bourvil, régression
        # constatée sur un run complet : 33% -> 21% de Found).
        if not title or not artist_name_matches(author, artist_q, allow_subset=True):
            continue
        key = normalize(title)
        if not key or key in seen:
            continue
        seen.add(key)
        href = o["href"]
        if href.startswith("/"):
            href = BM_HOME + href
        out.append({"title": title, "author": author, "href": href})
        if len(out) >= max_notices:
            break
    print(f"   [BM] {artist_q!r}: {len(pairs)} notices lues, {len(out)} CD retenus (match auteur)")
    return out


def _notice_part_dieu_cote(page, href: str) -> tuple[str, str]:
    """Navigue vers une notice et renvoie (cote, statut) Part-Dieu ('' si absent)."""
    try:
        page.goto(href, wait_until="domcontentloaded", timeout=20000)
        time.sleep(2)
        body = page.locator("body").inner_text()
        if "Part-Dieu" in body:
            cotes, statuses = _extract_part_dieu_cote(body)
            if cotes:
                return cotes[0], (statuses[0] if statuses else "")
    except Exception:
        pass
    return "", ""


def _find_other_bm_lyon_albums(page, artist_q: str, exclude_title: str,
                                debug_path: Path, artist_brut: str,
                                max_extra: int = 12) -> str:
    """Liste les CD du même artiste à la BM Lyon (hors `exclude_title`), avec cote.

    Basée sur `_harvest_artist_cd_notices` (recherche artiste + facette CD +
    filtre auteur), puis lecture de la cote Part-Dieu sur chaque notice.
    Retourne 'Album1 - Cote1, Album2 - Cote2' ('' si rien).
    """
    print(f"   [BM-extras] Autres albums de {artist_q!r} (exclut {exclude_title!r})")
    notices = _harvest_artist_cd_notices(page, artist_q)
    excl = normalize(exclude_title)
    out_parts: list[str] = []
    for nt in notices:
        if normalize(nt["title"]) == excl:
            continue
        cote, _statut = _notice_part_dieu_cote(page, nt["href"])
        if cote:
            out_parts.append(f"{nt['title']} - {cote}")
        if len(out_parts) >= max_extra:
            break
    _log_selection(debug_path, {
        "Timestamp": datetime.now().isoformat(timespec="seconds"),
        "Source": "BM_Lyon_OtherAlbums",
        "Artist_input": artist_brut, "Album_input": exclude_title,
        "Selected_text": f"notices={len(notices)}, kept={len(out_parts)}",
        "Score": "", "URL": "", "Status": f"{len(out_parts)} extra albums",
    })
    return ", ".join(out_parts)


def _extract_bm_lyon_detail_author(content_text: str) -> str:
    """Extrait le champ `Auteur : <nom>` depuis le texte brut de la page.

    Le catalogue affiche au-dessus de l'éditeur :
        Auteur : Daft Punk (groupe) [30]
        Éditeur : Music Brokers, 2015 [8]
    On lit ce label sur le texte déjà extrait via `page.locator("body").inner_text()`,
    plus robuste que de chasser le DOM avec un TreeWalker.

    On retire les annotations type "(groupe)", "(compositeur)" et les
    compteurs "[30]". Le catalogue écrit l'auteur en forme INVERSÉE
    "Nom, Prénom" (ex. "Roussel, Gaëtan (1972-....)") : on la remet dans
    l'ordre naturel "Prénom Nom" pour que la comparaison à l'artiste Spotify
    fonctionne (sinon on ne garderait que "Roussel"). Plusieurs auteurs sont
    séparés par ';' → on garde le premier. Retourne "" si pas de label.
    """
    if not content_text:
        return ""
    # Match "Auteur :" en début de ligne ou après newline ; capture jusqu'à
    # newline ou prochain label (Éditeur, Date, Type, etc.)
    m = re.search(r"Auteur\s*:\s*(.+?)(?:\n|$)", content_text)
    if not m:
        return ""
    raw = m.group(1)
    # Retirer parenthèses (dates de vie, "(groupe)"…) et crochets ("[30]")
    s = re.sub(r"\([^)]*\)", "", raw)
    s = re.sub(r"\[[^\]]*\]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Plusieurs auteurs → séparés par ';' dans le catalogue : on garde le 1er.
    s = s.split(";")[0].strip()
    # Dés-inversion "Nom, Prénom" → "Prénom Nom". Une seule virgule = forme
    # inversée d'un auteur unique ; plusieurs = on garde le 1er bloc.
    if s.count(",") == 1:
        nom, prenom = [p.strip() for p in s.split(",", 1)]
        s = f"{prenom} {nom}".strip() if (nom and prenom) else (nom or prenom)
    elif s.count(",") > 1:
        s = s.split(",")[0].strip()
    # Retirer points/tirets parasites en fin
    return s.rstrip(".,;:- ").strip()


def _bm_lyon_detail_artist_matches(page, content_text: str, target_artist: str,
                                   parsed_author: str = "") -> bool:
    """Re-vérifie sur la fiche détail BM Lyon que l'album est bien de `target_artist`.

    Trois sources, par fiabilité décroissante :

    1. le champ ``Auteur :`` de la fiche (label explicite du catalogue) — on
       tolère le sous-ensemble strict ("Bourvil" ⊂ "Andre Bourvil") via
       ``allow_subset`` car c'est la source la plus fiable ;
    2. l'auteur parsé du libellé ISBD du lien de résultat (``parsed_author``) ;
    3. en dernier recours, le ``<h1>`` de la page.

    Garde-fou indispensable : le filtre amont (liste de résultats) laisse
    passer les candidats dont le libellé n'expose pas d'auteur parsable ; sans
    cette re-vérification au clic, des albums d'autres artistes finissent en
    ``Found`` / ``Autres_albums_biblio``. C'est la fonction que le refactor
    3b7054d référençait sans la définir (NameError silencieusement avalé par
    les ``try/except`` appelants → matching BM Lyon totalement HS).
    """
    detail_author = _extract_bm_lyon_detail_author(content_text)
    if detail_author and artist_name_matches(detail_author, target_artist, allow_subset=True):
        return True
    if parsed_author and artist_name_matches(parsed_author, target_artist):
        return True
    try:
        h1 = page.locator("h1").first
        h1_text = h1.inner_text() if h1.count() else ""
        if h1_text and artist_name_matches(h1_text, target_artist):
            return True
    except Exception:
        pass
    return False


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
            # nu casse les cotes type "782.42-AIR". Une cote ne contient JAMAIS
            # elle-même " - " (espace-tiret-espace) ; en revanche le statut
            # peut être multi-parties ("Prêté - Retour prévu le : ...") — donc
            # on découpe sur la PREMIÈRE occurrence (pas la dernière, qui
            # coupait la cote en 2 : "782.ARC 61 - Prêté" au lieu de
            # "782.ARC 61" quand le statut avait lui-même un " - ").
            if " - " in next_line:
                parts = next_line.split(" - ", 1)
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

def _scroll_to_load(page, selector: str, max_rounds: int = 6, pause: float = 0.8) -> int:
    """Scrolle jusqu'à stabiliser le nombre d'éléments `selector` (lazy-load).

    Qobuz charge ses résultats au défilement : sans ça on ne voit que la
    première salve, et le bon résultat — pas toujours en tête (ex. "Christophe"
    noyé sous "Christopher …", ou un album précis loin dans la discographie) —
    n'est jamais récolté. C'est exactement le « on ne descend pas assez ».
    Retourne le compte final d'éléments.
    """
    prev = -1
    count = 0
    try:
        for _ in range(max_rounds):
            count = page.locator(selector).count()
            if count == prev:
                break
            prev = count
            page.mouse.wheel(0, 5000)
            time.sleep(pause)
    except Exception:
        pass
    return count


def _artist_match_threshold(artist_q: str) -> float:
    """Seuil de similarité artiste, durci pour les noms courts.

    Les noms courts (~6 lettres) sont des quasi-homographes dangereux ("Air"
    vs "Air Supply", "M83") : on exige un match quasi exact pour ne pas
    retenir un voisin. Noms longs : seuil standard. Note : un nom comme
    "Christophe" (10 lettres) reste sous le seuil standard — c'est la
    préférence au match EXACT (cf. `get_qobuz_link_via_artist`), combinée au
    scroll, qui le départage de "Christopher".
    """
    n = normalize(artist_q)
    return 0.95 if len(n) <= 6 else ARTIST_MATCH_THRESHOLD


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

        # Lazy-load : descendre pour récolter plus que la première salve
        # d'artistes (le bon n'est pas toujours en tête).
        _scroll_to_load(page, "a[href*='/interpreter/']")

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
        exact_artist = None          # match normalisé EXACT (display ou slug)
        target_n = normalize(artist_q)
        for c in artist_candidates:
            href = c.get("href", "")
            if "/interpreter/" not in href:
                continue
            slug_as_name = href.split("/interpreter/")[1].split("/")[0].replace("-", " ")
            # Nettoyer le displayName (retirer "21 albums", "Suivre", etc.)
            cleaned_display = _clean_qobuz_display(c.get("displayName", ""))
            # Si après nettoyage il ne reste rien d'utile, on retombe sur le slug
            display_for_score = cleaned_display or slug_as_name
            # Préférence au match EXACT : "Christophe" (exact) doit battre
            # "Christopher" (flou à 0.95). Le premier exact rencontré suffit
            # (1.0 est le plafond).
            if target_n and exact_artist is None and (
                normalize(display_for_score) == target_n
                or normalize(slug_as_name) == target_n
            ):
                exact_artist = dict(c, score=1.0, displayName=display_for_score)
                break
            score = max(
                name_similarity(display_for_score, artist_q),
                name_similarity(slug_as_name, artist_q),
            )
            if score > best_artist_score:
                best_artist_score = score
                best_artist = dict(c, score=score, displayName=display_for_score)

        if exact_artist is not None:
            best_artist = exact_artist
            best_artist_score = 1.0

        threshold = _artist_match_threshold(artist_q)
        if not best_artist or best_artist_score < threshold:
            print(f"   [Qobuz] No strict artist match "
                  f"(best={best_artist_score:.2f}, seuil={threshold:.2f})")
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

        # Discographie souvent lazy-loadée : scroller pour ne pas manquer un
        # album situé bas dans la liste avant de chercher le bon titre.
        release_items = page.locator("div.product__item")
        _scroll_to_load(page, "div.product__item", max_rounds=8)
        count = release_items.count()
        candidates: list[dict] = []
        for i in range(min(count, 150)):
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

        # Lazy-load : descendre avant de récolter (cap relevé à 25 vs 10).
        _scroll_to_load(page, "div.album-item")
        album_items = page.locator("div.album-item")
        count = album_items.count()
        artist_threshold = _artist_match_threshold(artist_q)
        if count > 0:
            scored = []
            for i in range(min(count, 25)):
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
                    if s_artist >= artist_threshold:
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
    """Localise un album à la BM Lyon + liste les autres CD du même artiste.

    Navigation par artiste (cf. `_harvest_artist_cd_notices`) : on récolte TOUS
    les CD de l'artiste, on lit la cote Part-Dieu de chacun, puis :
      - album cible = meilleure correspondance de titre → Cote / Disponibilité / Found ;
      - les autres → Autres_albums_biblio.
    Une seule recherche par artiste, robuste aux artistes « durs à retrouver »
    (dispersés, noms inversés, mélangés à des films) — le cas Gaëtan Roussel.
    """
    out = {
        "Artiste_Bibliotheque": "",
        "Cote": "",
        "Disponibilité": "",
        "Status": "Part-Dieu Not Listed",
        "Autres_albums_biblio": "",
    }
    # Collabs "Ghostpoet,Paul Smith" → on cherche l'artiste primaire seul.
    artist_q = _primary_artist(artist)

    try:
        # Cache par artiste (albums d'un même artiste consécutifs dans l'input).
        cache_key = normalize(artist_q)
        if cache_key in _BM_ARTIST_CACHE:
            enriched = _BM_ARTIST_CACHE[cache_key]
        else:
            notices = _harvest_artist_cd_notices(page, artist_q)
            # Lire la cote Part-Dieu de chaque notice (cap coût réseau).
            enriched = []
            for nt in notices[:15]:
                cote, statut = _notice_part_dieu_cote(page, nt["href"])
                enriched.append({**nt, "cote": cote, "statut": statut})
            _BM_ARTIST_CACHE[cache_key] = enriched

        if not enriched:
            _log_selection(debug_path, {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Source": "BM_Lyon", "Artist_input": artist, "Album_input": album,
                "Selected_text": "", "Score": 0.0, "Status": "artist_not_in_bm",
            })
            return out

        # Album cible = meilleure correspondance de titre parmi ses CD.
        best, best_s = None, 0.0
        for e in enriched:
            s = name_similarity(e["title"], album)
            if s > best_s:
                best_s, best = s, e
        main = best if (best and best_s >= 0.65) else None

        if main and main["cote"]:
            out["Status"] = "Found"
            out["Cote"] = main["cote"]
            out["Disponibilité"] = main["statut"]
            out["Artiste_Bibliotheque"] = main["author"] or artist_q
            _log_selection(debug_path, {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Source": "BM_Lyon", "Artist_input": artist, "Album_input": album,
                "Selected_text": f"{main['title']} (sim={best_s:.2f})",
                "Score": round(best_s, 3), "URL": main["href"], "Status": "found_part_dieu",
            })
        else:
            # Artiste présent en biblio mais album cible pas trouvé / sans cote.
            out["Artiste_Bibliotheque"] = enriched[0]["author"] or artist_q
            _log_selection(debug_path, {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Source": "BM_Lyon", "Artist_input": artist, "Album_input": album,
                "Selected_text": f"artiste present, album absent (best={best_s:.2f})",
                "Score": round(best_s, 3), "Status": "album_not_found_artist_present",
            })

        # Autres albums = ceux avec cote, hors l'album cible, cap 12.
        main_title_n = normalize(main["title"]) if main else normalize(album)
        others = []
        for e in enriched:
            if not e["cote"] or normalize(e["title"]) == main_title_n:
                continue
            others.append(f"{e['title']} - {e['cote']}")
            if len(others) >= 12:
                break
        out["Autres_albums_biblio"] = ", ".join(others)
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
