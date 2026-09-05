from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main


REPORT = {
    "logline": "A producer must choose between safety and the film of a lifetime.",
    "synopsis": "A complete saved coverage report.",
    "strengths": ["Clear stakes"],
    "weaknesses": ["Second act pacing"],
    "comparableTitles": [{"title": "The Player", "reason": "Industry pressure"}],
    "recommendation": "Consider",
}
TRACE = [
    {
        "id": "trace-1",
        "label": "Drafted recommendation",
        "detail": "Coverage complete",
        "status": "complete",
        "createdAt": "2026-09-05T00:00:00+00:00",
    }
]


class KeyArtRetryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db_path_patch = patch.object(main, "DB_PATH", str(self.db_path))
        self.db_path_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        self.client.close()
        main.key_art_retries_in_progress.clear()
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def insert_screenplay(
        self,
        screenplay_id: str,
        *,
        status: str = "approved",
        report: dict | None = REPORT,
        key_art_url: str | None = None,
    ) -> None:
        with main.db() as connection:
            connection.execute(
                """
                INSERT INTO screenplays (
                    id, file_name, mime_type, status, page_count, report_json,
                    trace_json, key_art_url, decision, decision_at, created_at,
                    source_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    screenplay_id,
                    "unchanged.txt",
                    "text/plain",
                    status,
                    99,
                    json.dumps(report) if report else None,
                    json.dumps(TRACE),
                    key_art_url,
                    "approved" if status == "approved" else None,
                    "2026-09-05T00:01:00+00:00" if status == "approved" else None,
                    "2026-09-05T00:00:00+00:00",
                    "Original screenplay text",
                ),
            )

    def raw_row(self, screenplay_id: str) -> tuple:
        with sqlite3.connect(self.db_path) as connection:
            return connection.execute(
                """
                SELECT file_name, mime_type, status, page_count, report_json,
                       trace_json, decision, decision_at, created_at, source_text,
                       key_art_url
                FROM screenplays WHERE id = ?
                """,
                (screenplay_id,),
            ).fetchone()

    def test_quota_failed_retry_preserves_every_completed_coverage_field(self) -> None:
        self.insert_screenplay("quota-failure")
        before = self.raw_row("quota-failure")

        with patch.object(
            main,
            "generate_key_art",
            AsyncMock(side_effect=RuntimeError("429 RESOURCE_EXHAUSTED quota")),
        ):
            response = self.client.post("/api/screenplays/quota-failure/key-art/retry")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "Gemini image quota is exhausted. Coverage remains ready."},
        )
        self.assertEqual(self.raw_row("quota-failure"), before)

    def test_successful_retry_changes_only_key_art_url(self) -> None:
        self.insert_screenplay("success")
        before_response = self.client.get("/api/screenplays/success").json()
        before_row = self.raw_row("success")
        generated_url = "data:image/png;base64,a2V5LWFydA=="

        async def save_key_art(screenplay_id: str, source_text: str, report: dict) -> None:
            self.assertEqual(source_text, "Original screenplay text")
            self.assertEqual(report, REPORT)
            main.update_record(screenplay_id, keyArtUrl=generated_url)

        with patch.object(main, "generate_key_art", side_effect=save_key_art):
            response = self.client.post("/api/screenplays/success/key-art/retry")

        self.assertEqual(response.status_code, 200)
        expected_response = deepcopy(before_response)
        expected_response["keyArtUrl"] = generated_url
        self.assertEqual(response.json(), expected_response)
        self.assertEqual(self.raw_row("success")[:-1], before_row[:-1])
        self.assertEqual(self.raw_row("success")[-1], generated_url)

    def test_retry_rejects_unfinished_coverage(self) -> None:
        self.insert_screenplay("unfinished", status="analyzing", report=None)

        response = self.client.post("/api/screenplays/unfinished/key-art/retry")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "Coverage must be ready before retrying key art."},
        )

    def test_retry_rejects_record_that_already_has_key_art(self) -> None:
        existing_url = "data:image/png;base64,ZXhpc3Rpbmc="
        self.insert_screenplay("already-generated", key_art_url=existing_url)

        response = self.client.post("/api/screenplays/already-generated/key-art/retry")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(), {"detail": "Key art has already been generated."}
        )
        self.assertEqual(self.raw_row("already-generated")[-1], existing_url)

    def test_concurrent_retries_generate_only_one_image(self) -> None:
        self.insert_screenplay("concurrent")
        generation_started = threading.Event()
        release_generation = threading.Event()
        call_count = 0
        call_count_lock = threading.Lock()

        async def slow_key_art(
            screenplay_id: str, source_text: str, report: dict
        ) -> None:
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            generation_started.set()
            await main.asyncio.to_thread(release_generation.wait)
            main.update_record(
                screenplay_id, keyArtUrl="data:image/png;base64,Y29uY3VycmVudA=="
            )

        with patch.object(main, "generate_key_art", side_effect=slow_key_art):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    self.client.post,
                    "/api/screenplays/concurrent/key-art/retry",
                )
                self.assertTrue(generation_started.wait(timeout=2))
                second_response = self.client.post(
                    "/api/screenplays/concurrent/key-art/retry"
                )
                release_generation.set()
                first_response = first.result(timeout=2)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 409)
        self.assertEqual(
            second_response.json(),
            {
                "detail": (
                    "Key art generation is already in progress for this screenplay."
                )
            },
        )
        self.assertEqual(call_count, 1)


if __name__ == "__main__":
    unittest.main()