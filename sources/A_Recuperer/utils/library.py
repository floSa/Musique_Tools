"""Scan de la bibliothèque physique.

La bibliothèque a 4 racines avec des conventions de nommage différentes :

| Racine                         | Structure        | Mapping                                     |
|--------------------------------|------------------|---------------------------------------------|
| M:\\musiques\\__Autres         | Artiste/Album/   | tel quel                                    |
| M:\\musiques\\__B.O            | "Album - Artiste"/ | split sur le dernier '-' du nom de dossier |
| M:\\musiques\\__COMPILS        | Album/           | Artist forcé à "Various Artists"            |
| M:\\musiques\\__JEUX           | Album/           | Artist forcé à "BO Jeux"                    |
"""
import pandas as pd
from pathlib import Path


# Racines par défaut (sous WSL via /mnt/m/...)
DEFAULT_ROOTS = {
    "autres":  "/mnt/m/musiques/__Autres",
    "bo":      "/mnt/m/musiques/__B.O",
    "compils": "/mnt/m/musiques/__COMPILS",
    "jeux":    "/mnt/m/musiques/__JEUX",
}

COMPILS_ARTIST = "Various Artists"
JEUX_ARTIST    = "BO Jeux"


# ---------------------------------------------------------------------------
# Stratégies de scan
# ---------------------------------------------------------------------------

def scan_artist_album_root(path: str | Path) -> list[dict]:
    """Structure path/Artiste/Album/ — utilisée pour __Autres."""
    library_path = Path(path)
    if not library_path.exists():
        print(f"Bibliothèque non trouvée : {library_path}")
        return []

    donnees = []
    for artiste_path in library_path.iterdir():
        if not artiste_path.is_dir():
            continue
        for album_path in artiste_path.iterdir():
            if album_path.is_dir():
                donnees.append({"Artist": artiste_path.name, "Album": album_path.name})
    return donnees


def scan_bo_root(path: str | Path) -> list[dict]:
    """Structure path/"Album - Artiste"/ — split au DERNIER '-' puis strip.

    Les dossiers sans '-' sont ignorés avec un warning.
    """
    library_path = Path(path)
    if not library_path.exists():
        print(f"Bibliothèque non trouvée : {library_path}")
        return []

    donnees = []
    for entry in library_path.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if "-" not in name:
            print(f"  [BO] Ignoré (pas de '-') : {name!r}")
            continue
        album_part, _, artist_part = name.rpartition("-")
        album = album_part.strip()
        artist = artist_part.strip()
        if not album or not artist:
            print(f"  [BO] Ignoré (vide après split) : {name!r}")
            continue
        donnees.append({"Artist": artist, "Album": album})
    return donnees


def scan_album_only_root(path: str | Path, fixed_artist: str) -> list[dict]:
    """Structure path/Album/ avec un artiste forcé — utilisée pour __COMPILS et __JEUX."""
    library_path = Path(path)
    if not library_path.exists():
        print(f"Bibliothèque non trouvée : {library_path}")
        return []

    donnees = []
    for entry in library_path.iterdir():
        if entry.is_dir():
            donnees.append({"Artist": fixed_artist, "Album": entry.name})
    return donnees


# ---------------------------------------------------------------------------
# API publiques
# ---------------------------------------------------------------------------

def scan_library(
    path: str | Path = "/mnt/m/musiques/__Autres",
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """Scan d'une seule racine Artiste/Album/ (compat historique).

    Pour scanner les 4 racines, utiliser `scan_all_libraries`.
    """
    donnees = scan_artist_album_root(path)
    df = pd.DataFrame(donnees, columns=["Artist", "Album"])
    if not df.empty:
        df = df.sort_values(by=["Artist", "Album"]).reset_index(drop=True)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"Bibliothèque sauvegardée : {output_path} ({len(df)} albums)")

    return df


def scan_all_libraries(
    autres:  str | Path | None = None,
    bo:      str | Path | None = None,
    compils: str | Path | None = None,
    jeux:    str | Path | None = None,
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """Scan combiné des 4 racines (chacune optionnelle si chemin None).

    Toute racine `None` est remplacée par sa valeur par défaut. Pour désactiver
    explicitement une racine, passer un chemin inexistant ou modifier les
    appelants pour ne pas l'inclure.
    """
    autres  = autres  if autres  is not None else DEFAULT_ROOTS["autres"]
    bo      = bo      if bo      is not None else DEFAULT_ROOTS["bo"]
    compils = compils if compils is not None else DEFAULT_ROOTS["compils"]
    jeux    = jeux    if jeux    is not None else DEFAULT_ROOTS["jeux"]

    rows: list[dict] = []
    rows += scan_artist_album_root(autres)
    rows += scan_bo_root(bo)
    rows += scan_album_only_root(compils, COMPILS_ARTIST)
    rows += scan_album_only_root(jeux,    JEUX_ARTIST)

    df = pd.DataFrame(rows, columns=["Artist", "Album"])
    if not df.empty:
        df = (
            df.drop_duplicates(subset=["Artist", "Album"])
              .sort_values(by=["Artist", "Album"])
              .reset_index(drop=True)
        )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"Bibliothèque sauvegardée : {output_path} ({len(df)} albums)")

    return df
