"""Calcul des scores de priorité pour les albums à récupérer.

6 signaux disponibles (chacun activable/désactivable via flag) :

1. `reco`        : score de Recommandation (Last.fm + Spotify + Qobuz)
                   pour cet artiste s'il était évalué par le moteur.
2. `multi_plist` : nombre de playlists distinctes où l'artiste apparaît.
3. `possedes`    : nombre d'albums déjà possédés du même artiste
                   (lecture `Liste_albums_pos`).
4. `dispo_bm`    : 1 si `Sources Bibli` non vide, 0 sinon.
5. `nb_a_recup`  : nombre d'albums à récupérer du même artiste
                   (= group-by sur le df d'entrée).
6. `ecoute`      : minutes d'écoute totales Spotify de l'artiste.

Chaque signal est normalisé min-max sur l'ensemble des artistes candidats,
puis pondéré et sommé. Sortie : score par artiste, puis on garde TOUTES
les lignes des top N artistes.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Import du moteur de Recommandation pour réutiliser les loaders et helpers.
# On évite `sys.path.insert + from engine import` qui rentrerait en conflit
# avec ce fichier (lui-même nommé engine.py) → utilisation d'importlib.
import importlib.util as _ilu

_RECO_ENGINE_PATH = Path(__file__).parent.parent / "Recommandation" / "engine.py"
_spec = _ilu.spec_from_file_location("reco_engine", _RECO_ENGINE_PATH)
_reco = _ilu.module_from_spec(_spec)
assert _spec.loader is not None
# Inscrire dans sys.modules AVANT exec_module pour que @dataclass et autres
# introspections puissent retrouver le module via cls.__module__.
sys.modules["reco_engine"] = _reco
_spec.loader.exec_module(_reco)

load_history          = _reco.load_history
history_minutes       = _reco.history_minutes
load_lastfm_similar   = _reco.load_lastfm_similar
load_spotify_similar  = _reco.load_spotify_similar
load_qobuz_similar    = _reco.load_qobuz_similar
spotify_rank_to_score = _reco.spotify_rank_to_score
qobuz_rank_to_score   = _reco.qobuz_rank_to_score
normalize_artist      = _reco.normalize_artist


SIGNAL_NAMES = ["reco", "multi_plist", "possedes", "dispo_bm", "nb_a_recup", "ecoute"]


def _split_first(name: str) -> str:
    """Premier artiste d'une chaîne potentiellement multi-artistes (split sur ',')."""
    if not isinstance(name, str):
        return ""
    return name.split(",")[0].strip()


def _safe_normalize_minmax(series: pd.Series) -> pd.Series:
    """Min-max scaling, retourne 0 si tous les valeurs sont identiques."""
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    mn, mx = s.min(), s.max()
    if mx <= mn:
        return pd.Series(np.zeros(len(s)), index=s.index)
    return (s - mn) / (mx - mn)


# ---------------------------------------------------------------------------
# Calcul des signaux par artiste
# ---------------------------------------------------------------------------

def compute_signal_multi_playlists(df: pd.DataFrame) -> dict[str, int]:
    """Pour chaque artiste (clé : 1er nom, normalisé), nombre de playlists distinctes."""
    if df.empty or "Playlist_source" not in df.columns:
        return {}
    tmp = df[["Artist_A_rechercher", "Playlist_source"]].copy()
    tmp["artist_key"] = tmp["Artist_A_rechercher"].map(lambda x: normalize_artist(_split_first(str(x))))
    return tmp.groupby("artist_key")["Playlist_source"].nunique().to_dict()


def compute_signal_possedes(df: pd.DataFrame) -> dict[str, int]:
    """Nombre d'albums possédés pour cet artiste, depuis `Liste_albums_pos`
    (champ déjà calculé par --match : liste des albums du même artiste en
    bibliothèque, séparés par ` - `)."""
    if df.empty or "Liste_albums_pos" not in df.columns:
        return {}
    tmp = df[["Artist_A_rechercher", "Liste_albums_pos"]].copy()
    tmp["artist_key"] = tmp["Artist_A_rechercher"].map(lambda x: normalize_artist(_split_first(str(x))))

    def _count(v):
        if pd.isna(v) or not str(v).strip():
            return 0
        return len([x for x in str(v).split(" - ") if x.strip()])

    tmp["n"] = tmp["Liste_albums_pos"].map(_count)
    return tmp.groupby("artist_key")["n"].max().to_dict()


def compute_signal_dispo_bm(df: pd.DataFrame) -> dict[str, int]:
    """Pour chaque artiste : 1 s'il a au moins un album dispo BM Lyon
    (Sources Bibli non vide), 0 sinon."""
    if df.empty or "Sources Bibli" not in df.columns:
        return {}
    tmp = df[["Artist_A_rechercher", "Sources Bibli"]].copy()
    tmp["artist_key"] = tmp["Artist_A_rechercher"].map(lambda x: normalize_artist(_split_first(str(x))))
    tmp["has_bm"] = tmp["Sources Bibli"].apply(
        lambda v: 1 if pd.notna(v) and str(v).strip() else 0
    )
    return tmp.groupby("artist_key")["has_bm"].max().to_dict()


def compute_signal_nb_a_recup(df: pd.DataFrame) -> dict[str, int]:
    """Nombre d'albums distincts à récupérer pour chaque artiste."""
    if df.empty:
        return {}
    tmp = df[["Artist_A_rechercher", "Album_A_rechercher"]].copy()
    tmp["artist_key"] = tmp["Artist_A_rechercher"].map(lambda x: normalize_artist(_split_first(str(x))))
    return tmp.groupby("artist_key")["Album_A_rechercher"].nunique().to_dict()


def compute_signal_ecoute(artist_keys: Iterable[str], history_dir: Path) -> dict[str, float]:
    """Minutes d'écoute totales par artiste (depuis l'historique Spotify).

    On normalise la clé artiste (`normalize_artist(_split_first(...))`) pour
    comparaison cohérente avec les autres signaux.
    """
    df_hist = load_history(Path(history_dir))
    if df_hist.empty:
        return {a: 0.0 for a in artist_keys}
    minutes_per_artist = history_minutes(df_hist)
    # Reclé : normalize_artist sur les artistes de l'historique
    normalized_minutes: dict[str, float] = {}
    for art, mn in minutes_per_artist.items():
        k = normalize_artist(_split_first(art))
        normalized_minutes[k] = normalized_minutes.get(k, 0.0) + float(mn)
    return {a: normalized_minutes.get(a, 0.0) for a in artist_keys}


def compute_signal_reco(
    artist_keys: Iterable[str],
    history_dir: Path,
    lastfm_db: Path,
    spotify_db: Path,
    qobuz_db: Path,
    lastfm_weight: float = 0.4,
    qobuz_weight: float = 0.2,
) -> dict[str, float]:
    """Pour chaque artiste candidat, calcule le score qu'il aurait dans le
    moteur de Recommandation (sans filtre exclusion).

    Seeds = historique d'écoute pondéré par minutes (normalisées /max).
    Sources : Last.fm (score 0-1), Spotify (rang→score), Qobuz (rang→score).
    Pondération : `lastfm_weight + qobuz_weight + spotify_weight = 1`,
    `spotify_weight` déduit.

    Plus le score est haut, plus le moteur de Recommandation aurait poussé
    cet artiste comme similaire à ce que l'utilisateur écoute déjà.
    """
    artist_set = {a for a in artist_keys if a}
    if not artist_set:
        return {}

    # Seeds = artistes de l'historique, poids = minutes normalisées par max
    df_hist = load_history(Path(history_dir))
    minutes_per_artist = history_minutes(df_hist) if not df_hist.empty else {}
    if minutes_per_artist:
        max_mn = max(minutes_per_artist.values()) or 1.0
        seeds = {normalize_artist(_split_first(a)): mn / max_mn for a, mn in minutes_per_artist.items()}
    else:
        seeds = {}

    # Charger les similarités
    lfm = load_lastfm_similar(Path(lastfm_db)) if lastfm_db.exists() else {}
    spt = load_spotify_similar(Path(spotify_db)) if spotify_db.exists() else {}
    qbz = load_qobuz_similar(Path(qobuz_db)) if qobuz_db.exists() else {}

    spotify_weight = max(0.0, 1.0 - lastfm_weight - qobuz_weight)

    # Construire les inverted index {candidate_norm: [(seed_norm, raw_score)]}
    # On fait ça une seule fois pour tous les seeds.
    inv_lfm: dict[str, float] = {a: 0.0 for a in artist_set}
    inv_spt: dict[str, float] = {a: 0.0 for a in artist_set}
    inv_qbz: dict[str, float] = {a: 0.0 for a in artist_set}

    for seed, w in seeds.items():
        if w <= 0:
            continue
        # Le seed peut être stocké avec une casse différente dans les DBs
        # (les loaders renvoient les clés telles que stockées). On compare
        # via normalize_artist.
        # Pour éviter un loop O(N*M), on construit un index sur les DBs ↓
        pass

    # Index "DB clé normalisée → liste de sims"
    def _idx(d):
        return {normalize_artist(k): v for k, v in d.items()}

    lfm_idx = _idx(lfm)
    spt_idx = _idx(spt)
    qbz_idx = _idx(qbz)

    for seed_key, w in seeds.items():
        if w <= 0:
            continue
        for sim in lfm_idx.get(seed_key, []):
            c = normalize_artist(sim["name"])
            if c in inv_lfm:
                inv_lfm[c] += w * float(sim.get("match", 0))
        for sim in spt_idx.get(seed_key, []):
            c = normalize_artist(sim["name"])
            if c in inv_spt:
                inv_spt[c] += w * spotify_rank_to_score(int(sim.get("rank", 999)))
        for sim in qbz_idx.get(seed_key, []):
            c = normalize_artist(sim["name"])
            if c in inv_qbz:
                inv_qbz[c] += w * qobuz_rank_to_score(int(sim.get("rank", 999)))

    out = {}
    for a in artist_set:
        out[a] = (
            lastfm_weight * inv_lfm.get(a, 0.0)
            + spotify_weight * inv_spt.get(a, 0.0)
            + qobuz_weight * inv_qbz.get(a, 0.0)
        )
    return out


# ---------------------------------------------------------------------------
# Orchestration : compute_priority_scores
# ---------------------------------------------------------------------------

def compute_priority_scores(
    df: pd.DataFrame,
    *,
    signals_enabled: dict[str, bool],
    signal_weights: dict[str, float],
    top_n: int,
    history_dir: Path | None = None,
    lastfm_db: Path | None = None,
    spotify_db: Path | None = None,
    qobuz_db: Path | None = None,
    reco_lastfm_weight: float = 0.4,
    reco_qobuz_weight: float = 0.2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calcule un score de priorité par artiste, garde les top N et retourne
    toutes leurs lignes.

    Args:
        df : DataFrame consolidé des `resultats_final_*.xlsx` (avec colonne
             `Playlist_source` ajoutée par `load_results_finaux`).
        signals_enabled : {nom_signal: bool} parmi `SIGNAL_NAMES`.
        signal_weights  : {nom_signal: float} — poids ; ceux des signaux
            désactivés sont ignorés et les poids des activés sont re-normalisés
            à somme = 1.
        top_n : nombre d'artistes à garder.
        history_dir, lastfm_db, spotify_db, qobuz_db : chemins pour le signal `reco`
            et le signal `ecoute`. Requis seulement si le signal correspondant
            est activé.

    Returns:
        Tuple `(df_artists, df_lines)` :
        - `df_artists` : 1 ligne par artiste sélectionné, colonnes
          [Artist_key, Score_prio, Rang_artiste, + valeurs des signaux].
        - `df_lines`   : toutes les lignes de `df` correspondant aux top N
          artistes, avec colonnes ajoutées `Score_prio`, `Rang_artiste`.
          Triées par Rang_artiste, puis Album.
    """
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Re-normalisation des poids actifs
    active = {s: signal_weights.get(s, 0.0) for s in SIGNAL_NAMES if signals_enabled.get(s, False)}
    total = sum(active.values())
    if total <= 0:
        # Aucun signal actif → tous les artistes ex aequo (score 0)
        active_norm: dict[str, float] = {}
    else:
        active_norm = {s: w / total for s, w in active.items()}

    # Clés artistes uniques (1er artiste, normalisé)
    df = df.copy()
    df["__artist_key"] = df["Artist_A_rechercher"].map(lambda x: normalize_artist(_split_first(str(x))))
    artist_keys = sorted(set(df["__artist_key"].dropna().unique()) - {""})

    # Calcul des signaux activés
    raw: dict[str, dict[str, float]] = {}
    if signals_enabled.get("multi_plist"):
        raw["multi_plist"] = compute_signal_multi_playlists(df)
    if signals_enabled.get("possedes"):
        raw["possedes"] = compute_signal_possedes(df)
    if signals_enabled.get("dispo_bm"):
        raw["dispo_bm"] = compute_signal_dispo_bm(df)
    if signals_enabled.get("nb_a_recup"):
        raw["nb_a_recup"] = compute_signal_nb_a_recup(df)
    if signals_enabled.get("ecoute") and history_dir:
        raw["ecoute"] = compute_signal_ecoute(artist_keys, history_dir)
    if signals_enabled.get("reco") and history_dir and (lastfm_db or spotify_db or qobuz_db):
        raw["reco"] = compute_signal_reco(
            artist_keys, history_dir,
            lastfm_db or Path("/dev/null"),
            spotify_db or Path("/dev/null"),
            qobuz_db or Path("/dev/null"),
            lastfm_weight=reco_lastfm_weight,
            qobuz_weight=reco_qobuz_weight,
        )

    # DataFrame par artiste avec les signaux bruts
    art_df = pd.DataFrame({"Artist_key": artist_keys})
    for s, mapping in raw.items():
        art_df[f"signal_{s}"] = art_df["Artist_key"].map(lambda a: float(mapping.get(a, 0)))

    # Normalisation min-max et combinaison
    art_df["Score_prio"] = 0.0
    for s, w in active_norm.items():
        col = f"signal_{s}"
        if col not in art_df.columns:
            continue
        norm = _safe_normalize_minmax(art_df[col])
        art_df[f"norm_{s}"] = norm
        art_df["Score_prio"] += w * norm

    # Tri + top N
    art_df = art_df.sort_values("Score_prio", ascending=False).reset_index(drop=True)
    art_df["Rang_artiste"] = art_df.index + 1
    top_artists = art_df.head(top_n).copy()

    # Pour ces artistes, récupérer TOUTES leurs lignes de df
    top_keys = set(top_artists["Artist_key"])
    df_lines = df[df["__artist_key"].isin(top_keys)].copy()

    # Joindre Score_prio + Rang_artiste sur chaque ligne
    rank_map = dict(zip(top_artists["Artist_key"], top_artists["Rang_artiste"]))
    score_map = dict(zip(top_artists["Artist_key"], top_artists["Score_prio"]))
    df_lines["Rang_artiste"] = df_lines["__artist_key"].map(rank_map)
    df_lines["Score_prio"] = df_lines["__artist_key"].map(score_map)
    df_lines = df_lines.drop(columns="__artist_key")

    # Tri final : par rang, puis par album
    sort_cols = ["Rang_artiste"]
    if "Album_A_rechercher" in df_lines.columns:
        sort_cols.append("Album_A_rechercher")
    df_lines = df_lines.sort_values(sort_cols).reset_index(drop=True)

    # Placer Score_prio / Rang_artiste en TÊTE des colonnes pour visibilité
    ordered_cols = ["Rang_artiste", "Score_prio"] + [
        c for c in df_lines.columns if c not in ("Rang_artiste", "Score_prio")
    ]
    df_lines = df_lines[ordered_cols]

    return top_artists.drop(columns=["Artist_key"]).rename(
        columns={
            **{f"signal_{s}": f"raw_{s}" for s in SIGNAL_NAMES},
        }
    ).assign(Artist=top_artists["Artist_key"]).set_index("Artist"), df_lines
