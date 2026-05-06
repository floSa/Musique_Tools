"""
Tests des loaders de bases de similarité (Last.fm + Spotify, tous deux SQLite
depuis l'unification du stockage).
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from engine import (
    load_lastfm_similar,
    load_lastfm_tags,
    load_qobuz_portraits,
    load_qobuz_similar,
    load_spotify_id_index,
    load_spotify_similar,
)


# ---------------------------------------------------------------------------
# Fixtures : création d'une DB Spotify minimale en mémoire (via fichier temp)
# ---------------------------------------------------------------------------

def _make_spotify_db(tmp_path: Path) -> Path:
    db = tmp_path / "spotify.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE artists (
            source_artist TEXT PRIMARY KEY,
            source_artist_id TEXT,
            similar_artists TEXT,
            tags TEXT DEFAULT '[]',
            status TEXT DEFAULT 'success'
        )
        """
    )
    rows = [
        (
            "Worakls",
            "5RPzPJCg4ER1LzQkorZ31p",
            json.dumps([
                {"name": "Joachim Pastor", "id": "6eNOjuJSfKkAvbiGW90AkZ", "rank": 1},
                {"name": "N'to", "id": "7ry8L53T1234567890ABCD", "rank": 2},
            ]),
            "[]",
            "success",
        ),
        (
            "L'Impératrice",
            "1Hx6pxauoOg4PUcEZxAPo7",
            json.dumps([
                {"name": "Polo & Pan", "id": "4cHvyrolmoQNcoI4q2Wkdd", "rank": 1},
            ]),
            "[]",
            "success",
        ),
        # Artiste sans similaires (cas réel après échec scraping)
        ("Inconnu", "", json.dumps([]), "[]", "success"),
    ]
    conn.executemany(
        "INSERT INTO artists VALUES (?, ?, ?, ?, ?)", rows
    )
    conn.commit()
    conn.close()
    return db


def _make_lastfm_db(tmp_path: Path) -> Path:
    db = tmp_path / "lastfm.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE artists (
            source_artist TEXT PRIMARY KEY,
            similar_artists TEXT,
            tags TEXT,
            status TEXT DEFAULT 'success'
        )
        """
    )
    conn.execute(
        "INSERT INTO artists VALUES (?, ?, ?, 'success')",
        (
            "Daft Punk",
            json.dumps([
                {"name": "Justice", "match": 0.95, "rank": 1},
                {"name": "Air", "match": 0.80, "rank": 2},
            ]),
            json.dumps(["electronic", "french house"]),
        ),
    )
    conn.commit()
    conn.close()
    return db


# ---------------------------------------------------------------------------
# Spotify SQLite
# ---------------------------------------------------------------------------

def test_load_spotify_similar(tmp_path):
    db = _make_spotify_db(tmp_path)
    sim = load_spotify_similar(db)

    assert "Worakls" in sim
    assert len(sim["Worakls"]) == 2
    assert sim["Worakls"][0] == {"name": "Joachim Pastor", "rank": 1}
    # Apostrophe gérée nativement (plus besoin d'ast.literal_eval)
    assert "L'Impératrice" in sim
    # Artiste sans similaires : entrée vide mais présente
    assert sim.get("Inconnu") == []


def test_load_spotify_similar_missing_db(tmp_path):
    assert load_spotify_similar(tmp_path / "absent.db") == {}


def test_load_spotify_id_index(tmp_path):
    db = _make_spotify_db(tmp_path)
    idx = load_spotify_id_index(db)

    # Source direct
    assert idx["Worakls"] == "5RPzPJCg4ER1LzQkorZ31p"
    # Voisin (provenant de similar_artists)
    assert idx["Joachim Pastor"] == "6eNOjuJSfKkAvbiGW90AkZ"
    assert idx["Polo & Pan"] == "4cHvyrolmoQNcoI4q2Wkdd"


def test_load_spotify_id_index_filters_invalid(tmp_path):
    """Un id de longueur != 22 doit être ignoré."""
    db = tmp_path / "spotify.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE artists (
            source_artist TEXT PRIMARY KEY,
            source_artist_id TEXT,
            similar_artists TEXT,
            tags TEXT DEFAULT '[]',
            status TEXT DEFAULT 'success'
        )
        """
    )
    conn.execute(
        "INSERT INTO artists VALUES ('A', 'short', '[]', '[]', 'success')"
    )
    conn.commit()
    conn.close()

    idx = load_spotify_id_index(db)
    assert "A" not in idx


# ---------------------------------------------------------------------------
# Qobuz SQLite
# ---------------------------------------------------------------------------

def _make_qobuz_db(tmp_path: Path) -> Path:
    db = tmp_path / "qobuz.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE artists (
            source_artist TEXT PRIMARY KEY,
            source_artist_id TEXT,
            similar_artists TEXT,
            portrait TEXT,
            tags TEXT DEFAULT '[]',
            status TEXT DEFAULT 'success'
        )
        """
    )
    conn.execute(
        "INSERT INTO artists VALUES (?, ?, ?, ?, '[]', 'success')",
        (
            "Worakls",
            "525535",
            json.dumps([
                {"name": "Joachim Pastor", "id": "843818", "rank": 1},
                {"name": "NTO", "id": "952600", "rank": 2},
            ]),
            "Worakls est un artiste français de musique électronique mélodique...",
        ),
    )
    conn.execute(
        "INSERT INTO artists VALUES (?, ?, ?, ?, '[]', 'success')",
        ("Sans bio", "999", json.dumps([{"name": "X", "id": "1", "rank": 1}]), ""),
    )
    conn.commit()
    conn.close()
    return db


def test_load_qobuz_similar(tmp_path):
    db = _make_qobuz_db(tmp_path)
    sim = load_qobuz_similar(db)

    assert "Worakls" in sim
    assert sim["Worakls"][0] == {"name": "Joachim Pastor", "rank": 1}
    assert sim["Worakls"][1] == {"name": "NTO", "rank": 2}


def test_load_qobuz_similar_missing_db(tmp_path):
    assert load_qobuz_similar(tmp_path / "absent.db") == {}


def test_load_qobuz_portraits(tmp_path):
    db = _make_qobuz_db(tmp_path)
    portraits = load_qobuz_portraits(db)

    assert "Worakls" in portraits
    assert "musique électronique" in portraits["Worakls"]
    # Les artistes sans bio sont absents du dict (cohérent avec un .get() côté UI)
    assert "Sans bio" not in portraits


# ---------------------------------------------------------------------------
# Last.fm SQLite — non régression
# ---------------------------------------------------------------------------

def test_load_lastfm_similar(tmp_path):
    db = _make_lastfm_db(tmp_path)
    sim = load_lastfm_similar(db)

    assert "Daft Punk" in sim
    assert sim["Daft Punk"][0]["name"] == "Justice"
    assert sim["Daft Punk"][0]["match"] == pytest.approx(0.95)


def test_load_lastfm_tags(tmp_path):
    db = _make_lastfm_db(tmp_path)
    tags = load_lastfm_tags(db)

    assert tags["Daft Punk"] == ["electronic", "french house"]
