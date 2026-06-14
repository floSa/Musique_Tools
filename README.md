# Musique_Tools

Boîte à outils centralisée pour la gestion et la découverte musicale. Elle regroupe huit services autour de Spotify, Qobuz, Last.fm, de la bibliothèque physique personnelle, des sources d'acquisition, et des bandes originales de jeux vidéo.

---

## Architecture

```
Musique_Tools/
│
├── data/
│   ├── Historique_Spotify/              # Exports JSON de l'historique Spotify (2012 →)
│   ├── Playlists_Spotify/               # CSV des playlists (Titres_AAAA + thématiques)
│   ├── Ressources/                      # Sources tenues à la main (pas générées)
│   │   ├── artistes_liste.csv           # Généré par A_Recuperer --extract-artists (partagé entre services)
│   │   ├── recherches_effectuees.xlsx   # Tenu à la main : albums déjà cherchés (filtre du pipeline)
│   │   └── recherches_effectuees.csv    # Export CSV de l'xlsx
│   ├── Pipeline/                        # Fichiers intermédiaires du pipeline A_Recuperer (regénérables)
│   │   ├── albums_a_rechercher.csv      # Généré par --match  (input pour --search)
│   │   ├── albums_match_complet.csv     # Généré par --match  (input pour --consolidate, avec scores fuzzy)
│   │   ├── resultats_cotes.csv          # Généré par --search (input pour --consolidate)
│   │   └── debug_selection.csv          # Log des sélections du scraper (audit)
│   ├── Resultats/                       # Sortie finale pour consultation
│   │   ├── resultats_final.csv          # Généré par A_Recuperer --consolidate
│   │   └── resultats_final.xlsx         # Excel équivalent
│   ├── Bibliotheque/
│   │   ├── bibliotheque.csv             # Généré par A_Recuperer --scan-library (4 racines, colonne Path)
│   │   └── bibliotheque.xlsx            # Excel équivalent
│   ├── Artistes_Similaires_LastFM/
│   │   ├── similar_artists.db           # Base SQLite (source de vérité)
│   │   └── output_related.csv           # Export CSV (dérivé)
│   ├── Artistes_Similaires_Spotify/
│   │   ├── similar_artists.db           # Base SQLite (source de vérité, schéma aligné Last.fm)
│   │   ├── output_related.csv           # Export CSV (dérivé, généré par export_to_csv.py)
│   │   └── debug_selection.csv          # Log de sélection des artistes
│   ├── Artistes_Similaires_Qobuz/
│   │   ├── similar_artists.db           # Base SQLite (similaires + portrait/bio Qobuz)
│   │   └── output_related.csv           # Export CSV (dérivé)
│   ├── Musique_Jeux_Video/
│   │   └── albums_khinsider.csv         # url + DL (booléen) — source de vérité
│   │                                    # Audio téléchargé hors du repo (KHINSIDER_OUTPUT)
│   └── Priorisation/
│       ├── exclusion.xlsx               # Tenu à la main : albums déjà récupérés (filtre)
│       └── priorites_artistes.xlsx      # Généré par Priorisation (export Streamlit)
│
├── sources/
│   ├── Analyse/                         # Notebooks d'analyse Spotify
│   ├── Artistes_Similaires_LastFM/      # Artistes similaires via API Last.fm (SQLite)
│   ├── Artistes_Similaires_Spotify/     # Artistes similaires via scraping Spotify (SQLite)
│   ├── Artistes_Similaires_Qobuz/       # Artistes similaires + portrait via Qobuz (SQLite)
│   ├── A_Recuperer/                     # Pipeline de recherche d'albums
│   │   └── utils/
│   ├── Musique_Jeux_Video/              # Scraper khinsider — OST de jeux vidéo
│   ├── Recommandation/                  # Interface Streamlit de découverte
│   └── Priorisation/                    # Interface Streamlit de priorisation des albums à récupérer
│
├── .env.example
└── pyproject.toml
```

---

## Services

| Service | Répertoire | Description |
|---|---|---|
| **Analyse** | `sources/Analyse/` | Notebooks d'analyse de l'historique et des playlists Spotify |
| **Artistes_Similaires_LastFM** | `sources/Artistes_Similaires_LastFM/` | Artistes similaires + genres via API Last.fm (score 0–1) |
| **Artistes_Similaires_Spotify** | `sources/Artistes_Similaires_Spotify/` | Artistes similaires "Fans Also Like" via scraping Spotify (rang) |
| **Artistes_Similaires_Qobuz** | `sources/Artistes_Similaires_Qobuz/` | Artistes similaires + bio "Portrait" via scraping Qobuz (rang) |
| **A_Recuperer** | `sources/A_Recuperer/` | Identifie les albums à récupérer et les recherche sur Lyon + Qobuz |
| **Recommandation** | `sources/Recommandation/` | Interface Streamlit de découverte d'artistes (Last.fm + Spotify) |
| **Priorisation** | `sources/Priorisation/` | Interface Streamlit qui prioritise les albums à récupérer (6 signaux pondérés, top N artistes) |
| **Musique_Jeux_Video** | `sources/Musique_Jeux_Video/` | Scraper khinsider.com — bandes originales de jeux vidéo (FLAC/MP3) |

---

## Installation

### Prérequis

- WSL Ubuntu 24.04
- [`uv`](https://github.com/astral-sh/uv) — gestionnaire de paquets Python

`uv` est installé dans `~/.local/bin/`. Ouvrir un terminal WSL via **Windows Terminal → Ubuntu** (profil login) pour que le PATH soit chargé automatiquement. Si `uv` n'est pas reconnu dans le terminal courant : `source ~/.profile`.

### Variables d'environnement

```bash
cp .env.example .env
# Remplir LASTFM_API_KEY
# Vérifier LIBRARY_PATH (défaut : /mnt/m/musiques/__Autres)
```

### Créer les environnements par service

Chaque service a son propre venv Python 3.12 isolé :

```bash
# Artistes_Similaires_LastFM
cd sources/Artistes_Similaires_LastFM
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# Artistes_Similaires_Spotify
cd sources/Artistes_Similaires_Spotify
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium

# Artistes_Similaires_Qobuz
cd sources/Artistes_Similaires_Qobuz
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium

# A_Recuperer
cd sources/A_Recuperer
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium

# Recommandation
cd sources/Recommandation
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# Priorisation
cd sources/Priorisation
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# Musique_Jeux_Video
cd sources/Musique_Jeux_Video
uv venv .venv --python 3.12
uv pip install -r requirements.txt
```

---

## Utilisation

### Service : Analyse

```bash
cd sources/Analyse
jupyter notebook
```

| Notebook | Rôle |
|---|---|
| `Spotify_Analyse.ipynb` | Analyse des playlists : BPM, énergie, popularité, genres |
| `Spotify_Histo.ipynb` | Visualisation de l'historique d'écoute 2012 → aujourd'hui |
| `Spotify_Artistes_Similaire.ipynb` | Nettoyage et analyse des données d'artistes similaires |

> `Spotify_Histo.ipynb` contient encore d'anciens chemins à mettre à jour manuellement (chemins `horellou.florian`).

---

### Service : Artistes_Similaires_LastFM

Artistes similaires et genres via l'API Last.fm. Score de similarité 0–1. Stockage SQLite avec reprise automatique.

```bash
cd sources/Artistes_Similaires_LastFM

uv run python main.py          # Scraper (reprend là où il s'est arrêté)
bash run_pipeline.sh           # Pipeline complet : check + rescue + export CSV
```

Commandes individuelles :

```bash
uv run python check_missing.py    # → artists_with_no_results.txt
uv run python rescue_missing.py   # Tente de retrouver les artistes manquants
uv run python export_to_csv.py    # → data/Artistes_Similaires_LastFM/output_related.csv
```

---

### Service : Artistes_Similaires_Spotify

Artistes similaires "Fans Also Like" via scraping Spotify. Classés par rang. Enrichis du genre et des auditeurs mensuels.

```bash
cd sources/Artistes_Similaires_Spotify

uv run python main.py              # Scraper (reprend là où il s'est arrêté)
HEADLESS=false uv run python main.py  # Mode visible pour débogage
```

---

### Service : Artistes_Similaires_Qobuz

Artistes similaires + biographie "Portrait" via scraping `www.qobuz.com`
(pages publiques, pas besoin de compte). Classés par rang. Le matching nom→artiste
utilise un fuzzy strict (seuil 0.85) pour éviter les confusions sur les homonymes.

```bash
cd sources/Artistes_Similaires_Qobuz

uv run python main.py              # Scraper (reprend là où il s'est arrêté)
HEADLESS=false uv run python main.py  # Mode visible pour débogage
uv run python export_to_csv.py     # Exporter la DB en CSV (dérivé)
```

Voir [documentation/Artistes_Similaires_Qobuz.md](documentation/Artistes_Similaires_Qobuz.md).

---

### Service : A_Recuperer

Pipeline en quatre étapes pour identifier les albums manquants et les localiser à la BM Lyon et sur Qobuz.

```bash
cd sources/A_Recuperer

uv run python main.py --all              # Pipeline complet

uv run python main.py --extract-artists  # Extrait les artistes uniques des playlists → artistes_liste.csv
uv run python main.py --scan-library     # Scan des 4 racines (__Autres, __B.O, __COMPILS, __JEUX) → bibliotheque.csv + .xlsx
uv run python main.py --match            # Matching playlists vs bibliothèque → albums_a_rechercher.csv
uv run python main.py --search           # Scraper Lyon + Qobuz → resultats_cotes.csv
uv run python main.py --consolidate      # Fusionne tout → data/Resultats/resultats_final.csv + .xlsx
```

Restreindre le pipeline à **une seule playlist** via la variable d'env `PLAYLIST_FILTER` (les fichiers de sortie sont alors suffixés) :

```bash
PLAYLIST_FILTER=Partage uv run python main.py --match
PLAYLIST_FILTER=Partage uv run python main.py --search
PLAYLIST_FILTER=Partage uv run python main.py --consolidate
# → data/Resultats/resultats_final_Partage.xlsx
```

---

### Service : Recommandation

Interface Streamlit de découverte d'artistes : exclut ce que tu possèdes déjà (biblio + playlists), agrège les similaires Last.fm + Spotify, pondère par l'historique d'écoute. Voir [documentation/Recommandation.md](documentation/Recommandation.md) pour le détail des choix d'algorithme.

```bash
cd sources/Recommandation
uv run streamlit run app.py
```

L'interface s'ouvre sur [http://localhost:8509](http://localhost:8509).

---

### Service : Priorisation

Interface Streamlit qui consomme les `data/Resultats/resultats_final_*.xlsx`
et liste les **top N artistes prioritaires à récupérer**, agrégés sur 6 signaux
pondérés (score de Recommandation dominant, multi-playlist, dispo BM Lyon,
nb d'albums possédés, nb d'albums à récupérer, écoute). Voir
[documentation/Priorisation.md](documentation/Priorisation.md).

```bash
cd sources/Priorisation
uv run streamlit run app.py
```

L'interface s'ouvre sur [http://localhost:8509](http://localhost:8509).

---

### Service : Musique_Jeux_Video

Scraper [khinsider.com](https://downloads.khinsider.com/) pour télécharger des
bandes originales de jeux vidéo. Privilégie le FLAC. Suivi de l'état via la
colonne `DL` (booléen) du CSV d'input — source de vérité unique. Audio stocké
**hors du repo** (par défaut `~/mes_projets/Musique_Jeux_Video/datas/`,
configurable via `KHINSIDER_OUTPUT`).

```bash
cd sources/Musique_Jeux_Video

# Ajouter une URL avec DL=False dans data/Musique_Jeux_Video/albums_khinsider.csv
uv run python main.py
# Le service met à jour DL=True après chaque album téléchargé avec succès
```

Voir [documentation/Musique_Jeux_Video.md](documentation/Musique_Jeux_Video.md).

---

## Données

### Playlists Spotify (`data/Playlists_Spotify/`)

- **Playlists fixes** : `Titres_AAAA.csv` — mes 50 titres de l'année (immuables)
- **Playlists thématiques** : `La_French.csv`, `Zen.csv`, `Partage.csv` — importées manuellement depuis chosic.com

### Historique Spotify (`data/Historique_Spotify/`)

18 fichiers JSON couvrant 2012 → 2026. Format : `[{endTime, artistName, trackName, msPlayed}, ...]`

### Ressources (`data/Ressources/`)

| Fichier | Rôle | Mis à jour par |
|---|---|---|
| `recherches_effectuees.xlsx` | Albums déjà recherchés | Manuellement |
| `data/Pipeline/albums_a_rechercher.csv` | Albums à scraper | `A_Recuperer --match` |
| `data/Pipeline/albums_match_complet.csv` | Idem + scores fuzzy | `A_Recuperer --match` |
| `data/Pipeline/resultats_cotes.csv` | Résultats bruts Lyon + Qobuz | `A_Recuperer --search` |
| `data/Resultats/resultats_final.csv` (+ `.xlsx`) | Jeu de données final consolidé | `A_Recuperer --consolidate` |

### Bibliothèque physique

Accessible depuis WSL via `/mnt/m/musiques/`. Le scan combine **4 racines** avec des conventions différentes :

| Racine | Structure | Mapping |
|---|---|---|
| `__Autres`  | `Artiste/Album/`       | tel quel |
| `__B.O`     | `"Album - Artiste"/` ou autre | split au dernier `-` si présent ; sinon `Artist="BO"`, `Album=nom_dossier` |
| `__COMPILS` | `Album/`               | Artist forcé à `"Various Artists"` |
| `__JEUX`    | `Album/`               | Artist forcé à `"BO Jeux"` |

Le résultat est un seul `bibliotheque.csv` (+ `.xlsx`) avec colonnes `Artist, Album, Path` (chemin physique du dossier album, propagé dans le fichier final via `Path_Possede`).

> Le lecteur M: doit être monté dans WSL avant de lancer `--scan-library`. Mount manuel (temporaire) :
> ```bash
> sudo mkdir -p /mnt/m && sudo mount -t drvfs M: /mnt/m
> ```
> Pour un mount permanent, voir `documentation/A_Recuperer.md`.

---

## Flux de données

```
Playlists Spotify (CSV — import manuel depuis chosic.com)
        │
        ▼
  A_Recuperer --match
        │
        ├──► déjà en bibliothèque physique → ignoré
        ├──► déjà dans recherches_effectuees → ignoré
        └──► albums_a_rechercher.csv
                    │
                    ▼
          A_Recuperer --search (Playwright)
                    │
                    ├──► BM Lyon Part-Dieu → Cote + Disponibilité
                    └──► Qobuz → URL
                                │
                                ▼
                        A_Recuperer --consolidate
                                │
                                ▼
                        data/Resultats/resultats_final.csv


A_Recuperer --extract-artists
        │
        ▼
data/Ressources/artistes_liste.csv
        │
        ├──► Artistes_Similaires_LastFM (API Last.fm)
        │           │
        │           ▼
        │    similar_artists.db → output_related.csv
        │
        ├──► Artistes_Similaires_Spotify (scraping Spotify)
        │           │
        │           ▼
        │    similar_artists.db → output_related.csv (export dérivé)
        │
        └──► Artistes_Similaires_Qobuz (scraping www.qobuz.com)
                    │
                    ▼
             similar_artists.db (similaires + portrait/bio)


Albums khinsider (URLs dans albums_khinsider.csv)
        │
        ▼
  Musique_Jeux_Video (requests + BeautifulSoup)
        │
        ▼
  data/Musique_Jeux_Video/<Album>/*.flac (ou *.mp3)
```

---

## Variables d'environnement

| Variable | Service | Description | Défaut |
|---|---|---|---|
| `LASTFM_API_KEY` | Artistes_Similaires_LastFM | Clé API Last.fm | — |
| `LIBRARY_PATH` | A_Recuperer | Chemin WSL vers la bibliothèque physique | `/mnt/m/musiques/__Autres` |
| `KHINSIDER_OUTPUT` | Musique_Jeux_Video | Dossier de sortie pour les OST téléchargées (audio volumineux, hors repo) | `~/mes_projets/Musique_Jeux_Video/datas` |
