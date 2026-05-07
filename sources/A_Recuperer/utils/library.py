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
    """Structure path/Artiste/Album/ — utilisée pour __Autres.

    Chaque entrée est `{Artist, Album, Path}` où `Path` est le chemin absolu
    du dossier album (utile pour ouvrir le dossier depuis le fichier final).
    """
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
                donnees.append({
                    "Artist": artiste_path.name,
                    "Album":  album_path.name,
                    "Path":   str(album_path),
                })
    return donnees


def scan_bo_root(path: str | Path) -> list[dict]:
    """Structure path/"Album - Artiste"/ — split au DERNIER '-' puis strip.

    Les dossiers sans '-' (Artiste seul, OST série, compilation thématique
    type "Disney Best OF") sont quand même indexés avec `Artist="BO"` et
    `Album=nom_dossier` — sinon ils ne seraient pas matchés du tout.
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
        if "-" in name:
            album_part, _, artist_part = name.rpartition("-")
            album = album_part.strip()
            artist = artist_part.strip()
            if album and artist:
                donnees.append({"Artist": artist, "Album": album, "Path": str(entry)})
                continue
            # Si vide après split → fallback comme "sans tiret"
        # Sans tiret (ou vide après split) : on garde quand même l'entrée
        donnees.append({"Artist": "BO", "Album": name, "Path": str(entry)})
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
            donnees.append({
                "Artist": fixed_artist,
                "Album":  entry.name,
                "Path":   str(entry),
            })
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
    df = pd.DataFrame(donnees, columns=["Artist", "Album", "Path"])
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

    df = pd.DataFrame(rows, columns=["Artist", "Album", "Path"])
    if not df.empty:
        df = (
            df.drop_duplicates(subset=["Artist", "Album"])
              .sort_values(by=["Artist", "Album"])
              .reset_index(drop=True)
        )

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        # En plus du CSV, on génère systématiquement un Excel à côté pour
        # consultation directe (même nom de base, extension .xlsx).
        xlsx_path = output_path.with_suffix(".xlsx")
        try:
            df.to_excel(xlsx_path, index=False)
            print(f"Bibliothèque sauvegardée : {output_path} + {xlsx_path.name} ({len(df)} albums)")
        except Exception as e:
            print(f"Bibliothèque sauvegardée : {output_path} ({len(df)} albums)")
            print(f"  (xlsx non généré : {e})")

    return df
