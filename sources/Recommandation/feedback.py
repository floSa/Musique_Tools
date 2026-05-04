"""
Gestion du feedback utilisateur (👍/👎 sur les recommandations).

Stockage : `data/Recommandation/feedback.csv` (colonnes : artist, vote, timestamp).

Règles :
- vote = +1 (like) : mémorisé, sans effet automatique sur les recos futures.
  Utilisable pour analyse ou tuning manuel des paramètres.
- vote = -1 (dislike) : exclusion automatique des recos futures (au même titre
  que les artistes de la bibliothèque ou des playlists).
- Si plusieurs votes pour un même artiste : le plus récent l'emporte.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

FEEDBACK_DIR = Path(__file__).parent.parent.parent / "data" / "Recommandation"
FEEDBACK_FILE = FEEDBACK_DIR / "feedback.csv"


def save_feedback(artist: str, vote: int) -> None:
    """Enregistre un vote. `vote` doit être +1 ou -1."""
    if vote not in (1, -1):
        raise ValueError("vote must be +1 or -1")
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not FEEDBACK_FILE.exists()
    with open(FEEDBACK_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["artist", "vote", "timestamp"])
        w.writerow([artist, vote, datetime.now().isoformat(timespec="seconds")])


def _load_latest_votes() -> pd.DataFrame:
    """Pour chaque artiste, garde uniquement le vote le plus récent."""
    if not FEEDBACK_FILE.exists():
        return pd.DataFrame(columns=["artist", "vote", "timestamp"])
    df = pd.read_csv(FEEDBACK_FILE)
    if df.empty:
        return df
    df = df.sort_values("timestamp").drop_duplicates(subset=["artist"], keep="last")
    return df


def get_disliked() -> set[str]:
    df = _load_latest_votes()
    if df.empty:
        return set()
    return set(df[df["vote"] < 0]["artist"])


def get_liked() -> set[str]:
    df = _load_latest_votes()
    if df.empty:
        return set()
    return set(df[df["vote"] > 0]["artist"])


def get_vote(artist: str) -> int | None:
    """Retourne +1, -1 ou None si pas de vote pour cet artiste."""
    df = _load_latest_votes()
    row = df[df["artist"] == artist]
    if row.empty:
        return None
    return int(row.iloc[0]["vote"])


def stats() -> dict[str, int]:
    df = _load_latest_votes()
    if df.empty:
        return {"likes": 0, "dislikes": 0}
    return {
        "likes": int((df["vote"] > 0).sum()),
        "dislikes": int((df["vote"] < 0).sum()),
    }
