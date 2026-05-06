"""
Stockage SQLite des artistes similaires Qobuz Play.

Schéma aligné sur Last.fm + Spotify, avec deux champs spécifiques à Qobuz :
- `source_artist_id` (entier dans l'URL, ex. `3586950` pour Worakls)
- `portrait` (texte de présentation extrait de la page artiste)

    artists(
        source_artist TEXT PRIMARY KEY,
        source_artist_id TEXT,
        similar_artists TEXT,    -- JSON: [{"name": ..., "id": ..., "rank": ...}]
        portrait TEXT,           -- biographie / présentation Qobuz
        tags TEXT DEFAULT '[]',  -- pas exposé côté Qobuz, gardé pour symétrie
        status TEXT DEFAULT 'success'
    )

Interface publique alignée sur les services LastFM/Spotify pour faciliter la
maintenance des trois scrapers.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "Artistes_Similaires_Qobuz" / "similar_artists.db"
)


class Database:
    def __init__(self, db_path: Path | None = None):
        path = Path(db_path) if db_path else DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self._create_table()

    def _create_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artists (
                source_artist TEXT PRIMARY KEY,
                source_artist_id TEXT,
                similar_artists TEXT,
                portrait TEXT,
                tags TEXT DEFAULT '[]',
                status TEXT DEFAULT 'success'
            )
            """
        )
        self.conn.commit()

    def artist_exists(self, artist_name: str) -> bool:
        """True si l'artiste a une entrée 'success' avec au moins un similaire."""
        row = self.conn.execute(
            "SELECT similar_artists FROM artists WHERE source_artist = ? AND status = 'success'",
            (artist_name,),
        ).fetchone()
        if not row:
            return False
        try:
            data = json.loads(row[0]) if row[0] else []
            return isinstance(data, list) and len(data) > 0
        except (json.JSONDecodeError, TypeError):
            return False

    def save_result(
        self,
        artist_name: str,
        source_artist_id: str,
        similar_artists: list[dict],
        portrait: str = "",
    ) -> None:
        """Insère ou remplace une entrée."""
        self.conn.execute(
            """
            INSERT INTO artists (source_artist, source_artist_id, similar_artists, portrait, tags, status)
            VALUES (?, ?, ?, ?, '[]', 'success')
            ON CONFLICT(source_artist) DO UPDATE SET
                source_artist_id = excluded.source_artist_id,
                similar_artists = excluded.similar_artists,
                portrait = excluded.portrait,
                status = 'success'
            """,
            (artist_name, source_artist_id or "", json.dumps(similar_artists), portrait or ""),
        )
        self.conn.commit()

    def get_all_results(self) -> list[tuple[str, str, str, str]]:
        """Retourne [(source_artist, source_artist_id, similar_json, portrait), ...]."""
        cur = self.conn.execute(
            "SELECT source_artist, source_artist_id, similar_artists, portrait "
            "FROM artists WHERE status = 'success'"
        )
        return cur.fetchall()

    def get_processed_artists(self) -> set[str]:
        """Set des artistes déjà traités (skip au démarrage)."""
        cur = self.conn.execute("SELECT source_artist FROM artists")
        return {row[0] for row in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()
