from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import main


class DatabaseContextTests(unittest.TestCase):
    def test_exception_rolls_back_transaction_and_closes_connection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "test.db"

            with patch.object(main, "DB_PATH", str(db_path)):
                connection: sqlite3.Connection | None = None

                with self.assertRaisesRegex(RuntimeError, "database operation failed"):
                    with main.db() as connection:
                        connection.execute(
                            """
                            INSERT INTO screenplays (
                                id, file_name, mime_type, status, page_count,
                                report_json, trace_json, key_art_url, decision,
                                decision_at, created_at, source_text
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                "rolled-back",
                                "failure.txt",
                                "text/plain",
                                "analyzing",
                                None,
                                None,
                                "[]",
                                None,
                                None,
                                None,
                                "2026-09-05T00:00:00+00:00",
                                "Failure path",
                            ),
                        )
                        raise RuntimeError("database operation failed")

                self.assertIsNotNone(connection)
                with self.assertRaisesRegex(
                    sqlite3.ProgrammingError, "closed database"
                ):
                    connection.execute("SELECT 1")

                with closing(sqlite3.connect(db_path)) as verification_connection:
                    row = verification_connection.execute(
                        "SELECT id FROM screenplays WHERE id = ?", ("rolled-back",)
                    ).fetchone()
                self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()