"""Persistance SQLite locale pour l'historique Fautexprimer.

La base et les audios sont conservés dans le dossier de données utilisateur. Ce module
ne contient aucune route Flask et ne reçoit aucun secret : il peut être testé
indépendamment avec un dossier temporaire.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS synthesis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    voice TEXT NOT NULL,
    text TEXT NOT NULL,
    speed REAL NOT NULL,
    gain REAL NOT NULL,
    format TEXT NOT NULL,
    filename TEXT NOT NULL UNIQUE,
    transcript TEXT NOT NULL DEFAULT '',
    reference_voice_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_synthesis_created
    ON synthesis_history(created_at DESC);

CREATE TABLE IF NOT EXISTS voice_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    original_name TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('uploaded', 'recorded')),
    filename TEXT NOT NULL UNIQUE,
    format TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_created
    ON voice_library(created_at DESC);
"""


class HistoryStore:
    """Petit dépôt SQLite thread-safe avec une connexion par opération."""

    def __init__(self, data_dir: Path, max_syntheses: int = 500, max_voices: int = 200):
        self.data_dir = data_dir.resolve()
        self.database_path = self.data_dir / "fautexprimer.db"
        self.history_dir = self.data_dir / "history"
        self.synthesis_dir = self.history_dir / "synthesis"
        self.voice_dir = self.data_dir / "voices"
        self.max_syntheses = max(1, int(max_syntheses))
        self.max_voices = max(1, int(max_voices))
        self._init_lock = threading.Lock()
        self._initialised = False
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialised:
                return
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.synthesis_dir.mkdir(parents=True, exist_ok=True)
            self.voice_dir.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.executescript(SCHEMA)
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(synthesis_history)")
                }
                if "reference_voice_id" not in columns:
                    connection.execute("ALTER TABLE synthesis_history ADD COLUMN reference_voice_id INTEGER")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
            self._initialised = True

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _safe_filename(prefix: str, extension: str) -> str:
        suffix = extension.lower().lstrip(".") or "bin"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}.{suffix}"

    def _validate_audio_path(self, path: Path) -> Path:
        resolved = path.resolve()
        roots = (self.synthesis_dir.resolve(), self.voice_dir.resolve())
        if not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError("Chemin audio hors du dossier de donnees utilisateur")
        return resolved

    def add_synthesis(
        self,
        source_path: Path,
        *,
        provider: str,
        voice: str,
        text: str,
        speed: float,
        gain: float,
        audio_format: str,
        transcript: str = "",
        reference_voice_id: int | None = None,
    ) -> dict[str, Any]:
        source = source_path.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        extension = source.suffix or f".{audio_format.lstrip('.')}"
        filename = self._safe_filename("synthese", extension)
        destination = self.synthesis_dir / filename
        shutil.copy2(source, destination)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO synthesis_history
                        (created_at, provider, voice, text, speed, gain, format, filename,
                         transcript, reference_voice_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._now(), provider, voice, text, float(speed), float(gain),
                        audio_format.lstrip("."), filename, transcript, reference_voice_id,
                    ),
                )
                record_id = int(cursor.lastrowid)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        self._trim_syntheses()
        return self.get_synthesis(record_id)  # type: ignore[return-value]

    def _trim_syntheses(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT filename FROM synthesis_history ORDER BY id DESC LIMIT -1 OFFSET ?",
                (self.max_syntheses,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "DELETE FROM synthesis_history WHERE filename = ?", (row["filename"],)
                )
        for row in rows:
            (self.synthesis_dir / row["filename"]).unlink(missing_ok=True)

    def get_synthesis(self, record_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM synthesis_history WHERE id = ?", (int(record_id),)
            ).fetchone()
        return self._row(row)

    def list_syntheses(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM synthesis_history ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def synthesis_audio(self, record_id: int) -> Path | None:
        record = self.get_synthesis(record_id)
        if record is None:
            return None
        path = self._validate_audio_path(self.synthesis_dir / record["filename"])
        return path if path.is_file() else None

    def delete_synthesis(self, record_id: int) -> bool:
        record = self.get_synthesis(record_id)
        if record is None:
            return False
        with self._connect() as connection:
            connection.execute("DELETE FROM synthesis_history WHERE id = ?", (int(record_id),))
        path = self.synthesis_dir / record["filename"]
        path.unlink(missing_ok=True)
        return True

    def clear_syntheses(self) -> int:
        records = self.list_syntheses(limit=500)
        with self._connect() as connection:
            connection.execute("DELETE FROM synthesis_history")
        for record in records:
            (self.synthesis_dir / record["filename"]).unlink(missing_ok=True)
        return len(records)

    def add_voice(
        self,
        source_path: Path,
        *,
        name: str,
        original_name: str,
        source: str,
        audio_format: str,
    ) -> dict[str, Any]:
        if source not in {"uploaded", "recorded"}:
            raise ValueError("Type de voix invalide")
        source_file = source_path.resolve()
        if not source_file.is_file():
            raise FileNotFoundError(source_file)
        extension = source_file.suffix or f".{audio_format.lstrip('.')}"
        filename = self._safe_filename("voix", extension)
        destination = self.voice_dir / filename
        shutil.copy2(source_file, destination)
        clean_name = " ".join(name.strip().split())[:120] or Path(original_name).stem[:120]
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO voice_library
                        (created_at, name, original_name, source, filename, format)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._now(), clean_name, Path(original_name).name[:255], source,
                        filename, audio_format.lstrip("."),
                    ),
                )
                record_id = int(cursor.lastrowid)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        self._trim_voices()
        return self.get_voice(record_id)  # type: ignore[return-value]

    def add_voice_bytes(
        self,
        audio: bytes,
        *,
        name: str,
        original_name: str,
        source: str,
        audio_format: str,
    ) -> dict[str, Any]:
        """Enregistre un clip reçu en mémoire sans fichier temporaire intermédiaire."""
        if source not in {"uploaded", "recorded"}:
            raise ValueError("Type de voix invalide")
        if not audio:
            raise ValueError("Le clip audio est vide")
        extension = f".{audio_format.lstrip('.') or 'bin'}"
        filename = self._safe_filename("voix", extension)
        destination = self.voice_dir / filename
        destination.write_bytes(audio)
        clean_name = " ".join(name.strip().split())[:120] or Path(original_name).stem[:120]
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO voice_library
                        (created_at, name, original_name, source, filename, format)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._now(), clean_name, Path(original_name).name[:255], source,
                        filename, extension.lstrip("."),
                    ),
                )
                record_id = int(cursor.lastrowid)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        self._trim_voices()
        return self.get_voice(record_id)  # type: ignore[return-value]

    def _trim_voices(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT filename FROM voice_library ORDER BY id DESC LIMIT -1 OFFSET ?",
                (self.max_voices,),
            ).fetchall()
            for row in rows:
                connection.execute("DELETE FROM voice_library WHERE filename = ?", (row["filename"],))
        for row in rows:
            (self.voice_dir / row["filename"]).unlink(missing_ok=True)

    def get_voice(self, record_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM voice_library WHERE id = ?", (int(record_id),)
            ).fetchone()
        return self._row(row)

    def list_voices(self, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM voice_library ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def voice_audio(self, record_id: int) -> Path | None:
        record = self.get_voice(record_id)
        if record is None:
            return None
        path = self._validate_audio_path(self.voice_dir / record["filename"])
        return path if path.is_file() else None

    def rename_voice(self, record_id: int, name: str) -> dict[str, Any] | None:
        clean_name = " ".join(name.strip().split())[:120]
        if not clean_name:
            raise ValueError("Le nom de la voix est vide")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE voice_library SET name = ? WHERE id = ?", (clean_name, int(record_id))
            )
        return self.get_voice(record_id) if cursor.rowcount else None

    def delete_voice(self, record_id: int) -> bool:
        record = self.get_voice(record_id)
        if record is None:
            return False
        with self._connect() as connection:
            connection.execute("DELETE FROM voice_library WHERE id = ?", (int(record_id),))
            connection.execute(
                "UPDATE synthesis_history SET reference_voice_id = NULL WHERE reference_voice_id = ?",
                (int(record_id),),
            )
        (self.voice_dir / record["filename"]).unlink(missing_ok=True)
        return True

    def clear_voices(self) -> int:
        records = self.list_voices(limit=500)
        with self._connect() as connection:
            connection.execute("DELETE FROM voice_library")
            connection.execute("UPDATE synthesis_history SET reference_voice_id = NULL")
        for record in records:
            (self.voice_dir / record["filename"]).unlink(missing_ok=True)
        return len(records)

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            syntheses = int(connection.execute("SELECT COUNT(*) FROM synthesis_history").fetchone()[0])
            voices = int(connection.execute("SELECT COUNT(*) FROM voice_library").fetchone()[0])
        synthesis_bytes = sum(path.stat().st_size for path in self.synthesis_dir.glob("*") if path.is_file())
        voice_bytes = sum(path.stat().st_size for path in self.voice_dir.glob("*") if path.is_file())
        return {
            "syntheses": syntheses,
            "voices": voices,
            "synthesisBytes": synthesis_bytes,
            "voiceBytes": voice_bytes,
        }
