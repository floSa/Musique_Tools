"""
Stockage SQLite des artistes similaires Spotify.

Schéma (aligné sur Artistes_Similaires_LastFM, avec un champ supplémentaire
`source_artist_id` pour l'ID Spotify de l'artiste source — utile pour
construire l'iframe embed côté Recommandation) :

    artists(
        source_artist TEXT PRIMARY KEY,
        source_artist_id TEXT,
        similar_artists TEXT,    -- JSON: [{"name": ..., "id": ..., "rank": ...}]
        tags TEXT DEFAULT '[]',  -- toujours [] côté Spotify (Spotify n'expose pas de tags),
                                 -- gardé pour symétrie avec Last.fm
        status TEXT DEFAULT 'success'
    )

L'interface publique (`artist_exists`, `save_result`, `get_all_results`) est
volontairement identique à celle de la classe Last.fm pour faciliter la
maintenance des deux scrapers.
"""
import json
import sqlite3
from pathlib import Path

DB_PATH = (
    Path(__file__).parent.parent.parent
    / "data" / "Artistes_Similaires_Spotify" / "similar_artists.db"
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
                tags TEXT DEFAULT '[]',
                status TEXT DEFAULT 'success'
            )
            """
        )
        self.conn.commit()

    def artist_exists(self, artist_name: str) -> bool:
        """True si l'artiste a une entrée 'success' avec au moins un similaire.

        On considère qu'un artiste avec entrée vide ([]) reste à re-scraper
        (peut-être qu'il n'avait pas encore généré ses recos chez Spotify).
        """
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
    ) -> None:
        """Insère ou remplace une entrée.

        Args:
            artist_name : nom canonique du source artist (clé primaire)
            source_artist_id : ID Spotify (22 chars) ou "" si inconnu
            similar_artists : liste [{"name": ..., "id": ..., "rank": ...}]
        """
        self.conn.execute(
            """
            INSERT INTO artists (source_artist, source_artist_id, similar_artists, tags, status)
            VALUES (?, ?, ?, '[]', 'success')
            ON CONFLICT(source_artist) DO UPDATE SET
                source_artist_id = excluded.source_artist_id,
                similar_artists = excluded.similar_artists,
                status = 'success'
            """,
            (artist_name, source_artist_id or "", json.dumps(similar_artists)),
        )
        self.conn.commit()

    def get_all_results(self) -> list[tuple[str, str, str]]:
        """Retourne [(source_artist, source_artist_id, similar_json), ...]."""
        cur = self.conn.execute(
            "SELECT source_artist, source_artist_id, similar_artists "
            "FROM artists WHERE status = 'success'"
        )
        return cur.fetchall()

    def get_processed_artists(self) -> set[str]:
        """Set des artistes déjà traités (pour skip au démarrage)."""
        cur = self.conn.execute("SELECT source_artist FROM artists")
        return {row[0] for row in cur.fetchall()}

    def close(self) -> None:
        self.conn.close()
