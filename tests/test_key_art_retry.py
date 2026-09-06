from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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
        with closing(sqlite3.connect(self.db_path)) as connection:
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
        generate_key_art = AsyncMock(
            side_effect=RuntimeError("429 RESOURCE_EXHAUSTED quota")
        )

        with patch.object(main, "generate_key_art", generate_key_art):
            first_response = self.client.post(
                "/api/screenplays/quota-failure/key-art/retry"
            )
            self.assertTrue(
                main.claim_key_art_retry("quota-failure", "regression-check")
            )
            main.release_key_art_retry("quota-failure", "regression-check")
            second_response = self.client.post(
                "/api/screenplays/quota-failure/key-art/retry"
            )

        for response in (first_response, second_response):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.json(),
                {"detail": "Gemini image quota is exhausted. Coverage remains ready."},
            )
        self.assertEqual(generate_key_art.await_count, 2)
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

    def test_independent_claim_owners_share_one_database_lease(self) -> None:
        self.insert_screenplay("shared-lease")

        self.assertTrue(main.claim_key_art_retry("shared-lease", "server-a"))
        self.assertFalse(main.claim_key_art_retry("shared-lease", "server-b"))

        main.release_key_art_retry("shared-lease", "server-a")
        self.assertTrue(main.claim_key_art_retry("shared-lease", "server-b"))

    def test_expired_claim_can_be_recovered_without_old_owner_releasing_it(self) -> None:
        self.insert_screenplay("abandoned-lease")
        claimed_at = datetime.now(timezone.utc) - main.KEY_ART_CLAIM_TTL - timedelta(
            seconds=1
        )

        self.assertTrue(
            main.claim_key_art_retry(
                "abandoned-lease", "abandoned-server", claimed_at=claimed_at
            )
        )
        self.assertTrue(main.claim_key_art_retry("abandoned-lease", "replacement-server"))

        main.release_key_art_retry("abandoned-lease", "abandoned-server")
        self.assertFalse(main.claim_key_art_retry("abandoned-lease", "third-server"))

    def test_delayed_request_rechecks_saved_art_after_claiming(self) -> None:
        self.insert_screenplay("stale-precheck")
        first_claim_blocked = threading.Event()
        allow_first_claim = threading.Event()
        claim_call_count = 0
        generation_count = 0
        count_lock = threading.Lock()
        original_claim = main.claim_key_art_retry

        def delay_first_claim(screenplay_id: str, owner_id: str) -> bool:
            nonlocal claim_call_count
            with count_lock:
                claim_call_count += 1
                call_number = claim_call_count
            if call_number == 1:
                first_claim_blocked.set()
                self.assertTrue(allow_first_claim.wait(timeout=2))
            return original_claim(screenplay_id, owner_id)

        async def save_key_art(
            screenplay_id: str, source_text: str, report: dict
        ) -> None:
            nonlocal generation_count
            with count_lock:
                generation_count += 1
            main.update_record(
                screenplay_id, keyArtUrl="data:image/png;base64,c3RhbGUtcmFjZQ=="
            )

        with (
            patch.object(main, "claim_key_art_retry", side_effect=delay_first_claim),
            patch.object(main, "generate_key_art", side_effect=save_key_art),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            delayed = executor.submit(
                self.client.post,
                "/api/screenplays/stale-precheck/key-art/retry",
            )
            self.assertTrue(first_claim_blocked.wait(timeout=2))
            winner_response = self.client.post(
                "/api/screenplays/stale-precheck/key-art/retry"
            )
            allow_first_claim.set()
            delayed_response = delayed.result(timeout=2)

        self.assertEqual(winner_response.status_code, 200)
        self.assertEqual(delayed_response.status_code, 409)
        self.assertEqual(
            delayed_response.json(), {"detail": "Key art has already been generated."}
        )
        self.assertEqual(generation_count, 1)

    def test_heartbeat_keeps_long_running_generation_claimed(self) -> None:
        self.insert_screenplay("long-running")
        generation_started = threading.Event()
        release_generation = threading.Event()
        generation_count = 0
        count_lock = threading.Lock()

        async def slow_key_art(
            screenplay_id: str, source_text: str, report: dict
        ) -> None:
            nonlocal generation_count
            with count_lock:
                generation_count += 1
            generation_started.set()
            await main.asyncio.to_thread(release_generation.wait)
            main.update_record(
                screenplay_id, keyArtUrl="data:image/png;base64,bG9uZy1ydW5uaW5n"
            )

        with (
            patch.object(main, "KEY_ART_CLAIM_TTL", timedelta(milliseconds=90)),
            patch.object(main, "generate_key_art", side_effect=slow_key_art),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(
                self.client.post,
                "/api/screenplays/long-running/key-art/retry",
            )
            self.assertTrue(generation_started.wait(timeout=2))
            threading.Event().wait(0.2)
            second_response = self.client.post(
                "/api/screenplays/long-running/key-art/retry"
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
        self.assertEqual(generation_count, 1)

    def test_heartbeat_cleanup_failure_releases_claim_for_next_retry(self) -> None:
        self.insert_screenplay("heartbeat-cleanup-failure")
        generated_url = "data:image/png;base64,cmV0cnktYWZ0ZXItY2xlYW51cA=="
        generation_count = 0

        async def generate_on_second_attempt(
            screenplay_id: str, source_text: str, report: dict
        ) -> None:
            nonlocal generation_count
            generation_count += 1
            if generation_count == 1:
                raise RuntimeError("image generation failed")
            main.update_record(screenplay_id, keyArtUrl=generated_url)

        heartbeat = AsyncMock(
            side_effect=[RuntimeError("lease renewal cleanup failed"), None]
        )
        with (
            patch.object(main, "maintain_key_art_retry_claim", heartbeat),
            patch.object(main, "generate_key_art", side_effect=generate_on_second_attempt),
        ):
            first_response = self.client.post(
                "/api/screenplays/heartbeat-cleanup-failure/key-art/retry"
            )
            second_response = self.client.post(
                "/api/screenplays/heartbeat-cleanup-failure/key-art/retry"
            )

        self.assertEqual(first_response.status_code, 503)
        self.assertEqual(
            first_response.json(),
            {"detail": "Key art could not be generated. Coverage remains ready."},
        )
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(second_response.json()["keyArtUrl"], generated_url)
        self.assertEqual(generation_count, 2)

    def test_failures_log_distinct_events_without_sensitive_context(self) -> None:
        self.insert_screenplay("structured-logging")
        heartbeat = AsyncMock(side_effect=RuntimeError("private heartbeat detail"))

        with (
            patch.object(main, "maintain_key_art_retry_claim", heartbeat),
            patch.object(
                main,
                "generate_key_art",
                AsyncMock(side_effect=RuntimeError("private provider detail")),
            ),
            patch.object(
                main,
                "release_key_art_retry",
                side_effect=RuntimeError("private database detail"),
            ),
            self.assertLogs(main.logger, level="ERROR") as captured,
        ):
            with self.assertRaises(RuntimeError) as raised:
                main.asyncio.run(main.retry_screenplay_key_art("structured-logging"))

        self.assertEqual(str(raised.exception), "private database detail")
        records = captured.records
        self.assertEqual(
            [record.event for record in records],
            [
                "key_art_image_generation_failed",
                "key_art_retry_heartbeat_failed",
                "key_art_retry_claim_release_failed",
            ],
        )
        for record in records:
            self.assertEqual(record.screenplay_id, "structured-logging")
            self.assertEqual(record.error_type, "RuntimeError")
            self.assertNotIn("private", record.getMessage())


if __name__ == "__main__":
    unittest.main()