# Service : Artistes_Similaires_LastFM

Scrape l'API Last.fm pour constituer une base de données d'artistes similaires et de genres musicaux.

---

## Objectif

Pour chaque artiste d'une liste source, récupérer via l'API Last.fm :
- Jusqu'à **20 artistes similaires** avec leur score de similarité (0.0 → 1.0)
- Jusqu'à **5 genres** (tags Last.fm)

Les résultats sont stockés dans une base SQLite avec reprise automatique en cas d'interruption.

---

## Architecture des fichiers

```
sources/Artistes_Similaires_LastFM/
├── main.py                  # Scraper principal (boucle sur data/Ressources/artistes_liste.csv)
├── api_client.py            # Client API Last.fm
├── database.py              # Couche SQLite
├── check_missing.py         # Étape 1 du pipeline : identifie les artistes manquants
├── rescue_missing.py        # Étape 2 : tente de retrouver les artistes non trouvés
├── export_to_csv.py         # Étape 3 : exporte SQLite → CSV
├── import_csv_to_sqlite.py  # One-shot : importe un CSV existant dans la DB
├── run_pipeline.sh          # Lance les 3 étapes en séquence
└── requirements.txt
```

---

## Données

### Input

**`data/Ressources/artistes_liste.csv`** (partagé entre tous les services)

CSV avec une colonne `Artist`. Généré automatiquement par `A_Recuperer --extract-artists` à partir des playlists. Peut aussi être complété manuellement.

### Output

**`data/Artistes_Similaires_LastFM/similar_artists.db`** (source de vérité)

Base SQLite avec une table `artists` :

| Colonne | Type | Description |
|---|---|---|
| `source_artist` | TEXT PK | Nom de l'artiste source |
| `similar_artists` | TEXT | JSON : liste de `{name, mbid, match, rank}` |
| `tags` | TEXT | JSON : liste de noms de genres |
| `status` | TEXT | `success` |

**`data/Artistes_Similaires_LastFM/output_related.csv`**

Export plat de la base SQLite :

| Colonne | Description |
|---|---|
| `Source_Artist` | Artiste source |
| `Related_Data_Raw` | Liste des artistes similaires avec scores |

---

## Commandes

```bash
cd sources/Artistes_Similaires_LastFM

# Scraper principal (reprend là où il s'est arrêté)
uv run python main.py

# Pipeline complet : vérification + rescue + export CSV
bash run_pipeline.sh

# Étapes séparées
uv run python check_missing.py    # → data/Artistes_Similaires_LastFM/artists_with_no_results.txt
uv run python rescue_missing.py   # Tente de retrouver les artistes manquants
uv run python export_to_csv.py    # → data/Artistes_Similaires_LastFM/output_related.csv
```

**Comportement du scraper :**
- Saute les artistes déjà en base (`status = 'success'` avec résultats non vides)
- Pause 0.5 s entre chaque artiste (respect du rate limit Last.fm ~5 req/s)
- Pause 5 min après 3 erreurs consécutives
- Interruptible avec Ctrl+C, reprend proprement

---

## API Last.fm

| Méthode | Paramètre | Description |
|---|---|---|
| `artist.getsimilar` | `artist`, `limit=20`, `autocorrect=1` | Artistes similaires avec scores 0–1 |
| `artist.gettoptags` | `artist`, `autocorrect=1` | Top 5 genres |
| `artist.search` | `artist`, `limit=5` | Recherche par nom (pour rescue) |

**Authentification :** `LASTFM_API_KEY` dans `.env`

---

## Installation

```bash
cd sources/Artistes_Similaires_LastFM
uv venv .venv --python 3.12
uv pip install -r requirements.txt
```

Obtenir une clé API : https://www.last.fm/api/account/create

---

## Ajouter des artistes

Ajouter les noms dans `data/Ressources/artistes_liste.csv` (colonne `Artist`), puis relancer `main.py`. Les nouveaux artistes sont traités en priorité (les existants sont skippés). Pour régénérer la liste depuis les playlists : `uv run python main.py --extract-artists` depuis `sources/A_Recuperer`.
