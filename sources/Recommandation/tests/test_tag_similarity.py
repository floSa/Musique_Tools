"""Tests de l'index de similarité de tags par co-occurrence."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from tag_similarity import (
    build_tag_cooccurrence,
    expand_genre_filter,
    soft_jaccard,
    tag_similarity,
    top_neighbors,
)


def test_build_index_basic():
    tags = {
        "A1": ["rock", "indie"],
        "A2": ["rock", "indie"],
        "A3": ["rock"],
        "A4": ["jazz"],
    }
    idx = build_tag_cooccurrence(tags, min_occurrences=1, min_similarity=0)
    # rock & indie : 2 artistes en commun (A1, A2), |rock|=3, |indie|=2
    # cos = 2 / sqrt(3 * 2) = 2 / 2.449 ≈ 0.816
    assert idx["rock"]["indie"] == pytest.approx(2 / (3 * 2) ** 0.5, abs=1e-3)
    # symétrie
    assert idx["indie"]["rock"] == idx["rock"]["indie"]
    # rock & jazz : aucun artiste commun
    assert "jazz" not in idx.get("rock", {})


def test_min_occurrences_filters_rare():
    tags = {
        "A1": ["rock", "weird"],
    }
    idx = build_tag_cooccurrence(tags, min_occurrences=2, min_similarity=0)
    # Tous deux portés par 1 seul artiste → exclus
    assert idx == {}


def test_min_similarity_filters_low():
    tags = {f"A{i}": ["t1"] for i in range(10)}
    tags["A0"] = ["t1", "t2"]
    tags["A1"] = ["t2"] * 1
    # t1 & t2 partagent 1 seul artiste sur 10 et 2 → cos = 1/sqrt(10*2) ≈ 0.22
    idx_strict = build_tag_cooccurrence(tags, min_occurrences=1, min_similarity=0.5)
    assert "t2" not in idx_strict.get("t1", {})


def test_tag_similarity_identity():
    assert tag_similarity("rock", "rock", {}) == 1.0
    assert tag_similarity("Rock", "rock", {}) == 1.0  # casse


def test_tag_similarity_lookup():
    idx = {"rock": {"indie": 0.5}}
    assert tag_similarity("rock", "indie", idx) == 0.5
    assert tag_similarity("rock", "jazz", idx) == 0.0


def test_soft_jaccard_identical():
    idx = {}
    assert soft_jaccard(["rock"], ["rock"], idx) == 1.0


def test_soft_jaccard_disjoint_no_sim():
    assert soft_jaccard(["rock"], ["jazz"], {}) == 0.0


def test_soft_jaccard_via_index():
    """Tags différents mais proches via index → score intermédiaire."""
    idx = {
        "rock": {"indie rock": 0.8},
        "indie rock": {"rock": 0.8},
    }
    s = soft_jaccard(["rock"], ["indie rock"], idx)
    assert s == pytest.approx(0.8)


def test_soft_jaccard_empty():
    assert soft_jaccard([], ["rock"], {}) == 0.0
    assert soft_jaccard(["rock"], [], {}) == 0.0


def test_expand_genre_filter_strict():
    idx = {"techno": {"minimal techno": 0.4}}
    expanded = expand_genre_filter(["techno"], idx, threshold=1.0)
    assert expanded == {"techno"}


def test_expand_genre_filter_loose():
    idx = {
        "techno": {"minimal techno": 0.4, "house": 0.2},
    }
    expanded = expand_genre_filter(["techno"], idx, threshold=0.3)
    assert expanded == {"techno", "minimal techno"}
    # threshold plus bas inclut house
    expanded2 = expand_genre_filter(["techno"], idx, threshold=0.1)
    assert expanded2 == {"techno", "minimal techno", "house"}


def test_top_neighbors():
    idx = {"rock": {"indie": 0.5, "alt": 0.3, "metal": 0.7}}
    top = top_neighbors("rock", idx, k=2)
    assert top == [("metal", 0.7), ("indie", 0.5)]
