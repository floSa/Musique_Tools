# Service : Artistes_Similaires_Qobuz

Scrape la section **"Artistes similaires"** et le **Portrait** (biographie) de
Qobuz pour enrichir la base de similarités, en complément de Last.fm et Spotify.

---

## Objectif

Pour chaque artiste d'une liste source, récupérer via le web Qobuz (sans
authentification) :
- La liste ordonnée des **artistes similaires** (1 = le plus proche)
- L'**ID Qobuz** de l'artiste source (pour générer des liens)
- Le **portrait** : biographie/présentation Qobuz, si exposée publiquement

Qobuz, comme Spotify, ne fournit pas de score numérique — seul le rang
d'apparition est disponible.

---

## Pourquoi `www.qobuz.com` et pas `play.qobuz.com` ?

`play.qobuz.com` est la SPA (web app) de lecture, **réservée aux abonnés**.
Sans login, on n'a accès qu'à la page d'accueil générique.

`www.qobuz.com` (le miroir SEO) expose les mêmes pages artistes en HTML rendu
côté serveur, **accessible sans compte**. Le service `A_Recuperer` utilise déjà
ce subdomain pour trouver les liens d'albums. On suit le même principe.

L'URL d'un artiste sur `www.qobuz.com` est :

    https://www.qobuz.com/fr-fr/interpreter/{slug}/{id}

L'ID est différent de celui de `play.qobuz.com` (ex. Worakls = `525535` sur
www, `3586950` sur play). On stocke celui de www.qobuz.com.

---

## Différences avec Last.fm / Spotify

| Critère | Last.fm | Spotify | Qobuz |
|---|---|---|---|
| Source | API officielle | Scraping web (login pour play) | Scraping web (public) |
| Similarité | Score 0–1 | Rang 1–40 | Rang 1–~80 |
| Genres / tags | Oui | Genre principal | Non exposé |
| Bio / portrait | Non | Non | **Oui** (texte de présentation) |
| ID artiste | MusicBrainz ID | Spotify ID (22 chars) | ID numérique Qobuz |
| Vitesse | ~0.5 s/artiste | ~5–10 s/artiste | ~5–8 s/artiste |
| Robustesse | Très stable | Sensible aux anti-bots | Stable (pas d'auth) |

---

## Architecture des fichiers

```
sources/Artistes_Similaires_Qobuz/
├── main.py                  # Scraper principal (écrit dans la DB SQLite)
├── database.py              # Wrapper SQLite (interface alignée Last.fm/Spotify)
├── export_to_csv.py         # Export DB → CSV (dérivé pour lecture humaine)
├── pyproject.toml
└── requirements.txt
```

---

## Données

### Input

**`data/Ressources/artistes_liste.csv`** — partagé entre tous les services
de scraping (cf. `A_Recuperer --extract-artists`).

### Output (source de vérité)

**`data/Artistes_Similaires_Qobuz/similar_artists.db`** — base SQLite avec le
même schéma que Last.fm + Spotify, plus un champ `portrait` :

```sql
CREATE TABLE artists (
    source_artist TEXT PRIMARY KEY,
    source_artist_id TEXT,        -- ID Qobuz numérique (ex. "525535")
    similar_artists TEXT,          -- JSON : [{"name": ..., "id": ..., "rank": ...}]
    portrait TEXT,                 -- biographie / présentation Qobuz
    tags TEXT DEFAULT '[]',        -- non exposé côté Qobuz, gardé pour symétrie
    status TEXT DEFAULT 'success'
);
```

### Output (dérivé)

**`data/Artistes_Similaires_Qobuz/output_related.csv`** — généré par
`export_to_csv.py` quand on en a besoin pour inspection humaine.

---

## Algorithme de matching strict

> ⚠️ **Coquille corrigée** — la fonction `check_artist_presence` du scraper
> existant (`A_Recuperer/utils/scraper.py`) acceptait un match dès **1 token**
> commun, ce qui causait des confusions ("Worakls" matchait "Kevin Worakls",
> par exemple). Le service Qobuz utilise un matching plus strict.

1. Recherche : `https://www.qobuz.com/fr-fr/search/artists/{nom}`
2. Récupération de tous les liens `/interpreter/{slug}/{id}`
3. Pour chaque candidat, calcul de la similarité de nom :
   - Sur le slug normalisé (espaces, ASCII, minuscules)
   - Sur le texte affiché (titre du lien)
   - Score retenu = max des deux
4. Seuil strict `NAME_MATCH_THRESHOLD = 0.85` (vs ~0.5 dans l'ancien scraper).
   Sous le seuil → l'artiste est considéré "non trouvé".
5. Match exact (après normalisation) → score forcé à 1.0.

---

## Extraction sur la page artiste

### Portrait

Le texte complet est dans `#catalog-heading__text` (un **id**, pas une classe).
Il est tronqué visuellement par CSS via une checkbox `#expand-toggle` et un label
"Lire la suite", mais **entièrement présent dans le DOM dès le chargement**.

On utilise `text_content()` (qui ignore la visibilité) plutôt que `inner_text()`
pour récupérer le texte complet sans avoir à cliquer "Lire la suite".

### Artistes similaires

Section délimitée par `h3.catalog-heading__subtitle` contenant le texte
"Artistes similaires". On scope **strictement** au `.catalog-heading` parent
le plus proche pour éviter de capturer d'autres carrousels d'artistes
ailleurs sur la page.

Chaque item suit la structure :

```html
<a class="catalog-heading__item" href="/fr-fr/interpreter/{slug}/{id}">
    <span class="catalog-heading__name">Nom de l'artiste</span>
</a>
```

Selon la popularité de l'artiste source, Qobuz retourne entre ~5 et ~80
similaires.

---

## Anti-bot et résilience

Le scraper hérite des bonnes pratiques du service Spotify :

- **Sessions rotatives** : chaque navigateur traite 15 à 25 artistes (random)
  avant restart, pour éviter les patterns détectables
- **Délais aléatoires** : 2 à 5 secondes entre chaque artiste
- **Stealth** : `playwright-stealth` masque les signatures `navigator.webdriver`
  et autres traces d'automatisation
- **Cookie banner** : dismissé automatiquement (`#didomi-notice-agree-button`)
- **Network resilience** : détection de coupure internet et attente du
  rétablissement
- **Reprise propre** : la DB SQLite garde la liste des artistes traités, donc
  un Ctrl+C suivi d'un `uv run python main.py` reprend exactement où on s'est
  arrêté

---

## Commandes

```bash
cd sources/Artistes_Similaires_Qobuz

# Setup (une seule fois)
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium

# Lancer le scraper (reprend là où il s'est arrêté)
uv run python main.py

# Mode visible pour debug
HEADLESS=false uv run python main.py

# Exporter la DB vers le CSV (lecture humaine / sauvegarde Git-friendly)
uv run python export_to_csv.py
```

---

## Intégration au service Recommandation

Qobuz devient une **3e source de similarité** pondérée. Le score combiné
devient :

    score(c) = α_lfm × s_lastfm(c) + α_spt × s_spotify(c) + α_qbz × s_qobuz(c)

avec `α_spt = max(0, 1 − α_lfm − α_qbz)` (déduit). Côté UI Streamlit, deux
sliders : poids Last.fm et poids Qobuz ; le poids Spotify s'affiche en
"effectif".

Le portrait Qobuz est propagé sur chaque recommandation et affiché en
expander dans la carte de détails.

Le rang Qobuz est converti en score linéairement, comme Spotify mais avec
une échelle plus large (`QOBUZ_MAX_RANK = 50` car Qobuz expose plus de
similaires que Spotify) :

    qobuz_rank_to_score(rang) = max(0, 1 − (rang − 1) / 50)

---

## Limites connues

- Pas tous les artistes ont un portrait public sur `www.qobuz.com` (les bios
  sont parfois exclusives à `play.qobuz.com`). Le champ `portrait` est alors
  vide, ce qui est traité comme "pas de bio à afficher".
- Le matching à 0.85 peut rater des artistes aux noms très courts (ex. "N'to"
  vs slug "nto" → score 0.857... techniquement OK mais limite). Si beaucoup
  de matches échouent, on peut baisser le seuil à 0.80.
- Qobuz ne fournit pas de tags / genres. La diversification MMR de
  Recommandation reste basée sur les tags Last.fm.
