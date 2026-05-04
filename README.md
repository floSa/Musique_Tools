# Musique_Tools

Boîte à outils centralisée pour la gestion et la découverte musicale. Elle regroupe quatre services autour de Spotify, de la bibliothèque physique personnelle et des sources d'acquisition.

---

## Architecture

```
Musique_Tools/
│
├── data/
│   ├── Historique_Spotify/              # Exports JSON de l'historique Spotify (2012 →)
│   ├── Playlists_Spotify/               # CSV des playlists (Titres_AAAA + thématiques)
│   ├── Ressources/                      # Fichiers de référence et résultats intermédiaires
│   │   ├── artistes_liste.csv           # Généré par A_Recuperer --extract-artists (partagé)
│   │   ├── recherches_effectuees.xlsx   # Albums déjà recherchés (évite les doublons)
│   │   ├── Albums_musique_AAAA_MM.xlsx  # Mapping noms Spotify ↔ noms bibliothèque physique
│   │   ├── albums_a_rechercher.csv      # Généré par A_Recuperer --match
│   │   ├── albums_match_complet.csv     # Généré par A_Recuperer --match (avec scores fuzzy)
│   │   └── resultats_cotes.csv          # Généré par A_Recuperer --search
│   ├── Resultats/
│   │   └── resultats_final.csv          # Généré par A_Recuperer --consolidate (jeu final)
│   ├── Bibliotheque/
│   │   └── bibliotheque.csv             # Généré par A_Recuperer --scan-library
│   ├── Artistes_Similaires_LastFM/
│   │   ├── output_related.csv           # Export CSV des résultats
│   │   └── similar_artists.db           # Base SQLite (source de vérité)
│   └── Artistes_Similaires_Spotify/
│       ├── output_related.csv           # Export CSV des résultats
│       └── debug_selection.csv          # Log de sélection des artistes
│
├── sources/
│   ├── Analyse/                         # Notebooks d'analyse Spotify
│   ├── Artistes_Similaires_LastFM/      # Artistes similaires via API Last.fm
│   ├── Artistes_Similaires_Spotify/     # Artistes similaires via scraping Spotify
│   └── A_Recuperer/                     # Pipeline de recherche d'albums
│       └── utils/
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
| **A_Recuperer** | `sources/A_Recuperer/` | Identifie les albums à récupérer et les recherche sur Lyon + Qobuz |

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

# A_Recuperer
cd sources/A_Recuperer
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium
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

### Service : A_Recuperer

Pipeline en quatre étapes pour identifier les albums manquants et les localiser à la BM Lyon et sur Qobuz.

```bash
cd sources/A_Recuperer

uv run python main.py --all              # Pipeline complet

uv run python main.py --extract-artists  # Extrait les artistes uniques des playlists → artistes_liste.csv
uv run python main.py --scan-library     # Scan M:\musiques\__Autres → bibliotheque.csv
uv run python main.py --match            # Matching playlists vs bibliothèque → albums_a_rechercher.csv
uv run python main.py --search           # Scraper Lyon + Qobuz → resultats_cotes.csv
uv run python main.py --consolidate      # Fusionne tout → data/Resultats/resultats_final.csv
```

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
| `Albums_musique_AAAA_MM.xlsx` | Mapping noms Spotify ↔ noms physiques | Manuellement (mensuel) |
| `albums_a_rechercher.csv` | Albums à scraper | `A_Recuperer --match` |
| `albums_match_complet.csv` | Idem + scores fuzzy | `A_Recuperer --match` |
| `resultats_cotes.csv` | Résultats bruts Lyon + Qobuz | `A_Recuperer --search` |
| `data/Resultats/resultats_final.csv` | Jeu de données final consolidé | `A_Recuperer --consolidate` |

### Bibliothèque physique

Accessible depuis WSL via `/mnt/m/musiques/__Autres`. Structure : `__Autres/Artiste/Album/`.

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
        └──► Artistes_Similaires_Spotify (scraping Spotify)
                    │
                    ▼
             output_related.csv (rang + ID Spotify)
```

---

## Variables d'environnement

| Variable | Service | Description | Défaut |
|---|---|---|---|
| `LASTFM_API_KEY` | Artistes_Similaires_LastFM | Clé API Last.fm | — |
| `LIBRARY_PATH` | A_Recuperer | Chemin WSL vers la bibliothèque physique | `/mnt/m/musiques/__Autres` |
