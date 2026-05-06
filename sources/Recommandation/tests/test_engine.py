"""
Tests unitaires du moteur de recommandation.

Lancement :
    cd sources/Recommandation
    uv run pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

from engine import (
    HISTORY_BOOST_CAP_MIN,
    Recommendation,
    SPOTIFY_MAX_RANK,
    _jaccard,
    compute_artist_popularity,
    diversify_mmr,
    history_weights,
    normalize_artist,
    popularity_penalty_factor,
    recommend,
    spotify_rank_to_score,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_normalize_artist():
    assert normalize_artist("  Daft Punk  ") == "daft punk"
    assert normalize_artist("DAFT PUNK") == "daft punk"
    assert normalize_artist("Daft Punk") == normalize_artist("daft punk")


def test_spotify_rank_to_score():
    assert spotify_rank_to_score(1) == 1.0
    assert spotify_rank_to_score(SPOTIFY_MAX_RANK) == pytest.approx(0.025, abs=1e-3)
    assert spotify_rank_to_score(SPOTIFY_MAX_RANK + 10) == 0.0
    # Strictement décroissant
    assert spotify_rank_to_score(2) < spotify_rank_to_score(1)
    assert spotify_rank_to_score(20) < spotify_rank_to_score(10)


def test_jaccard():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert _jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1 / 3)
    assert _jaccard({"a"}, {"b"}) == 0.0


# ---------------------------------------------------------------------------
# History weights
# ---------------------------------------------------------------------------

def _build_history(rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["artist", "ts", "ms_played"])
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df


def test_history_weights_empty():
    assert history_weights(pd.DataFrame(columns=["artist", "ts", "ms_played"]), 12, 0.5) == {}


def test_history_weights_recent_only():
    """Avec recent_weight=1, seuls les écoutes récentes pèsent."""
    now = pd.Timestamp.now(tz="UTC")
    df = _build_history([
        ("A", (now - pd.Timedelta(days=10)).isoformat(), 60000),  # récent
        ("B", (now - pd.Timedelta(days=400)).isoformat(), 60000),  # ancien
    ])
    w = history_weights(df, recent_months=12, recent_weight=1.0)
    # A est dans la fenêtre récente → poids 1, B est hors → poids 0
    assert w["A"] == pytest.approx(1.0)
    assert w["B"] == pytest.approx(0.0)


def test_history_weights_total_only():
    """Avec recent_weight=0, c'est l'écoute totale qui prime."""
    now = pd.Timestamp.now(tz="UTC")
    df = _build_history([
        ("A", (now - pd.Timedelta(days=10)).isoformat(), 60000),
        ("B", (now - pd.Timedelta(days=400)).isoformat(), 120000),  # 2× plus écouté au total
    ])
    w = history_weights(df, recent_months=12, recent_weight=0.0)
    # B doit avoir le poids max (1.0), A la moitié
    assert w["B"] == pytest.approx(1.0)
    assert w["A"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Diversify MMR
# ---------------------------------------------------------------------------

def _make_rec(artist: str, score: float, tags: list[str]) -> Recommendation:
    return Recommendation(
        artist=artist, score=score, citations=1,
        lastfm_score=score, spotify_score=0.0,
        in_history=False, history_minutes=0.0,
        tags=tags, citing_seeds=[],
    )


def test_diversify_zero_keeps_score_order():
    recs = [
        _make_rec("A", 1.0, ["techno"]),
        _make_rec("B", 0.9, ["techno"]),
        _make_rec("C", 0.8, ["jazz"]),
    ]
    out = diversify_mmr(recs, diversity_weight=0.0, n=3)
    assert [r.artist for r in out] == ["A", "B", "C"]


def test_diversify_full_avoids_duplicate_tags():
    """Avec diversity_weight=1, deux artistes aux mêmes tags ne devraient pas se suivre."""
    recs = [
        _make_rec("A", 1.0, ["techno"]),
        _make_rec("B", 0.95, ["techno"]),  # très similaire à A
        _make_rec("C", 0.5, ["jazz"]),     # disjoint
    ]
    out = diversify_mmr(recs, diversity_weight=1.0, n=2)
    # Le premier reste A (score max), le second doit être C (tag disjoint), pas B
    assert out[0].artist == "A"
    assert out[1].artist == "C"


# ---------------------------------------------------------------------------
# Recommend (intégration)
# ---------------------------------------------------------------------------

def _build_simple_data():
    """Petit jeu de test cohérent."""
    lastfm_sim = {
        "SeedX": [
            {"name": "AlphaArt", "match": 0.9, "rank": 1},
            {"name": "BetaArt",  "match": 0.5, "rank": 2},
        ],
        "SeedY": [
            {"name": "AlphaArt", "match": 0.8, "rank": 1},  # cité aussi par SeedY
            {"name": "GammaArt", "match": 0.3, "rank": 2},
        ],
    }
    spotify_sim = {
        "SeedX": [{"name": "AlphaArt", "rank": 1}, {"name": "DeltaArt", "rank": 2}],
        "SeedY": [],
    }
    lastfm_tags = {
        "AlphaArt": ["rock", "indie"],
        "BetaArt": ["jazz"],
        "GammaArt": [],
        "DeltaArt": ["pop"],
    }
    return lastfm_sim, spotify_sim, lastfm_tags


def test_recommend_aggregates_citations():
    """AlphaArt est cité par les deux seeds (Last.fm) ET par Spotify de SeedX → 2 citations."""
    lastfm_sim, spotify_sim, lastfm_tags = _build_simple_data()
    recs = recommend(
        seeds={"SeedX": 1.0, "SeedY": 1.0},
        lastfm_similar=lastfm_sim,
        spotify_similar=spotify_sim,
        lastfm_tags=lastfm_tags,
        excluded=set(),
        history_minutes_map={},
        lastfm_weight=1.0,  # Last.fm only pour ce test
        history_boost=0.0,
        genre_filter=[],
        n_results=10,
    )
    alpha = next(r for r in recs if r.artist == "AlphaArt")
    assert alpha.citations == 2
    # AlphaArt doit être premier (cité par les 2 seeds)
    assert recs[0].artist == "AlphaArt"


def test_recommend_excludes():
    lastfm_sim, spotify_sim, lastfm_tags = _build_simple_data()
    recs = recommend(
        seeds={"SeedX": 1.0},
        lastfm_similar=lastfm_sim,
        spotify_similar=spotify_sim,
        lastfm_tags=lastfm_tags,
        excluded={"AlphaArt"},  # exclusion de AlphaArt
        history_minutes_map={},
        lastfm_weight=1.0,
        history_boost=0.0,
        genre_filter=[],
        n_results=10,
    )
    assert all(r.artist != "AlphaArt" for r in recs)


def test_recommend_excludes_case_insensitive():
    lastfm_sim, spotify_sim, lastfm_tags = _build_simple_data()
    recs = recommend(
        seeds={"SeedX": 1.0},
        lastfm_similar=lastfm_sim,
        spotify_similar=spotify_sim,
        lastfm_tags=lastfm_tags,
        excluded={"alphaart"},  # casse différente
        history_minutes_map={},
        lastfm_weight=1.0,
        history_boost=0.0,
        genre_filter=[],
        n_results=10,
    )
    assert all(r.artist != "AlphaArt" for r in recs)


def test_recommend_genre_filter():
    lastfm_sim, spotify_sim, lastfm_tags = _build_simple_data()
    recs = recommend(
        seeds={"SeedX": 1.0, "SeedY": 1.0},
        lastfm_similar=lastfm_sim,
        spotify_similar=spotify_sim,
        lastfm_tags=lastfm_tags,
        excluded=set(),
        history_minutes_map={},
        lastfm_weight=1.0,
        history_boost=0.0,
        genre_filter=["jazz"],  # seul BetaArt a jazz
        n_results=10,
    )
    assert len(recs) == 1
    assert recs[0].artist == "BetaArt"


def test_recommend_history_boost():
    """Un artiste écouté >= 60 min voit son score boosté."""
    lastfm_sim, spotify_sim, lastfm_tags = _build_simple_data()
    base = recommend(
        seeds={"SeedX": 1.0},
        lastfm_similar=lastfm_sim, spotify_similar=spotify_sim,
        lastfm_tags=lastfm_tags, excluded=set(),
        history_minutes_map={},
        lastfm_weight=1.0, history_boost=0.0,
        genre_filter=[], n_results=10,
    )
    boosted = recommend(
        seeds={"SeedX": 1.0},
        lastfm_similar=lastfm_sim, spotify_similar=spotify_sim,
        lastfm_tags=lastfm_tags, excluded=set(),
        history_minutes_map={"BetaArt": 120},  # 120 min > cap, donc boost max
        lastfm_weight=1.0, history_boost=1.0,
        genre_filter=[], n_results=10,
    )
    base_beta = next(r for r in base if r.artist == "BetaArt")
    boosted_beta = next(r for r in boosted if r.artist == "BetaArt")
    # Avec γ=1 et minutes >= cap : boost = 2
    assert boosted_beta.score == pytest.approx(2 * base_beta.score)


def test_recommend_score_normalized_by_seed_weight():
    """Le score doit rester comparable entre runs avec différents nombres de seeds."""
    lastfm_sim = {
        "S1": [{"name": "A", "match": 0.5, "rank": 1}],
        "S2": [{"name": "A", "match": 0.5, "rank": 1}],
    }
    recs_one = recommend(
        seeds={"S1": 1.0},
        lastfm_similar=lastfm_sim, spotify_similar={},
        lastfm_tags={"A": []}, excluded=set(),
        history_minutes_map={}, lastfm_weight=1.0,
        history_boost=0.0, genre_filter=[], n_results=10,
    )
    recs_two = recommend(
        seeds={"S1": 1.0, "S2": 1.0},
        lastfm_similar=lastfm_sim, spotify_similar={},
        lastfm_tags={"A": []}, excluded=set(),
        history_minutes_map={}, lastfm_weight=1.0,
        history_boost=0.0, genre_filter=[], n_results=10,
    )
    # Avec normalisation, le score moyen reste à ~0.5 dans les deux cas
    assert recs_one[0].score == pytest.approx(0.5)
    assert recs_two[0].score == pytest.approx(0.5)


def test_compute_artist_popularity():
    lastfm = {
        "S1": [{"name": "A", "match": 1.0, "rank": 1}, {"name": "B", "match": 0.5, "rank": 2}],
        "S2": [{"name": "A", "match": 1.0, "rank": 1}],
    }
    spotify = {
        "S3": [{"name": "A", "rank": 1}, {"name": "C", "rank": 2}],
    }
    pop = compute_artist_popularity(lastfm, spotify)
    assert pop == {"A": 3, "B": 1, "C": 1}


def test_popularity_penalty_factor_zero_omega():
    """Avec ω = 0, pas de pénalité quel que soit la popularité."""
    assert popularity_penalty_factor(0, 0) == 1.0
    assert popularity_penalty_factor(1000, 0) == 1.0


def test_popularity_penalty_factor_decreases():
    """Le facteur doit décroître quand la popularité augmente (ω > 0)."""
    f1 = popularity_penalty_factor(1, 0.7)
    f10 = popularity_penalty_factor(10, 0.7)
    f100 = popularity_penalty_factor(100, 0.7)
    assert 1.0 > f1 > f10 > f100 > 0


def test_recommend_popularity_penalty_reorders():
    """Avec une forte pénalité, un candidat populaire avec match haut peut
    perdre face à un candidat niche avec match plus bas."""
    lastfm_sim = {
        "S": [
            {"name": "Popular", "match": 0.9, "rank": 1},
            {"name": "Niche", "match": 0.6, "rank": 2},
        ],
    }
    pop = {"Popular": 200, "Niche": 5}
    recs = recommend(
        seeds={"S": 1.0},
        lastfm_similar=lastfm_sim,
        spotify_similar={},
        lastfm_tags={"Popular": [], "Niche": []},
        excluded=set(),
        history_minutes_map={},
        lastfm_weight=1.0,
        history_boost=0.0,
        genre_filter=[],
        n_results=10,
        popularity_penalty=2.0,  # forte pénalité
        artist_popularity=pop,
    )
    # Niche doit passer devant Popular grâce à la pénalité
    assert recs[0].artist == "Niche"
    assert recs[1].artist == "Popular"


def test_recommend_genre_filter_and_mode():
    lastfm_sim = {
        "S": [
            {"name": "Both", "match": 0.9, "rank": 1},
            {"name": "OnlyRock", "match": 0.8, "rank": 2},
        ],
    }
    tags = {"Both": ["rock", "indie"], "OnlyRock": ["rock"]}
    recs_or = recommend(
        seeds={"S": 1.0}, lastfm_similar=lastfm_sim, spotify_similar={},
        lastfm_tags=tags, excluded=set(), history_minutes_map={},
        lastfm_weight=1.0, history_boost=0.0,
        genre_filter=["rock", "indie"], n_results=10, genre_filter_mode="OR",
    )
    assert {r.artist for r in recs_or} == {"Both", "OnlyRock"}

    recs_and = recommend(
        seeds={"S": 1.0}, lastfm_similar=lastfm_sim, spotify_similar={},
        lastfm_tags=tags, excluded=set(), history_minutes_map={},
        lastfm_weight=1.0, history_boost=0.0,
        genre_filter=["rock", "indie"], n_results=10, genre_filter_mode="AND",
    )
    assert {r.artist for r in recs_and} == {"Both"}


def test_genre_expansion_via_sim_index():
    """Avec un sim_index et un seuil, le filtre genre s'étend aux tags proches."""
    lastfm_sim = {
        "S": [
            {"name": "Mini", "match": 0.9, "rank": 1},
            {"name": "Jazzy", "match": 0.8, "rank": 2},
        ],
    }
    tags = {
        "Mini": ["minimal techno"],   # tag voisin de "techno"
        "Jazzy": ["jazz"],             # disjoint
    }
    sim_index = {
        "techno": {"minimal techno": 0.4},
        "minimal techno": {"techno": 0.4},
    }

    # Filtre strict (threshold=1.0) : seul "techno" cherché → personne ne match
    recs_strict = recommend(
        seeds={"S": 1.0}, lastfm_similar=lastfm_sim, spotify_similar={},
        lastfm_tags=tags, excluded=set(), history_minutes_map={},
        lastfm_weight=1.0, history_boost=0.0,
        genre_filter=["techno"], n_results=10,
        tag_sim_index=sim_index, genre_expansion_threshold=1.0,
    )
    assert len(recs_strict) == 0

    # Filtre étendu (threshold=0.3) : "techno" expand à "minimal techno"
    recs_expanded = recommend(
        seeds={"S": 1.0}, lastfm_similar=lastfm_sim, spotify_similar={},
        lastfm_tags=tags, excluded=set(), history_minutes_map={},
        lastfm_weight=1.0, history_boost=0.0,
        genre_filter=["techno"], n_results=10,
        tag_sim_index=sim_index, genre_expansion_threshold=0.3,
    )
    assert len(recs_expanded) == 1
    assert recs_expanded[0].artist == "Mini"


def test_diversify_uses_soft_jaccard_when_index_provided():
    """Avec sim_index, MMR pénalise les tags proches même s'ils diffèrent."""
    recs = [
        _make_rec("A", 1.0, ["techno"]),
        _make_rec("B", 0.95, ["minimal techno"]),  # proche de A via sim
        _make_rec("C", 0.5, ["jazz"]),
    ]
    sim_index = {
        "techno": {"minimal techno": 0.5},
        "minimal techno": {"techno": 0.5},
    }
    out = diversify_mmr(recs, diversity_weight=1.0, n=2, tag_sim_index=sim_index)
    # Avec MMR souple, B est pénalisé (proche de A) → C doit passer en 2e
    assert out[0].artist == "A"
    assert out[1].artist == "C"


def test_recommend_empty_seeds():
    recs = recommend(
        seeds={}, lastfm_similar={}, spotify_similar={}, lastfm_tags={},
        excluded=set(), history_minutes_map={},
        lastfm_weight=0.5, history_boost=0.3, genre_filter=[], n_results=5,
    )
    assert recs == []


# ---------------------------------------------------------------------------
# Qobuz : 3e source de similarité
# ---------------------------------------------------------------------------

def test_qobuz_rank_to_score():
    from engine import qobuz_rank_to_score, QOBUZ_MAX_RANK
    assert qobuz_rank_to_score(1) == 1.0
    assert qobuz_rank_to_score(QOBUZ_MAX_RANK) == pytest.approx(0.02, abs=1e-3)
    assert qobuz_rank_to_score(QOBUZ_MAX_RANK + 10) == 0.0
    assert qobuz_rank_to_score(0) == 0.0
    # Strictement décroissant
    assert qobuz_rank_to_score(2) < qobuz_rank_to_score(1)


def test_recommend_qobuz_only():
    """qobuz_weight=1, lastfm_weight=0 → score basé uniquement sur Qobuz."""
    qobuz_sim = {
        "S": [
            {"name": "TopMatch", "rank": 1},
            {"name": "FarMatch", "rank": 30},
        ]
    }
    recs = recommend(
        seeds={"S": 1.0},
        lastfm_similar={}, spotify_similar={}, lastfm_tags={},
        excluded=set(), history_minutes_map={},
        lastfm_weight=0.0, qobuz_weight=1.0,
        history_boost=0.0, genre_filter=[], n_results=10,
        qobuz_similar=qobuz_sim,
    )
    assert recs[0].artist == "TopMatch"
    assert recs[0].qobuz_score > recs[1].qobuz_score
    # Spotify et Last.fm doivent être à 0 (pas de données)
    assert recs[0].lastfm_score == 0.0
    assert recs[0].spotify_score == 0.0


def test_recommend_three_sources_combined():
    """Avec lastfm + spotify + qobuz pondérés à 0.4/0.3/0.3, les 3 contribuent."""
    lastfm_sim = {"S": [{"name": "X", "match": 1.0, "rank": 1}]}
    spotify_sim = {"S": [{"name": "X", "rank": 1}]}
    qobuz_sim = {"S": [{"name": "X", "rank": 1}]}

    recs = recommend(
        seeds={"S": 1.0},
        lastfm_similar=lastfm_sim, spotify_similar=spotify_sim,
        lastfm_tags={}, excluded=set(), history_minutes_map={},
        lastfm_weight=0.4, qobuz_weight=0.3,
        history_boost=0.0, genre_filter=[], n_results=10,
        qobuz_similar=qobuz_sim,
    )
    rec = recs[0]
    assert rec.artist == "X"
    assert rec.lastfm_score == 1.0
    assert rec.spotify_score == 1.0
    assert rec.qobuz_score == 1.0
    # spotify_weight = 1 - 0.4 - 0.3 = 0.3 ; score = 0.4*1 + 0.3*1 + 0.3*1 = 1.0
    assert rec.score == pytest.approx(1.0)


def test_recommend_qobuz_weight_clamped():
    """Si lastfm + qobuz > 1, spotify_weight est clampé à 0 (pas de score négatif)."""
    qobuz_sim = {"S": [{"name": "X", "rank": 1}]}
    spotify_sim = {"S": [{"name": "Y", "rank": 1}]}
    recs = recommend(
        seeds={"S": 1.0},
        lastfm_similar={}, spotify_similar=spotify_sim, lastfm_tags={},
        excluded=set(), history_minutes_map={},
        lastfm_weight=0.7, qobuz_weight=0.6,  # somme > 1
        history_boost=0.0, genre_filter=[], n_results=10,
        qobuz_similar=qobuz_sim,
    )
    # X (Qobuz) doit dominer ; Y (Spotify) a un poids effectif 0
    assert recs[0].artist == "X"
    y_recs = [r for r in recs if r.artist == "Y"]
    assert all(r.score == 0 for r in y_recs)


def test_recommend_portrait_propagation():
    """Le portrait Qobuz est propagé sur la recommandation finale."""
    qobuz_sim = {"S": [{"name": "X", "rank": 1}]}
    recs = recommend(
        seeds={"S": 1.0},
        lastfm_similar={}, spotify_similar={}, lastfm_tags={},
        excluded=set(), history_minutes_map={},
        lastfm_weight=0.0, qobuz_weight=1.0,
        history_boost=0.0, genre_filter=[], n_results=10,
        qobuz_similar=qobuz_sim,
        qobuz_portraits={"X": "Bio fictive de X."},
    )
    assert recs[0].portrait == "Bio fictive de X."


def test_compute_artist_popularity_with_qobuz():
    lastfm = {"S1": [{"name": "A", "match": 1.0, "rank": 1}]}
    spotify = {"S2": [{"name": "A", "rank": 1}]}
    qobuz = {"S3": [{"name": "A", "rank": 1}, {"name": "B", "rank": 2}]}
    pop = compute_artist_popularity(lastfm, spotify, qobuz)
    # A apparaît dans les trois → 3
    assert pop["A"] == 3
    assert pop["B"] == 1
