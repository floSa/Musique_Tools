import os
import pandas as pd
from pathlib import Path


def load_playlists(folder_path: str | Path) -> pd.DataFrame:
    """Load all CSV playlist files from a folder into a single DataFrame."""
    folder = Path(folder_path)
    all_data = []
    for filename in os.listdir(folder):
        if not filename.endswith(".csv"):
            continue
        base = os.path.splitext(filename)[0]
        try:
            df = pd.read_csv(folder / filename)
            df.insert(0, 'Playlist', base)
            all_data.append(df)
        except Exception as e:
            print(f"Erreur lecture {filename}: {e}")
    if not all_data:
        print(f"Aucun CSV trouvé dans: {folder}")
        return pd.DataFrame()
    return pd.concat(all_data, ignore_index=True)


def load_recherches_effectuees(path: str | Path) -> pd.DataFrame:
    """Load recherches_effectuees.xlsx and return a clean deduplicated DataFrame."""
    df = pd.read_excel(path)
    if "recuperer" in df.columns:
        df.drop(columns=["recuperer"], inplace=True)
    df = df.drop_duplicates(subset=['Artist', 'Album'])
    df = df.sort_values(by=['Artist', 'Album'])
    return df
