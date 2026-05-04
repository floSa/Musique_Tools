"""
Persistance des sessions de recommandation.

Stockage : `data/Recommandation/sessions.csv`.

Une ligne par recommandation produite, avec timestamp partagé pour grouper
les recos d'une même session. Permet de retrouver "qu'est-ce que j'avais reco
le 5 mai avec ces paramètres ?".

Colonnes :
    session_id    : timestamp ISO de la session (commun à toutes les recos d'un run)
    rank          : position dans le top (1, 2, ...)
    artist        : nom de l'artiste recommandé
    score         : score final
    citations     : nombre de seeds qui pointent
    lastfm_score  : sous-score Last.fm
    spotify_score : sous-score Spotify
    tags          : tags joints par "|"
    seeds         : seeds principaux joints par "|" (limités à 10)
    params        : paramètres joints par ";" (alpha, beta, gamma, lambda)
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

SESSION_DIR = Path(__file__).parent.parent.parent / "data" / "Recommandation"
SESSION_FILE = SESSION_DIR / "sessions.csv"

COLUMNS = [
    "session_id", "rank", "artist", "score",
    "citations", "lastfm_score", "spotify_score",
    "tags", "seeds", "params",
]


def save_session(recs: list, seeds: dict[str, float], params: dict[str, Any]) -> str:
    """Persiste les recos d'une session. Retourne le `session_id` créé."""
    if not recs:
        return ""

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().isoformat(timespec="seconds")

    top_seeds = sorted(seeds.items(), key=lambda x: x[1], reverse=True)[:10]
    seeds_str = "|".join(f"{a}:{w:.2f}" for a, w in top_seeds)
    params_str = ";".join(f"{k}={v}" for k, v in params.items())

    new_file = not SESSION_FILE.exists()
    with open(SESSION_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(COLUMNS)
        for i, r in enumerate(recs, 1):
            w.writerow([
                session_id, i, r.artist, round(r.score, 4),
                r.citations, round(r.lastfm_score, 4), round(r.spotify_score, 4),
                "|".join(r.tags), seeds_str, params_str,
            ])
    return session_id


def list_sessions() -> pd.DataFrame:
    """Retourne un DataFrame résumant les sessions (1 ligne par session)."""
    if not SESSION_FILE.exists():
        return pd.DataFrame(columns=["session_id", "n_recos", "top_artist", "params"])
    df = pd.read_csv(SESSION_FILE)
    if df.empty:
        return df
    summary = (
        df.sort_values(["session_id", "rank"])
          .groupby("session_id")
          .agg(
              n_recos=("artist", "count"),
              top_artist=("artist", "first"),
              params=("params", "first"),
          )
          .reset_index()
          .sort_values("session_id", ascending=False)
    )
    return summary


def load_session(session_id: str) -> pd.DataFrame:
    """Retourne les recos détaillées d'une session donnée."""
    if not SESSION_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(SESSION_FILE)
    return df[df["session_id"] == session_id].sort_values("rank")
