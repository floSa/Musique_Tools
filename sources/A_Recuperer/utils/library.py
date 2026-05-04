import pandas as pd
from pathlib import Path


def scan_library(
    path: str | Path = "/mnt/m/musiques/__Autres",
    output_path: str | Path | None = None,
) -> pd.DataFrame:
    """
    Scan a music library organised as path/Artist/Album/ and return a DataFrame.

    Args:
        path: Root folder to scan (default: /mnt/m/musiques/__Autres via WSL).
        output_path: If provided, save the result as CSV at this path.

    Returns:
        DataFrame with columns ['Artist', 'Album'].
    """
    library_path = Path(path)
    if not library_path.exists():
        print(f"Bibliothèque non trouvée : {library_path}")
        return pd.DataFrame(columns=['Artist', 'Album'])

    donnees = []
    for artiste_path in library_path.iterdir():
        if not artiste_path.is_dir():
            continue
        for album_path in artiste_path.iterdir():
            if album_path.is_dir():
                donnees.append({'Artist': artiste_path.name, 'Album': album_path.name})

    df = pd.DataFrame(donnees).sort_values(by=['Artist', 'Album']).reset_index(drop=True)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"Bibliothèque sauvegardée : {output_path} ({len(df)} albums)")

    return df
