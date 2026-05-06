"""
Moteur de recommandation d'artistes — logique pure, indépendante de Streamlit.

Vue d'ensemble
==============

L'objectif est de recommander des artistes que l'utilisateur ne possède PAS encore
(ni en bibliothèque physique, ni dans ses playlists), mais qui apparaissent
fréquemment comme similaires aux artistes qu'il connaît / écoute.

Sources de similarité
---------------------

1. **Last.fm** : score de similarité 0–1 (champ `match` de l'API `artist.getsimilar`).
   Échelle continue, basée sur les écoutes croisées des utilisateurs Last.fm.

2. **Spotify "Fans Also Like"** : rang d'apparition (1 = artiste le plus proche
   selon l'algorithme Spotify). Pas de score numérique exposé publiquement.

Construction des seeds
----------------------

Les "seeds" sont les artistes qui servent de point de départ. Deux sources :

- **Manuels** : sélection explicite par l'utilisateur (poids = 1.0)
- **Historique** : top N artistes les plus écoutés, pondéré par minutes d'écoute

La pondération de l'historique combine :

    poids = β × poids_récent + (1-β) × poids_total

où `β` est le slider "récent vs total" et la fenêtre récente est paramétrable
en mois. Les minutes sont normalisées par le max de la période.

Calcul du score
---------------

Pour chaque candidat `c` (artiste similaire à au moins un seed) :

    score_lastfm(c) = Σ_seeds [poids_seed × match_lastfm(seed, c)]
    score_spotify(c) = Σ_seeds [poids_seed × spotify_rank_to_score(rang(seed, c))]
    score(c) = α × score_lastfm(c) + (1-α) × score_spotify(c)

où `α` est le slider "Last.fm vs Spotify". Le rang Spotify est converti
linéairement : rang 1 → 1.0, rang 40 → 0.025.

Cette formule récompense naturellement les artistes "souvent cités" : si 5 seeds
pointent vers le même candidat, leurs contributions s'additionnent.

Boost historique
----------------

Les artistes déjà présents dans l'historique d'écoute (mais pas dans biblio /
playlists) sont multipliés par un facteur :

    boost = 1 + γ × min(1, minutes_écoutées / 60)

où `γ` est le slider "boost historique" (0 = désactivé). Plafonné à 60 minutes
pour éviter qu'un artiste massivement écouté domine tout.

Filtrage
--------

1. Exclusion stricte : artistes en bibliothèque physique OU dans une playlist
2. Optionnel : filtre par genre (au moins un tag Last.fm parmi ceux sélectionnés)

L'historique d'écoute n'est PAS un critère d'exclusion — c'est un signal positif
(je connais un peu, je veux creuser).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta
import json
import logging
import sqlite3
import time

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

SPOTIFY_MAX_RANK = 40       # rang max possible chez Spotify "Fans Also Like"
HISTORY_BOOST_CAP_MIN = 60  # plafond du boost historique en minutes


def compute_artist_popularity(
    lastfm_similar: dict[str, list[dict]],
    spotify_similar: dict[str, list[dict]],
) -> dict[str, int]:
    """Pour chaque artiste, combien de fois il apparaît comme similaire d'un autre.

    Cette popularité est utilisée pour dampener les candidats trop "génériques"
    (ceux qui ressortent comme similaire de presque tout le monde — Daft Punk,
    Radiohead, etc.) et laisser plus de place aux artistes plus pointus.

    Sources combinées Last.fm + Spotify : un artiste cité 50 fois en Last.fm
    et 30 fois en Spotify a une popularité de 80.
    """
    pop: dict[str, int] = {}
    for similars in lastfm_similar.values():
        for s in similars:
            name = s.get("name")
            if name:
                pop[name] = pop.get(name, 0) + 1
    for similars in spotify_similar.values():
        for s in similars:
            name = s.get("name")
            if name:
                pop[name] = pop.get(name, 0) + 1
    return pop


def popularity_penalty_factor(popularity: int, omega: float) -> float:
    """Facteur ∈ (0, 1] qui dampe les candidats populaires.

        factor = 1 / (1 + ω × log(1 + popularity))

    - ω = 0 → factor = 1 (pas de pénalité)
    - ω élevé + popularity élevée → factor → 0 (forte pénalité)

    Utilise log pour que la pénalité augmente vite au début (1 → 50 citations)
    puis s'aplatisse (les ultra-populaires ne sont pas écrasés à zéro).
    """
    if omega <= 0:
        return 1.0
    import math
    return 1.0 / (1.0 + omega * math.log(1 + popularity))


# ---------------------------------------------------------------------------
# Modèles
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    artist: str
    score: float
    citations: int                  # nombre de seeds distincts qui le citent
    lastfm_score: float             # somme pondérée des scores Last.fm
    spotify_score: float            # somme pondérée des scores Spotify
    in_history: bool                # True si déjà écouté
    history_minutes: float          # minutes totales écoutées
    tags: list[str]                 # genres Last.fm
    citing_seeds: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_artist(name: str) -> str:
    """Normalisation pour comparaison (insensible à la casse, sans espaces autour)."""
    return name.strip().lower()


def spotify_rank_to_score(rank: int) -> float:
    """Convertit un rang Spotify en score 0–1.

    Linéaire : rang 1 → 1.0, rang 40 → 0.025, rang > 40 → 0.
    """
    return max(0.0, 1.0 - (rank - 1) / SPOTIFY_MAX_RANK)


# ---------------------------------------------------------------------------
# Chargement des données
# ---------------------------------------------------------------------------

def load_history(history_dir: Path) -> pd.DataFrame:
    """Charge l'historique Spotify (JSON exports) en DataFrame.

    Colonnes : `artist`, `ts` (datetime), `ms_played` (int).

    Les exports Spotify utilisent deux formats de clés selon l'année :
    - Ancien : `artistName`, `endTime`, `msPlayed`
    - Nouveau (Extended Streaming History) : `master_metadata_album_artist_name`,
      `ts`, `ms_played`
    Les deux sont gérés.
    """
    rows = []
    for f in sorted(history_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        for item in data:
            artist = (
                item.get('artistName')
                or item.get('master_metadata_album_artist_name')
            )
            ts_raw = item.get('endTime') or item.get('ts')
            ms = item.get('msPlayed') or item.get('ms_played') or 0
            if artist and ts_raw:
                rows.append({'artist': artist, 'ts': ts_raw, 'ms_played': ms})

    if not rows:
        return pd.DataFrame(columns=['artist', 'ts', 'ms_played'])

    df = pd.DataFrame(rows)
    df['ts'] = pd.to_datetime(df['ts'], utc=True, errors='coerce')
    df = df.dropna(subset=['ts'])
    return df


def history_weights(
    df_history: pd.DataFrame,
    recent_months: int,
    recent_weight: float,
) -> dict[str, float]:
    """Pondération combinée récent / total des artistes de l'historique.

    Args:
        df_history : DataFrame issu de `load_history`
        recent_months : fenêtre "récent" en mois
        recent_weight : β ∈ [0,1] — 0 = total uniquement, 1 = récent uniquement

    Returns:
        {artist: poids ∈ [0,1]} où le poids est normalisé par le max de chaque période.
    """
    if df_history.empty:
        return {}

    cutoff = pd.Timestamp.now(tz='UTC') - timedelta(days=int(recent_months * 30))
    df_recent = df_history[df_history['ts'] >= cutoff]

    total_min = df_history.groupby('artist')['ms_played'].sum() / 60000
    if total_min.empty:
        return {}
    total_norm = total_min / total_min.max()

    if not df_recent.empty:
        recent_min = df_recent.groupby('artist')['ms_played'].sum() / 60000
        recent_norm = recent_min / recent_min.max()
    else:
        recent_norm = pd.Series(dtype=float)

    weights = {}
    for artist in total_norm.index:
        w_total = float(total_norm.get(artist, 0))
        w_recent = float(recent_norm.get(artist, 0))
        weights[artist] = recent_weight * w_recent + (1 - recent_weight) * w_total
    return weights


def history_minutes(df_history: pd.DataFrame) -> dict[str, float]:
    """Total en minutes par artiste (toutes périodes confondues)."""
    if df_history.empty:
        return {}
    return (df_history.groupby('artist')['ms_played'].sum() / 60000).to_dict()


def load_lastfm_similar(db_path: Path) -> dict[str, list[dict]]:
    """Retourne {artist: [{name, match: float, rank: int}, ...]} depuis la DB SQLite."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT source_artist, similar_artists FROM artists WHERE status='success'"
    )
    result = {}
    for src, sim_json in cur.fetchall():
        try:
            sim = json.loads(sim_json) if sim_json else []
            result[src] = [
                {
                    'name': s['name'],
                    'match': float(s.get('match', 0) or 0),
                    'rank': int(s.get('rank', 999)),
                }
                for s in sim
                if isinstance(s, dict) and 'name' in s
            ]
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    conn.close()
    return result


def load_lastfm_tags(db_path: Path) -> dict[str, list[str]]:
    """Retourne {artist: [tags]} depuis la DB SQLite."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT source_artist, tags FROM artists WHERE status='success'"
    )
    result = {}
    for src, tags_json in cur.fetchall():
        try:
            tags = json.loads(tags_json) if tags_json else []
            result[src] = tags if isinstance(tags, list) else []
        except (json.JSONDecodeError, TypeError):
            result[src] = []
    conn.close()
    return result


def load_spotify_id_index(db_path: Path) -> dict[str, str]:
    """Construit un index `{nom_artiste: spotify_id}` depuis la DB Spotify.

    Utilise :
    - `source_artist` + `source_artist_id` (artistes scrapés directement)
    - Les `{name, id}` à l'intérieur de `similar_artists` (qui apportent
      souvent des artistes non scrapés)

    En cas de doublons (peu probable car les IDs Spotify sont uniques par artiste),
    la première occurrence rencontrée est conservée.
    """
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT source_artist, source_artist_id, similar_artists FROM artists "
        "WHERE status = 'success'"
    )
    index: dict[str, str] = {}
    for source_artist, source_id, sim_json in cur.fetchall():
        # Source direct
        if (
            isinstance(source_artist, str)
            and isinstance(source_id, str)
            and len(source_id) == 22
        ):
            index.setdefault(source_artist, source_id)

        # Related (souvent des artistes non scrapés)
        try:
            related = json.loads(sim_json) if sim_json else []
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(related, list):
            continue
        for r in related:
            if isinstance(r, dict) and "name" in r and "id" in r:
                if isinstance(r["id"], str) and len(r["id"]) == 22:
                    index.setdefault(r["name"], r["id"])
    conn.close()
    return index


def load_spotify_similar(db_path: Path) -> dict[str, list[dict]]:
    """Retourne {artist: [{name, rank}, ...]} depuis la DB SQLite Spotify."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute(
        "SELECT source_artist, similar_artists FROM artists WHERE status = 'success'"
    )
    result: dict[str, list[dict]] = {}
    for src, sim_json in cur.fetchall():
        try:
            related = json.loads(sim_json) if sim_json else []
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(related, list):
            continue
        result[src] = [
            {
                "name": r["name"],
                # `rank` est stocké explicitement depuis le refactor SQLite ;
                # fallback sur l'index pour les anciennes données importées.
                "rank": int(r.get("rank", i + 1)),
            }
            for i, r in enumerate(related)
            if isinstance(r, dict) and "name" in r
        ]
    conn.close()
    return result


# ---------------------------------------------------------------------------
# Cœur du moteur
# ---------------------------------------------------------------------------

def _new_candidate_entry() -> dict:
    return {
        'lastfm_total': 0.0,
        'spotify_total': 0.0,
        'citing_seeds': set(),
    }


def _jaccard(a: set, b: set) -> float:
    """Similarité de Jaccard entre deux ensembles. Retourne 0 si l'union est vide."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def diversify_mmr(
    recs: list[Recommendation],
    diversity_weight: float,
    n: int,
    tag_sim_index: dict[str, dict[str, float]] | None = None,
) -> list[Recommendation]:
    """Re-classement façon MMR (Maximal Marginal Relevance) basé sur les tags.

    Pour chaque candidat à sélectionner :

        mmr(c) = λ × score_normalisé(c) - (1-λ) × max_j Jaccard(tags_c, tags_cj_sélectionnés)

    où `λ = 1 - diversity_weight`. Avec :
    - `diversity_weight = 0` → re-classement neutre (score pur)
    - `diversity_weight = 1` → anti-redondance pure (ignore le score)

    Args:
        recs : recommandations triées par score (DESC) déjà filtrées
        diversity_weight : ∈ [0,1] — force de la diversification
        n : taille du top à retourner

    Returns:
        Liste de `n` recommandations re-classées.
    """
    if diversity_weight <= 0 or len(recs) <= 1:
        return recs[:n]

    lam = 1 - diversity_weight
    max_score = max((r.score for r in recs), default=0)
    if max_score <= 0:
        return recs[:n]

    # Si un index de similarité de tags est fourni, on utilise le soft Jaccard
    # (similarité graduée via co-occurrence). Sinon, fallback sur Jaccard binaire.
    if tag_sim_index is not None:
        from tag_similarity import soft_jaccard

        def tag_sim(a: list[str], b: list[str]) -> float:
            return soft_jaccard(a, b, tag_sim_index)
    else:
        def tag_sim(a: list[str], b: list[str]) -> float:
            return _jaccard(
                set(t.lower() for t in a),
                set(t.lower() for t in b),
            )

    selected: list[Recommendation] = [recs[0]]
    candidates = list(recs[1:])

    while len(selected) < n and candidates:
        best_idx = 0
        best_mmr = -float("inf")
        for i, c in enumerate(candidates):
            max_sim = max(
                (tag_sim(c.tags, s.tags) for s in selected),
                default=0.0,
            )
            mmr = lam * (c.score / max_score) - (1 - lam) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = i
        selected.append(candidates.pop(best_idx))

    return selected


def recommend(
    seeds: dict[str, float],
    lastfm_similar: dict[str, list[dict]],
    spotify_similar: dict[str, list[dict]],
    lastfm_tags: dict[str, list[str]],
    excluded: set[str],
    history_minutes_map: dict[str, float],
    lastfm_weight: float,
    history_boost: float,
    genre_filter: list[str],
    n_results: int,
    diversity_weight: float = 0.0,
    popularity_penalty: float = 0.0,
    artist_popularity: dict[str, int] | None = None,
    genre_filter_mode: str = "OR",
    tag_sim_index: dict[str, dict[str, float]] | None = None,
    genre_expansion_threshold: float = 1.0,
) -> list[Recommendation]:
    """Calcule le top N des recommandations.

    Args:
        seeds : {artist: poids} — seeds manuels (poids=1) + historique (poids ∈ [0,1])
        lastfm_similar : sortie de `load_lastfm_similar`
        spotify_similar : sortie de `load_spotify_similar`
        lastfm_tags : sortie de `load_lastfm_tags`
        excluded : artistes à exclure (biblio + playlists + dislikes)
        history_minutes_map : {artist: minutes totales} pour le boost
        lastfm_weight : α ∈ [0,1] — pondération Last.fm vs Spotify dans le score final
        history_boost : γ ∈ [0,1] — force du boost pour les artistes déjà écoutés
        genre_filter : liste de genres ; un candidat passe s'il a au moins un de ces tags
        n_results : nombre de recommandations à retourner
        diversity_weight : ∈ [0,1] — force de la diversification (MMR sur tags Last.fm).
            0 = score pur (défaut), 1 = anti-redondance maximale.
        popularity_penalty : ω ≥ 0 — pénalité pour les candidats trop populaires
            (souvent cités comme similaires). 0 = pas de pénalité.
            Typique : 0.3–0.7 pour favoriser les niches sans écraser les hits.
        artist_popularity : pré-calcul de `compute_artist_popularity()`. Requis si
            `popularity_penalty > 0`.
        genre_filter_mode : "OR" (défaut, un tag suffit) ou "AND" (tous requis).
        tag_sim_index : index de co-occurrence de tags (sortie de
            `tag_similarity.build_tag_cooccurrence`). Activé pour le MMR
            (soft Jaccard) et le filtre genre étendu.
        genre_expansion_threshold : ∈ [0, 1] — seuil de proximité pour étendre
            le filtre genre. 1.0 = filtre strict (défaut), 0.3 = inclut les
            tags voisins proches (techno → minimal techno, etc.).

    Returns:
        Liste de `Recommendation` triée par MMR si diversification, sinon par score.
        Tronquée à n_results.
    """
    t0 = time.time()
    seeds_with_data = sum(
        1 for s in seeds
        if s in lastfm_similar or s in spotify_similar
    )
    logger.info(
        "recommend(): %d seeds reçus (%d avec données de similarité), "
        "α=%.2f γ=%.2f λ=%.2f ω=%.2f, n=%d, genre=%s/%s",
        len(seeds), seeds_with_data,
        lastfm_weight, history_boost, diversity_weight, popularity_penalty,
        n_results, genre_filter or [], genre_filter_mode,
    )

    # 1. Agrégation par candidat
    candidates: dict[str, dict] = {}

    for seed, seed_weight in seeds.items():
        seen_in_seed = set()

        for sim in lastfm_similar.get(seed, []):
            name = sim['name']
            entry = candidates.setdefault(name, _new_candidate_entry())
            entry['lastfm_total'] += seed_weight * sim['match']
            seen_in_seed.add(name)

        for sim in spotify_similar.get(seed, []):
            name = sim['name']
            entry = candidates.setdefault(name, _new_candidate_entry())
            entry['spotify_total'] += seed_weight * spotify_rank_to_score(sim['rank'])
            seen_in_seed.add(name)

        for name in seen_in_seed:
            candidates[name]['citing_seeds'].add(seed)

    n_before_exclusion = len(candidates)

    # 2. Filtrage exclusion (biblio + playlists)
    excluded_lower = {normalize_artist(x) for x in excluded}
    candidates = {
        a: v for a, v in candidates.items()
        if normalize_artist(a) not in excluded_lower
    }
    logger.info(
        "Candidats : %d avant exclusion → %d après (excluded=%d)",
        n_before_exclusion, len(candidates), len(excluded),
    )

    # 3. Score, boost, filtre genre
    # Normalisation : on divise par Σ poids_seed pour obtenir un score moyen par seed,
    # comparable entre runs (différents nombres de seeds, différents poids).
    seeds_weight_sum = sum(seeds.values()) or 1.0

    # Préparation du filtre genre : expansion via similarité si demandé
    if genre_filter and tag_sim_index is not None and genre_expansion_threshold < 1.0:
        from tag_similarity import expand_genre_filter
        genre_groups = [
            expand_genre_filter([g], tag_sim_index, genre_expansion_threshold)
            for g in genre_filter
        ]
    else:
        genre_groups = [{g.lower()} for g in genre_filter]

    pop_map = artist_popularity or {}
    recs = []
    for artist, agg in candidates.items():
        base_score = (
            lastfm_weight * agg['lastfm_total']
            + (1 - lastfm_weight) * agg['spotify_total']
        ) / seeds_weight_sum

        # Pénalité de popularité (#4) : dampe les artistes "génériques"
        if popularity_penalty > 0:
            pop = pop_map.get(artist, 0)
            base_score *= popularity_penalty_factor(pop, popularity_penalty)

        hist_min = history_minutes_map.get(artist, 0.0)
        if history_boost > 0 and hist_min > 0:
            boost = 1 + history_boost * min(1.0, hist_min / HISTORY_BOOST_CAP_MIN)
            score = base_score * boost
        else:
            score = base_score

        tags = lastfm_tags.get(artist, [])

        if genre_groups:
            tags_lower = {t.lower() for t in tags}
            if genre_filter_mode == "AND":
                if not all(group & tags_lower for group in genre_groups):
                    continue
            else:
                if not any(group & tags_lower for group in genre_groups):
                    continue

        recs.append(Recommendation(
            artist=artist,
            score=score,
            citations=len(agg['citing_seeds']),
            lastfm_score=agg['lastfm_total'],
            spotify_score=agg['spotify_total'],
            in_history=hist_min > 0,
            history_minutes=hist_min,
            tags=tags,
            citing_seeds=sorted(agg['citing_seeds']),
        ))

    recs.sort(key=lambda r: r.score, reverse=True)

    if genre_filter:
        logger.info("Après filtre genre (%s) : %d candidats", genre_filter_mode, len(recs))

    if diversity_weight > 0:
        pool_size = min(len(recs), max(n_results * 5, 30))
        out = diversify_mmr(
            recs[:pool_size], diversity_weight, n_results,
            tag_sim_index=tag_sim_index,
        )
        logger.info(
            "MMR appliqué (pool=%d → top %d) en %.2fs",
            pool_size, len(out), time.time() - t0,
        )
        return out

    logger.info("Top %d retourné en %.2fs", min(n_results, len(recs)), time.time() - t0)
    return recs[:n_results]
