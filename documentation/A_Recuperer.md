# Service : A_Recuperer

Pipeline de bout en bout pour identifier les albums présents dans les playlists Spotify mais absents de la bibliothèque physique, puis les localiser à la Bibliothèque Municipale de Lyon (Part-Dieu) et/ou sur Qobuz.

---

## Objectif

Répondre à la question : **quels albums de mes playlists est-ce que je n'ai pas encore, et où les trouver ?**

Le service croise trois sources :
1. **Playlists Spotify** — ce que tu écoutes
2. **Bibliothèque physique** (`M:\musiques\__Autres`) — ce que tu possèdes
3. **Recherches déjà effectuées** — ce qui a déjà été cherché (évite les doublons)

Pour les albums manquants, il scrape automatiquement :
- La **BM Lyon Part-Dieu** : disponibilité et cote de rangement
- **Qobuz** : URL directe de lecture/achat

---

## Architecture des fichiers

```
sources/A_Recuperer/
├── main.py               # CLI : point d'entrée principal
├── A_Recuperer.ipynb     # Notebook interactif (même logique)
├── requirements.txt
└── utils/
    ├── matching.py       # Fuzzy matching playlist↔bibliothèque (rapidfuzz)
    ├── text_match.py     # Matching strict scraper : SequenceMatcher + parsing ISBD
    ├── library.py        # Scan de la bibliothèque physique
    ├── data_loader.py    # Chargement playlists + recherches_effectuees
    └── scraper.py        # Scraper BM Lyon + Qobuz (Playwright)
```

---

## Flux d'exécution

### Étape 1 — Scan de la bibliothèque (`--scan-library`)

`scan_all_libraries()` parcourt **4 racines** avec des conventions de nommage
différentes (cf. `utils/library.py`) :

- `M:\musiques\__Autres`  — `Artiste/Album/` (cas standard)
- `M:\musiques\__B.O`     — `"Album - Artiste"/` (split au dernier `-`)
- `M:\musiques\__COMPILS` — `Album/` → Artist forcé à `"Various Artists"`
- `M:\musiques\__JEUX`    — `Album/` → Artist forcé à `"BO Jeux"`

Le résultat fusionné est dédoublonné et sauvegardé dans
`data/Bibliotheque/bibliotheque.csv`. À relancer uniquement quand la
bibliothèque physique a changé.

---

### Étape 2 — Chargement des trois sources et matching (`--match`)

`cmd_match()` charge **trois sources** en parallèle avant de faire le matching :

**Source 1 — Playlists** (`load_playlists`)

`load_playlists()` parcourt tous les CSV de `data/Playlists_Spotify/` sans distinction. Deux types coexistent :

| Type | Exemples | Contenu |
|---|---|---|
| Annuelles | `Titres_2017.csv` … `Titres_2025.csv` | Mes 50 titres de l'année, immuables |
| Thématiques | `La_French.csv`, `Zen.csv`, `Partage.csv` | Mises à jour manuellement |

Chaque fichier reçoit une colonne `Playlist` = nom sans extension, puis tous sont concaténés en un seul DataFrame.

**Source 2 — Bibliothèque physique**

`pd.read_csv(data/Bibliotheque/bibliotheque.csv)` — le CSV généré par `--scan-library`. Contient tous les albums physiquement possédés.

**Source 3 — Recherches déjà effectuées** (`load_recherches_effectuees`)

`load_recherches_effectuees(data/Ressources/recherches_effectuees.xlsx)` — fichier tenu à jour manuellement. Liste les albums déjà scrappés lors des runs précédents pour ne pas les relancer.

---

**Normalisation et matching**

```
Source 1 : df_playlists
  │  dédoublonnage sur (Artist, Album)
  │  clean_artist() + clean_albums()
  │    → minuscules, sans accents, sans parenthèses
  │    → suppression de "Deluxe", "Radio Edit", "EP"
  │    → split sur la virgule (prend le premier artiste)
  │
Source 2 : df_biblio = bibliotheque.csv ∪ Albums_musique_AAAA_MM.xlsx (le plus récent)
  │  ⚠ le xlsx est tenu à la main et liste les albums "considérés possédés"
  │    plus largement que le scan disque (acquisitions hors filesystem,
  │    fichiers vrac sans dossier album, etc.). 858 entrées additionnelles
  │    en pratique (mai 2026).
  │  même normalisation appliquée
  │
  ▼
match_albums_with_fuzz()   (rapidfuzz · token_sort_ratio)
    fuzzy scoring SUR les colonnes Artist_clean/Album_clean
    (préserve les noms bruts en sortie pour le scraper et l'affichage)
    seuil artiste : 90
    seuil album   : 80
  │
  ├── Album_sim ≥ 80         → déjà possédé en bibliothèque, ignoré
  │
  ├── dans df_recherches     → déjà scrappé (Source 3), ignoré
  │   (jointure normalisée via clean_artist/clean_albums des 2 côtés —
  │    le xlsx étant tenu à la main avec des noms déjà cleaned)
  │
  └── reste → data/Ressources/albums_a_rechercher.csv
```

**Mesure indicative sur la playlist Partage** (avril 2026, biblio ~11k albums) :

| Étape | Albums restants |
|---|---|
| Avant filtrage | 3 136 |
| Après matching biblio (fuzzy ≥80) | ~2 400 |
| Après élargissement via Albums_musique_2026_05.xlsx | ~614 |
| Après filtre `recherches_effectuees.xlsx` | **40** |

---

### Étape 3 — Scraping (`--search`)

```
albums_a_rechercher.csv
        │
        ▼ run_scraper()  (Playwright · Chromium headless · locale fr-FR)
        │
        ├── catalogue.bm-lyon.fr
        │     recherche : "{Artist} {Album} Disque compact"
        │     vérifie l'artiste sur la page de détail
        │     cherche "Part-Dieu" → extrait Cote + Disponibilité
        │
        └── qobuz.com
              stratégie 1 : artiste → page artiste → parcours discographie (≤ 50 titres)
              stratégie 2 (fallback) : recherche directe "{Artist} {Album}"
              → URL play.qobuz.com/album/...
                          │
                          ▼
              data/Ressources/resultats_cotes.csv
              (reprise automatique si le fichier existe déjà)
```

---

## Commandes CLI

```bash
cd sources/A_Recuperer

# Pipeline complet (recommandé)
uv run python main.py --all

# Étapes séparées
uv run python main.py --scan-library  # Génère data/Bibliotheque/bibliotheque.csv
uv run python main.py --match         # Génère albums_a_rechercher.csv + albums_match_complet.csv
uv run python main.py --search        # Génère data/Ressources/resultats_cotes.csv
uv run python main.py --consolidate   # Génère data/Resultats/resultats_final.csv
```

**Quand utiliser chaque étape séparément :**
- `--scan-library` : uniquement quand la bibliothèque physique a changé (nouveaux albums ajoutés)
- `--match` : après ajout de nouvelles playlists ou mise à jour de `recherches_effectuees.xlsx`
- `--search` : pour scraper les albums déjà identifiés sans relancer tout le matching
- `--consolidate` : pour regénérer le fichier final sans relancer le scraping

### Restreindre à une seule playlist (`PLAYLIST_FILTER`)

Par défaut, `--match` charge **toutes** les playlists de `data/Playlists_Spotify/`
et les concatène. Pour ne traiter qu'**une seule** playlist (ex: chercher
uniquement ce qu'il manque dans `Partage`), définir la variable d'environnement
`PLAYLIST_FILTER` avec le nom du fichier (sans extension) :

```bash
# Tout le pipeline restreint à la playlist Partage
PLAYLIST_FILTER=Partage uv run python main.py --match
PLAYLIST_FILTER=Partage uv run python main.py --search
PLAYLIST_FILTER=Partage uv run python main.py --consolidate

# Run global par défaut (toutes les playlists)
uv run python main.py --match
```

Les fichiers générés sont alors **suffixés** avec le nom de la playlist
pour ne pas écraser le run global :

| Fichier global | Avec `PLAYLIST_FILTER=Partage` |
|---|---|
| `albums_a_rechercher.csv`  | `albums_a_rechercher_Partage.csv` |
| `albums_match_complet.csv` | `albums_match_complet_Partage.csv` |
| `resultats_cotes.csv`      | `resultats_cotes_Partage.csv` |
| `resultats_final.csv`      | `resultats_final_Partage.csv` |

Si le nom passé n'existe pas dans le dossier des playlists, le script
affiche la liste des playlists disponibles et s'arrête sans rien écrire.
La variable doit être définie pour **chaque** invocation de la pipeline
(`--match`, `--search`, `--consolidate`) — sinon les paths ne correspondent
plus.

`uv run` utilise automatiquement le venv `.venv/` présent dans le répertoire courant. Pas besoin d'activer manuellement.

---

### Consulter sa bibliothèque physique

**Rafraîchir le scan** (à relancer quand tu ajoutes des albums dans `M:\musiques\__Autres`) :

```bash
cd sources/A_Recuperer
uv run python main.py --scan-library
# → data/Bibliotheque/bibliotheque.csv  (~3240 artistes)
```

**Rechercher un artiste ou un album dans le CSV généré :**

```bash
# Tous les albums d'un artiste
grep -i "air" ../../data/Bibliotheque/bibliotheque.csv

# Compter le nombre d'albums
wc -l ../../data/Bibliotheque/bibliotheque.csv
```

**Ou depuis Python / notebook :**

```python
import pandas as pd
df = pd.read_csv("../../data/Bibliotheque/bibliotheque.csv")

# Chercher un artiste
df[df["Artist"].str.contains("Air", case=False)]

# Nombre d'albums par artiste (top 10)
df.groupby("Artist").size().sort_values(ascending=False).head(10)
```

---

## Modules `utils/`

Ces fonctions sont conçues pour être importées dans les notebooks autant que dans le CLI.

### `utils/matching.py`

| Fonction | Signature | Description |
|---|---|---|
| `clean_artist(text)` | `str → str` | Normalise un nom d'artiste : supprime parenthèses, accents, met en minuscules |
| `clean_albums(text)` | `str → str` | Normalise un nom d'album : supprime crochets, "Deluxe", "Radio Edit", "EP", accents |
| `match_albums_with_fuzz(df_a, df_b, name_a, name_b, threshold)` | `DataFrame, DataFrame, str, str, int → DataFrame` | Fuzzy matching artiste+album entre deux DataFrames |
| `get_percentage(count, total)` | `int, int → int` | Calcul de pourcentage tronqué |

**Détail du matching :**

1. Pour chaque ligne de `df_a`, cherche le meilleur artiste dans `df_b` avec `fuzz.token_sort_ratio`
2. Si `Artist_sim ≥ threshold` (défaut 90), filtre les albums de cet artiste et cherche le meilleur album
3. Retourne un DataFrame avec les scores de similarité pour artiste et album

**Exemple d'utilisation dans un notebook :**

```python
from utils.matching import clean_artist, clean_albums, match_albums_with_fuzz

df_playlists['Artist_clean'] = df_playlists['Artist'].apply(clean_artist)
df_biblio['Artist_clean'] = df_biblio['Artist'].apply(clean_artist)

df_match = match_albums_with_fuzz(
    df_playlists, df_biblio,
    name_tester='Playlist', name_ressource='Biblio',
    artist_similarity_threshold=90
)
# Filtrer les non-trouvés
df_a_recuperer = df_match[df_match['Album_sim'] < 80]
```

---

### `utils/library.py`

| Fonction | Signature | Description |
|---|---|---|
| `scan_library(path, output_path)` | `str, str|None → DataFrame` | Parcourt `path/Artiste/Album/` (compat historique, racine `__Autres` uniquement) |
| `scan_all_libraries(autres, bo, compils, jeux, output_path)` | tous optionnels → `DataFrame` | Scanne les 4 racines avec leurs règles spécifiques et fusionne |
| `scan_artist_album_root(path)` | `str → list[dict]` | Stratégie `Artiste/Album/` |
| `scan_bo_root(path)` | `str → list[dict]` | Stratégie `"Album - Artiste"/` (split au dernier `-`) |
| `scan_album_only_root(path, fixed_artist)` | `str, str → list[dict]` | Stratégie `Album/` avec artiste forcé |

La bibliothèque est répartie sur 4 racines avec des conventions distinctes :

| Racine Windows | Convention dossier | Mapping `(Artist, Album)` |
|---|---|---|
| `M:\musiques\__Autres`  | `Artiste/Album/`        | tel quel |
| `M:\musiques\__B.O`     | `"Album - Artiste"/` ou autre | split au **dernier** `-` si présent ; sinon `Artist="BO"`, `Album=nom_dossier` |
| `M:\musiques\__COMPILS` | `Album/`                | Artist = `"Various Artists"` |
| `M:\musiques\__JEUX`    | `Album/`                | Artist = `"BO Jeux"` |

Exemple `__B.O` :
- `1989-2024 - John Williams` → split au dernier `-` → `Album="1989-2024"`, `Artist="John Williams"`
- `Disney Best OF` (pas de `-`) → `Artist="BO"`, `Album="Disney Best OF"` (cohérent avec `__JEUX`)

Accessible depuis WSL via `/mnt/m/musiques/...`.

Les 4 chemins sont configurables via variables d'environnement :
`LIBRARY_PATH` (défaut `__Autres`), `LIBRARY_BO_PATH`, `LIBRARY_COMPILS_PATH`,
`LIBRARY_JEUX_PATH`.

> **Le lecteur M: doit être monté dans WSL avant de lancer `--scan-library`.**
> WSL n'auto-monte pas les lecteurs connectés après son démarrage (lecteurs réseau, externes, etc.).
>
> **Mount manuel (temporaire, à refaire après redémarrage WSL) :**
> ```bash
> sudo mkdir -p /mnt/m
> sudo mount -t drvfs M: /mnt/m
> ```
>
> **Mount permanent (via `/etc/fstab`) :**
> ```bash
> sudo mkdir -p /mnt/m
> echo 'M: /mnt/m drvfs defaults,uid=1000,gid=1000 0 0' | sudo tee -a /etc/fstab
> sudo mount -a
> ```
> Avec `/etc/fstab`, le mount est restauré automatiquement à chaque démarrage de WSL.

**Résultat sauvegardé dans :** `data/Bibliotheque/bibliotheque.csv`

---

### `utils/data_loader.py`

| Fonction | Signature | Description |
|---|---|---|
| `load_playlists(folder_path)` | `str|Path → DataFrame` | Charge tous les CSV d'un dossier en un seul DataFrame, avec colonne `Playlist` |
| `load_recherches_effectuees(path)` | `str|Path → DataFrame` | Charge `recherches_effectuees.xlsx`, dédoublonne sur (Artist, Album) |

---

### `utils/scraper.py`

Scraper basé sur **Playwright** (navigateur headless Chromium, locale `fr-FR`).

| Fonction | Description |
|---|---|
| `run_scraper(input_csv, output_csv)` | Point d'entrée principal : lit le CSV d'albums et écrit les résultats |
| `get_qobuz_play_url(page, artist, album)` | Cherche l'URL Qobuz pour un album |
| `get_qobuz_link_via_artist(page, artist, album)` | Stratégie 1 : artiste → discographie → album |

**Stratégie BM Lyon :**
1. Recherche `{Artiste} {Album} Disque compact` sur `catalogue.bm-lyon.fr`
2. Récolte de **tous les liens** de résultat contenant "Disque compact"
3. **Scoring strict** de chaque candidat : on parse le format ISBD
   `Titre / Auteur. - Disque compact - Année` et on calcule
   `score = 0.55·sim(album, titre) + 0.45·sim(artiste, auteur)`
4. Top-K (5) candidats triés par score → on clique dans l'ordre, on extrait
   la cote Part-Dieu sur le premier qui passe la double vérification artiste
5. Reprend là où il s'est arrêté si le fichier de sortie existe déjà

**Stratégie Qobuz :**
1. Priorité : recherche artiste → on score **tous** les candidats (vs `.first`),
   match strict ≥ 0.85 sur le nom (`SequenceMatcher`) → page artiste →
   parcours discographie (≤ 50 titres) → meilleur titre album ≥ 0.55
2. Fallback : recherche directe `{Artiste} {Album}` → on score tous les
   `div.album-item` et on garde le meilleur si `sim_artiste ≥ 0.85`

### Matching strict (`utils/text_match.py`)

Aligné sur le service Artistes_Similaires_Qobuz pour éviter les faux positifs
(coquille historique : "Worakls" matchait "Kevin Worakls", "Air" matchait
"Air Supply"). Helpers communs :

| Fonction | Rôle |
|---|---|
| `normalize(s)` | NFKD + ASCII + lowercase + whitespace compressé |
| `name_similarity(a, b)` | `SequenceMatcher.ratio()` (1.0 si égalité après normalisation) |
| `parse_bm_lyon_title(text)` | Parse le format ISBD → `{title, author, raw}` |
| `score_bm_lyon_candidate(text, artist, album)` | Score combiné album+auteur ∈ [0, 1] |

Seuils : `ARTIST_MATCH_THRESHOLD = 0.85`, `BM_CANDIDATE_THRESHOLD = 0.55`,
`QOBUZ_ALBUM_THRESHOLD = 0.55`.

### Debug log (`debug_selection.csv`)

Le scraper écrit à côté de `resultats_cotes.csv` un journal de chaque
sélection pour audit a posteriori. Colonnes : `Timestamp, Source,
Artist_input, Album_input, Selected_text, Score, URL, Status`. Permet de
détecter rapidement les faux positifs/négatifs sans relancer Playwright.

**Colonnes de sortie (`resultats_cotes.csv`) :**

| Colonne | Description |
|---|---|
| `Artist` | Artiste recherché |
| `Album` | Album recherché |
| `Status` | `Found` / `Part-Dieu Not Listed` / `Error` |
| `Cote` | Cote(s) BM Lyon (ex: `782.42 AIR`) |
| `Artiste_Bibliotheque` | Nom trouvé dans le catalogue |
| `Artiste_Qobuz` | Nom trouvé sur Qobuz |
| `Album_Qobuz` | Titre trouvé sur Qobuz |
| `Disponibilité` | Statut de disponibilité BM Lyon |
| `Qobuz_URL` | URL directe `play.qobuz.com/album/...` |

---

## Notebook interactif

`A_Recuperer.ipynb` permet d'explorer chaque étape individuellement, d'ajuster les seuils de matching, et d'inspecter les résultats intermédiaires.

```bash
cd sources/A_Recuperer
uv pip install jupyter ipykernel   # si pas encore installé
uv run jupyter notebook A_Recuperer.ipynb
```

Toutes les fonctions de `utils/` sont importées directement dans le notebook.

---

## Données de référence

### Fichiers générés automatiquement

| Fichier | Généré par | Contenu |
|---|---|---|
| `data/Bibliotheque/bibliotheque.csv` + `.xlsx` | `--scan-library` | Scan des 4 racines → `{Artist, Album, Path}` (CSV + Excel à côté) |
| `data/Ressources/albums_a_rechercher.csv` | `--match` | Albums non possédés à scraper : `{Artist, Album}` |
| `data/Ressources/albums_match_complet.csv` | `--match` | Idem + scores fuzzy + `Path_Possede` (chemin physique de l'album le plus proche, vide si Artist_sim<90) |
| `data/Ressources/resultats_cotes.csv` | `--search` | Résultats BM Lyon + Qobuz bruts |
| `data/Resultats/resultats_final.csv` + `.xlsx` | `--consolidate` | Jeu de données final consolidé (CSV + Excel à côté, voir colonnes ci-dessous) |

### Colonnes de `resultats_final.csv` / `.xlsx`

| Colonne | Description |
|---|---|
| `Sources` | Qobuz URL et/ou cote BM Lyon + disponibilité, séparés par ` \| ` |
| `Reference` | Cote BM Lyon seule (pour localiser le CD en rayon) |
| `Path_Possede` | Chemin physique de l'album possédé (`M:\musiques\…\Artiste\Album`) si Artist_sim≥90, vide sinon |
| `Artist_A_rechercher` | Artiste tel qu'il apparaît dans la playlist |
| `Artist_Possede` | Meilleur artiste correspondant trouvé en bibliothèque |
| `Artist_sim` | Score de similarité artiste (0–100) |
| `Album_A_rechercher` | Album tel qu'il apparaît dans la playlist |
| `Album_Possede` | Meilleur album correspondant trouvé en bibliothèque |
| `Album_sim` | Score de similarité album (0–100, < 80 = non possédé) |
| `Liste_albums_pos` | Tous les albums possédés de l'artiste correspondant |

### Fichiers de référence manuels

### `data/Ressources/recherches_effectuees.xlsx`

Tenu à jour manuellement. Permet de ne pas re-scraper des albums déjà traités lors des runs précédents. Colonnes attendues : `Artist`, `Album`.

### `data/Ressources/Albums_musique_AAAA_MM.xlsx`

Fichier de correspondance entre les noms tels qu'ils apparaissent sur Spotify et les noms réels dans la bibliothèque physique (ex : un album avec un titre légèrement différent, une édition différente). Mis à jour manuellement chaque mois. Utilisé pour affiner le matching.

---

## Installation

```bash
cd sources/A_Recuperer
uv venv .venv --python 3.12
uv pip install -r requirements.txt
uv run playwright install chromium   # navigateur headless, à faire une seule fois
```
