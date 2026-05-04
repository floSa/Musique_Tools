import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent.parent / "data" / "Artistes_Similaires_LastFM" / "similar_artists.db"


class Database:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self._create_table()

    def _create_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS artists (
                source_artist TEXT PRIMARY KEY,
                similar_artists TEXT,
                tags TEXT,
                status TEXT DEFAULT 'success'
            )
        """)
        self.conn.commit()

    def artist_exists(self, artist_name: str) -> bool:
        row = self.conn.execute(
            "SELECT similar_artists FROM artists WHERE source_artist = ? AND status = 'success'",
            (artist_name,)
        ).fetchone()
        if not row:
            return False
        try:
            data = json.loads(row[0])
            if isinstance(data, list):
                return len(data) > 0
            if isinstance(data, dict):
                return len(data.get("similar_artists", [])) > 0
        except (json.JSONDecodeError, TypeError):
            pass
        return False

    def save_result(self, artist_name: str, data: dict):
        similar = data.get("similar_artists", [])
        tags = data.get("tags", [])
        self.conn.execute(
            """
            INSERT INTO artists (source_artist, similar_artists, tags, status)
            VALUES (?, ?, ?, 'success')
            ON CONFLICT(source_artist) DO UPDATE SET
                similar_artists = excluded.similar_artists,
                tags = excluded.tags,
                status = 'success'
            """,
            (artist_name, json.dumps(similar), json.dumps(tags))
        )
        self.conn.commit()

    def log_failure(self, artist_name: str, error_message: str):
        pass

    def get_all_results(self):
        cursor = self.conn.execute(
            "SELECT source_artist, similar_artists, tags FROM artists WHERE status = 'success'"
        )
        return cursor.fetchall()

    def close(self):
        self.conn.close()
