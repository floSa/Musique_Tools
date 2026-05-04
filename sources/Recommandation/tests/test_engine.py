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
    diversify_mmr,
    history_weights,
    normalize_artist,
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


def test_recommend_empty_seeds():
    recs = recommend(
        seeds={}, lastfm_similar={}, spotify_similar={}, lastfm_tags={},
        excluded=set(), history_minutes_map={},
        lastfm_weight=0.5, history_boost=0.3, genre_filter=[], n_results=5,
    )
    assert recs == []
