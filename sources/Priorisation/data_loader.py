"""Chargement des données pour Priorisation.

- `load_results_finaux` : concatène tous les `resultats_final_*.xlsx` de
  data/Resultats/, ajoute la colonne `Playlist_source` (= nom du fichier).
- `load_exclusion` : charge un fichier xlsx d'exclusion s'il existe.
- `apply_exclusion` : retire les lignes d'exclusion du df principal.
"""
from pathlib import Path
import pandas as pd
import re


def load_results_finaux(results_dir: Path) -> pd.DataFrame:
    """Charge tous les `resultats_final_*.xlsx` (ou .csv en fallback).

    Ajoute une colonne `Playlist_source` = nom du fichier sans préfixe
    `resultats_final_` ni extension (ex: "Partage").

    Retourne un DataFrame concaténé. Si aucun fichier trouvé, DataFrame vide.
    """
    results_dir = Path(results_dir)
    if not results_dir.exists():
        return pd.DataFrame()

    # Priorité au xlsx (plus stable que csv qui peut avoir des problèmes
    # de quoting sur les noms d'artistes avec virgules)
    xlsx_files = sorted(results_dir.glob("resultats_final_*.xlsx"))
    parts = []
    seen_stems = set()
    for f in xlsx_files:
        stem = f.stem  # "resultats_final_Partage"
        playlist = stem.replace("resultats_final_", "")
        if playlist in seen_stems:
            continue
        seen_stems.add(playlist)
        try:
            df = pd.read_excel(f)
            df["Playlist_source"] = playlist
            parts.append(df)
        except Exception as e:
            print(f"[Priorisation] Echec lecture {f.name}: {e}")

    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


def load_exclusion(exclusion_path: Path) -> pd.DataFrame:
    """Charge le fichier d'exclusion s'il existe. Format = même colonnes que
    `resultats_final_*.xlsx`. On utilise (Artist_A_rechercher, Album_A_rechercher)
    comme clé pour l'exclusion."""
    exclusion_path = Path(exclusion_path)
    if not exclusion_path.exists():
        return pd.DataFrame()
    try:
        return pd.read_excel(exclusion_path)
    except Exception as e:
        print(f"[Priorisation] Echec lecture exclusion {exclusion_path}: {e}")
        return pd.DataFrame()


def apply_exclusion(df_main: pd.DataFrame, df_exclusion: pd.DataFrame) -> pd.DataFrame:
    """Retire de df_main toutes les lignes dont (Artist_A_rechercher,
    Album_A_rechercher) est présent dans df_exclusion. Comparaison
    insensible à la casse + strip."""
    if df_main.empty or df_exclusion.empty:
        return df_main
    if "Artist_A_rechercher" not in df_exclusion.columns or "Album_A_rechercher" not in df_exclusion.columns:
        return df_main

    def _key(s):
        return str(s).strip().lower() if pd.notna(s) else ""

    exclude_keys = set(zip(
        df_exclusion["Artist_A_rechercher"].map(_key),
        df_exclusion["Album_A_rechercher"].map(_key),
    ))
    if not exclude_keys:
        return df_main

    keys_main = list(zip(
        df_main["Artist_A_rechercher"].map(_key),
        df_main["Album_A_rechercher"].map(_key),
    ))
    mask = [k not in exclude_keys for k in keys_main]
    return df_main[mask].reset_index(drop=True)
