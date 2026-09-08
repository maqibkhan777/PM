"""Database connection management for SQLite."""

import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator, Optional
from app.config.settings import settings
from app.utils.logger import logger


class DatabaseManager:
    """Thread-safe SQLite database manager."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._lock = threading.Lock()

    @property
    def db_path(self) -> str:
        if self._db_path:
            return self._db_path
        return settings.get_database_path()

    def get_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection configured with Row factory and foreign keys."""
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def session(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing a transactional database session."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database transaction rolled back due to error: {e}")
            raise
        finally:
            conn.close()

    @contextmanager
    def cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager providing a transactional cursor."""
        with self.session() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()


# Global database manager instance
db_manager = DatabaseManager()
