# Service : Artistes_Similaires_Spotify

Scrape la section **"Fans Also Like"** de Spotify pour constituer une base d'artistes similaires, enrichie du genre et des auditeurs mensuels.

---

## Objectif

Pour chaque artiste d'une liste source, récupérer via le web Spotify (sans API officielle) :
- La liste ordonnée des **artistes suggérés** ("Fans Also Like"), avec leur ID Spotify
- Le **rang** de chaque artiste (1 = le plus proche)
- Le **genre** (extrait du JSON-LD de la page)
- Les **auditeurs mensuels**

Contrairement au service Last.fm qui expose un score de similarité 0–1, Spotify ne fournit pas de score public : le rang d'apparition est l'indicateur de proximité.

---

## Différences avec Artistes_Similaires_LastFM

| Critère | LastFM | Spotify |
|---|---|---|
| Source | API officielle | Scraping web |
| Similarité | Score 0–1 | Rang (1 = plus proche) |
| Genres | Tags Last.fm | JSON-LD page artiste |
| Auditeurs mensuels | Non | Oui |
| ID artiste | MusicBrainz ID | Spotify ID (22 chars) |
| Vitesse | ~0.5 s/artiste | ~5–10 s/artiste |
| Robustesse | Très stable | Dépend des anti-bots Spotify |

---

## Architecture des fichiers

```
sources/Artistes_Similaires_Spotify/
├── main.py          # Scraper principal
└── requirements.txt
```

---

## Données

### Input

**`data/Ressources/artistes_liste.csv`** (partagé entre tous les services)

CSV avec une colonne `Artist`. Généré par `A_Recuperer --extract-artists` à partir des playlists.

### Output

**`data/Artistes_Similaires_Spotify/output_related.csv`**

| Colonne | Description |
|---|---|
| `Source_Artist` | Artiste source (nom recherché) |
| `Source_Artist_ID` | ID Spotify de l'artiste source |
| `Related_Data_Raw` | Liste JSON `[{name, id}, ...]` des artistes suggérés |

**`data/Artistes_Similaires_Spotify/debug_selection.csv`**

Log de sélection : pour chaque artiste cherché, indique quel candidat a été retenu, son rang dans les résultats, son score de similarité de nom et l'URL.

---

## Commandes

```bash
cd sources/Artistes_Similaires_Spotify

# Lancer le scraper (reprend là où il s'est arrêté)
uv run python main.py

# Mode visible (non headless) pour débogage
HEADLESS=false uv run python main.py
```

**Comportement :**
- Reprend automatiquement : les artistes déjà dans `output_related.csv` sont skippés
- Sessions de 10–15 artistes par instance de navigateur (rotation pour éviter la détection)
- Délai aléatoire 2–5 s entre chaque artiste
- En cas de coupure internet : pause et attente automatique du rétablissement
- Utilise `playwright-stealth` pour masquer l'automatisation

---

## Algorithme de recherche

1. Navigation vers `https://open.spotify.com/search/{artiste}/artists`
   - Le suffixe `/artists` force l'affichage de profils artistes (évite albums/chansons homonymes)
2. Scan des 30 premiers résultats :
   - Correspondance exacte (insensible à la casse) → priorité immédiate
   - Sinon : fuzzy matching (seuil 80%) → meilleur score retenu
3. Clic sur le profil sélectionné → extraction genre + auditeurs mensuels
4. Navigation vers `https://open.spotify.com/artist/{id}/related` → extraction "Fans Also Like"

---

## Installation

```bash
cd sources/Artistes_Similaires_Spotify
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium   # à faire une seule fois
```

---

## Ajouter des artistes

Ajouter les noms dans `data/Ressources/artistes_liste.csv` (colonne `Artist`), puis relancer `main.py`. Les noms déjà présents dans `output_related.csv` sont automatiquement ignorés. Pour régénérer depuis les playlists : `uv run python main.py --extract-artists` depuis `sources/A_Recuperer`.

---

## Limites connues

- Spotify peut détecter et bloquer le scraping → le script redémarre le navigateur mais peut nécessiter une surveillance manuelle sur de longues sessions
- Le genre extrait via JSON-LD n'est pas toujours présent (dépend de l'artiste)
- Les auditeurs mensuels sont une valeur visuelle, non structurée
