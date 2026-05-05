"""
Index de similarité entre tags Last.fm basé sur la co-occurrence sur les artistes.

Idée : deux tags sont proches s'ils apparaissent sur largement le même ensemble
d'artistes. Aucune ontologie à maintenir — la proximité émerge des données.

Métrique :

    sim(t1, t2) = |A(t1) ∩ A(t2)| / sqrt(|A(t1)| × |A(t2)|)

C'est le **cosinus** sur les vecteurs d'incidence artiste→tag (équivalent à la
similarité d'Ochiai entre les sets d'artistes). Valeurs ∈ [0, 1] :
- 1.0 : tags présents sur exactement les mêmes artistes
- 0.0 : aucun artiste en commun
- typiquement 0.3–0.7 pour des tags vraiment proches (techno / minimal techno)

Stockage : dict imbriqué `sim[t1][t2] = cosinus`, sparse (on omet les paires à 0
et celles sous un seuil minimal pour limiter la taille).
"""
from __future__ import annotations

import math
from collections import defaultdict


# Seuil minimal de similarité pour être stocké (sous ce seuil = bruit)
MIN_SIM_THRESHOLD = 0.05
# Nombre minimum d'artistes portant un tag pour qu'il soit considéré
# (évite que des tags très rares créent des similarités à 1.0 fragiles)
MIN_TAG_OCCURRENCES = 2


def build_tag_cooccurrence(
    lastfm_tags: dict[str, list[str]],
    min_occurrences: int = MIN_TAG_OCCURRENCES,
    min_similarity: float = MIN_SIM_THRESHOLD,
) -> dict[str, dict[str, float]]:
    """Construit l'index de similarité entre tags.

    Args:
        lastfm_tags : {artist: [tags]} (sortie de `engine.load_lastfm_tags`)
        min_occurrences : ignorer les tags portés par moins de N artistes
        min_similarity : ne stocker que les paires de similarité ≥ ce seuil

    Returns:
        dict imbriqué `index[tag1][tag2] = cosinus`. Symétrique. Tags en
        lowercase. La diagonale (sim(t,t)=1) n'est PAS stockée — utiliser
        `tag_similarity()` pour le lookup qui gère ce cas.
    """
    # Index inversé : tag -> set d'artistes
    tag_to_artists: dict[str, set[str]] = defaultdict(set)
    for artist, tags in lastfm_tags.items():
        for t in tags:
            tag_to_artists[t.lower()].add(artist)

    # Filtre tags trop rares
    tag_to_artists = {
        t: artists for t, artists in tag_to_artists.items()
        if len(artists) >= min_occurrences
    }

    counts = {t: len(artists) for t, artists in tag_to_artists.items()}
    tags = list(tag_to_artists.keys())

    sim: dict[str, dict[str, float]] = defaultdict(dict)
    for i, t1 in enumerate(tags):
        artists1 = tag_to_artists[t1]
        c1 = counts[t1]
        for j in range(i + 1, len(tags)):
            t2 = tags[j]
            inter = len(artists1 & tag_to_artists[t2])
            if inter == 0:
                continue
            cos = inter / math.sqrt(c1 * counts[t2])
            if cos < min_similarity:
                continue
            sim[t1][t2] = cos
            sim[t2][t1] = cos

    return dict(sim)


def tag_similarity(t1: str, t2: str, sim_index: dict[str, dict[str, float]]) -> float:
    """Lookup symétrique de la similarité entre deux tags.

    Retourne 1.0 si les tags sont identiques (insensible casse), sinon le
    cosinus si la paire est dans l'index, sinon 0.0.
    """
    t1l = t1.lower()
    t2l = t2.lower()
    if t1l == t2l:
        return 1.0
    return sim_index.get(t1l, {}).get(t2l, 0.0)


def soft_jaccard(
    tags_a: list[str],
    tags_b: list[str],
    sim_index: dict[str, dict[str, float]],
) -> float:
    """Jaccard "souple" qui prend en compte la similarité entre tags.

    Pour chaque tag de A, on prend sa meilleure correspondance dans B (au
    moins l'identité si présent) et on moyenne. Idem dans l'autre sens, puis
    on moyenne les deux pour rester symétrique.

    Propriétés :
    - tags identiques (en set) → 1.0
    - tags disjoints sans similarité → 0.0
    - tags proches (techno / minimal techno) → 0.4–0.7
    """
    if not tags_a or not tags_b:
        return 0.0

    a_to_b = sum(
        max(tag_similarity(t1, t2, sim_index) for t2 in tags_b)
        for t1 in tags_a
    ) / len(tags_a)

    b_to_a = sum(
        max(tag_similarity(t2, t1, sim_index) for t1 in tags_a)
        for t2 in tags_b
    ) / len(tags_b)

    return (a_to_b + b_to_a) / 2


def expand_genre_filter(
    filter_tags: list[str],
    sim_index: dict[str, dict[str, float]],
    threshold: float,
) -> set[str]:
    """Étend un filtre genre en y ajoutant les tags suffisamment proches.

    Args:
        filter_tags : tags choisis par l'utilisateur
        sim_index : sortie de `build_tag_cooccurrence`
        threshold : ∈ [0, 1] — sous ce seuil, le tag voisin n'est pas inclus.
            0.0 = inclut tout (fortement permissif),
            0.5 = ne garde que les vraiment proches,
            1.0 = équivalent à pas d'expansion (seuls les tags exacts).

    Returns:
        Set de tags (lowercase) — original ∪ voisins ≥ threshold.
    """
    expanded = {t.lower() for t in filter_tags}
    if threshold >= 1.0:
        return expanded
    for t in filter_tags:
        neighbors = sim_index.get(t.lower(), {})
        for neighbor, sim in neighbors.items():
            if sim >= threshold:
                expanded.add(neighbor)
    return expanded


def top_neighbors(
    tag: str,
    sim_index: dict[str, dict[str, float]],
    k: int = 10,
) -> list[tuple[str, float]]:
    """Retourne les K tags les plus proches d'un tag donné, triés DESC.

    Utile pour le debug ("qu'est-ce qui ressemble à 'techno' selon mes data ?").
    """
    neighbors = sim_index.get(tag.lower(), {})
    return sorted(neighbors.items(), key=lambda x: x[1], reverse=True)[:k]
