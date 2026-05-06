"""
Scraper Khinsider — téléchargement de bandes originales de jeux vidéo.

Usage :
    cd sources/Musique_Jeux_Video
    uv run python main.py

Source de vérité : `data/Musique_Jeux_Video/albums_khinsider.csv` avec deux colonnes :
    url   — URL khinsider de l'album
    DL    — booléen ("True"/"False") indiquant si l'album est déjà téléchargé

Le service skippe automatiquement toute ligne où `DL=True`. Quand un album est
téléchargé avec succès, sa ligne est mise à jour à `DL=True` (écriture atomique
via `tmp + replace` pour ne pas corrompre le CSV en cas d'interruption).

Sortie audio : par défaut `~/mes_projets/Musique_Jeux_Video/datas/`. Configurable
via la variable d'env `KHINSIDER_OUTPUT`.

Privilégie le format FLAC, sinon MP3.
"""
import os
import time
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "Musique_Jeux_Video"
INPUT_CSV = DATA_DIR / "albums_khinsider.csv"

# Audio téléchargé en dehors du repo (volumineux). Configurable via env var.
DEFAULT_OUTPUT = Path.home() / "mes_projets" / "Musique_Jeux_Video" / "datas"
OUTPUT_DIR = Path(os.environ.get("KHINSIDER_OUTPUT", str(DEFAULT_OUTPUT)))

BASE_URL = "https://downloads.khinsider.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# CSV state — source de vérité du "qu'est-ce qui est déjà téléchargé"
# ---------------------------------------------------------------------------

def _coerce_bool(val) -> bool:
    """Tolère True/False/'True'/'False'/1/0/yes/no et autres variantes humaines."""
    if isinstance(val, bool):
        return val
    if pd.isna(val):
        return False
    s = str(val).strip().lower()
    return s in {"true", "1", "yes", "y", "oui"}


def load_state() -> pd.DataFrame:
    """Charge le CSV d'input ; ajoute la colonne DL si elle manque (legacy)."""
    if not INPUT_CSV.exists():
        raise FileNotFoundError(
            f"Input file {INPUT_CSV} not found.\n"
            f"Crée-le avec deux colonnes : url, DL — par exemple :\n"
            f"    url,DL\n"
            f"    https://downloads.khinsider.com/.../cuphead,False\n"
        )
    df = pd.read_csv(INPUT_CSV)
    if "url" not in df.columns:
        raise ValueError(f"{INPUT_CSV} doit contenir une colonne 'url'.")
    if "DL" not in df.columns:
        # Compat avec l'ancien format (sans DL) — on suppose tout à False
        df["DL"] = False
    df["DL"] = df["DL"].map(_coerce_bool)
    df["url"] = df["url"].astype(str).str.strip()
    return df


def save_state(df: pd.DataFrame) -> None:
    """Écriture atomique : tmp + replace, pour ne pas corrompre en cas de crash."""
    tmp = INPUT_CSV.with_suffix(".csv.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(INPUT_CSV)


def mark_downloaded(df: pd.DataFrame, url: str) -> pd.DataFrame:
    """Marque l'URL comme téléchargée et sauvegarde le CSV."""
    df.loc[df["url"] == url, "DL"] = True
    save_state(df)
    return df


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def get_soup(url: str) -> BeautifulSoup | None:
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None


def download_file(url: str, filepath: Path) -> bool:
    try:
        if filepath.exists():
            print(f"  Skipping {filepath.name} (already exists)")
            return True

        print(f"  Downloading to {filepath}...")
        response = requests.get(url, stream=True, headers=HEADERS)
        response.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print("  Done.")
        return True
    except Exception as e:
        print(f"  Error downloading {url}: {e}")
        return False


# ---------------------------------------------------------------------------
# Khinsider parsing
# ---------------------------------------------------------------------------

def process_song(song_page_url: str, album_dir: Path) -> bool:
    """Extrait le lien de téléchargement et lance le download (FLAC > MP3)."""
    soup = get_soup(song_page_url)
    if not soup:
        return False

    flac_link = None
    mp3_link = None
    for a in soup.find_all("a"):
        text = a.text.lower()
        if "click here to download as flac" in text:
            flac_link = a["href"]
        elif "click here to download as mp3" in text:
            mp3_link = a["href"]

    download_url = flac_link or mp3_link
    if not download_url:
        print("    No download link found (FLAC or MP3).")
        return False

    filename = unquote(download_url.split("/")[-1])
    filepath = album_dir / filename

    print(f"    Target: {filename} ({'FLAC' if flac_link else 'MP3'})")
    return download_file(download_url, filepath)


def process_album(album_url: str) -> bool:
    """Télécharge un album entier. Retourne True si tous les morceaux sont OK."""
    print(f"\nProcessing Album: {album_url}")
    soup = get_soup(album_url)
    if not soup:
        return False

    album_title_tag = soup.find("h2")
    album_name = (
        album_title_tag.text.strip() if album_title_tag
        else album_url.rstrip("/").split("/")[-1]
    )
    print(f"Album Name: {album_name}")

    album_dir = OUTPUT_DIR / album_name
    album_dir.mkdir(parents=True, exist_ok=True)

    songlist_table = soup.find("table", id="songlist")
    if not songlist_table:
        print("  Could not find song list table.")
        return False

    song_page_urls: list[str] = []
    for row in songlist_table.find_all("tr"):
        if row.get("id") == "songlist_header":
            continue
        name_cell = row.find("td", class_="clickable-row")
        if name_cell:
            link = name_cell.find("a")
            if link and "href" in link.attrs:
                song_url = link["href"]
                if not song_url.startswith("http"):
                    song_url = BASE_URL + song_url
                song_page_urls.append(song_url)

    song_page_urls = sorted(set(song_page_urls))
    print(f"Found {len(song_page_urls)} tracks.")

    all_ok = True
    for i, song_url in enumerate(song_page_urls, 1):
        print(f"  Track {i}/{len(song_page_urls)}: {song_url}")
        if not process_song(song_url, album_dir):
            all_ok = False
        time.sleep(1)  # courtesy delay
    return all_ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_state()

    todo = df[~df["DL"]]
    print(f"Total : {len(df)} albums | déjà téléchargés : {df['DL'].sum()} | à traiter : {len(todo)}")
    print(f"Output : {OUTPUT_DIR}")

    if todo.empty:
        print("Rien à télécharger. Tout est déjà à DL=True.")
        return

    for _, row in todo.iterrows():
        url = row["url"]
        ok = process_album(url)
        if ok:
            df = mark_downloaded(df, url)
            print(f"  ✅ Marqué DL=True pour : {url}")
        else:
            print(f"  ⚠️  Échec partiel — {url} reste DL=False (sera retenté au prochain run)")


if __name__ == "__main__":
    main()
