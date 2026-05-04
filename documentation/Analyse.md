# Service : Analyse

Notebooks d'exploration et de visualisation des données Spotify personnelles.

---

## Objectif

Ce service ne produit pas de fichiers de sortie — c'est un espace d'exploration interactif pour comprendre ses habitudes d'écoute, analyser ses playlists et nettoyer les données d'artistes similaires.

---

## Notebooks

### `Spotify_Analyse.ipynb`

**Ce qu'il fait :**
Analyse croisée des playlists Spotify exportées en CSV. Compare les caractéristiques audio des titres (énergie, BPM, popularité, Camelot/tonalité) selon les playlists et les années.

**Données en entrée :**
- `data/Playlists_Spotify/*.csv` — toutes les playlists

**Ce qu'on peut explorer :**
- Distribution des BPM par playlist ou par année
- Popularité moyenne des titres dans chaque playlist
- Comparaison énergie / tonalité entre playlists thématiques et annuelles
- Artistes les plus représentés

---

### `Spotify_Histo.ipynb`

**Ce qu'il fait :**
Traite les exports bruts de l'historique Spotify (JSON) pour visualiser l'évolution des écoutes dans le temps.

**Données en entrée :**
- `data/Historique_Spotify/*.json` — 18 fichiers couvrant 2012 → 2026

**Ce qu'on peut explorer :**
- Temps d'écoute par mois / année
- Artistes et titres les plus écoutés sur une période
- Évolution des goûts dans le temps
- Identification des périodes d'écoute intense

> **Note :** Ce notebook contient encore d'anciens chemins en dur (utilisateur `horellou.florian`). Mettre à jour les cellules concernées pour pointer vers `../../data/Historique_Spotify/`.

---

### `Spotify_Artistes_Similaire.ipynb`

**Ce qu'il fait :**
Nettoyage et analyse du fichier `2026_02_artistes_similaires.csv` issu des données d'artistes similaires Spotify. Corrige les problèmes d'encodage (mojibake UTF-8), identifie les artistes sans ID Spotify, et analyse les scores de similarité.

**Données en entrée :**
- `data/Artistes_Similaires/2026_02_artistes_similaires.csv`
- `data/Artistes_Similaires/2026_02_debug.csv`

**Ce qu'on peut explorer :**
- Qualité des données (artistes avec ID manquant, noms mal encodés)
- Distribution des scores de similarité
- Artistes avec le plus de connexions similaires

---

## Données attendues dans les CSV de playlists

| Colonne | Description |
|---|---|
| `Song` | Titre du morceau |
| `Artist` | Artiste principal |
| `Album` | Nom de l'album |
| `BPM` | Tempo en battements par minute |
| `Camelot` | Tonalité (notation Camelot wheel, ex: `8A`) |
| `Energy` | Énergie 0–100 (Spotify audio feature) |
| `Duration` | Durée en ms |
| `Popularity` | Score de popularité Spotify 0–100 |
| `Genre` | Genre(s) associés |
| `Spotify_ID` | ID Spotify de la piste |
| `ISRC` | Code ISRC international |

---

## Comment ajouter une nouvelle playlist

1. Exporter la playlist depuis Spotify (via un outil tiers comme Exportify ou Soundiiz) au format CSV
2. Nommer le fichier selon la convention :
   - Playlist fixe (annuelle) : `Titres_AAAA.csv`
   - Playlist thématique : `NomPlaylist_JJ_MM.csv` (ex : `La_French_26_04.csv`)
3. Déposer le fichier dans `data/Playlists_Spotify/`
4. Relancer le service **A_Recuperer** pour mettre à jour le matching
