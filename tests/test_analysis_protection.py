from __future__ import annotations

import base64
import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter

import main


class AnalysisProtectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            main, "DB_PATH", str(Path(self.temp_dir.name) / "test.db")
        )
        self.db_patch.start()
        main.rate_limit_events.clear()
        main.jobs.clear()
        main.job_owners.clear()
        main.analysis_slots.clear()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        for job in list(main.jobs.values()):
            job.cancel()
        self.client.close()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_oversized_request_body_is_rejected_before_parsing(self) -> None:
        response = self.client.post(
            "/api/screenplays/analyze",
            content=b'{"fileName":"x","mimeType":"text/plain","content":"' + b"x" * 8_400_000,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_decoded_text_limit_is_enforced(self) -> None:
        with patch.object(main, "MAX_DECODED_FILE_BYTES", 10):
            response = self.client.post(
                "/api/screenplays/analyze",
                json={"fileName": "large.txt", "mimeType": "text/plain", "content": "x" * 11},
                headers={"x-user-id": "large-user"},
            )
        self.assertEqual(response.status_code, 413)

    def test_extracted_text_limit_is_enforced(self) -> None:
        with patch.object(main, "MAX_EXTRACTED_TEXT_CHARS", 10):
            response = self.client.post(
                "/api/screenplays/analyze",
                json={
                    "fileName": "too-long.txt",
                    "mimeType": "text/plain",
                    "content": "x" * 11,
                },
            )
        self.assertEqual(response.status_code, 413)

    def test_malformed_pdf_is_rejected_without_parser_details(self) -> None:
        response = self.client.post(
            "/api/screenplays/analyze",
            json={"fileName": "bad.pdf", "mimeType": "application/pdf", "content": "bm90IGEgcGRm"},
            headers={"x-user-id": "pdf-user"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "The uploaded file is not a valid PDF."})

    def test_repeated_submission_does_not_start_a_second_job(self) -> None:
        owner_key = f"ip:{hashlib.sha256(b'testclient').hexdigest()}"
        content = "FADE IN"
        submission_hash = hashlib.sha256(
            b"text/plain\0" + content.encode()
        ).hexdigest()
        with main.db() as connection:
            connection.execute(
                """
                INSERT INTO screenplays (
                    id, file_name, mime_type, status, page_count, trace_json,
                    created_at, source_text, owner_key, submission_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "existing",
                    "script.txt",
                    "text/plain",
                    "failed",
                    1,
                    "[]",
                    main.now(),
                    content,
                    owner_key,
                    submission_hash,
                ),
            )
        with patch.object(main, "run_analysis", AsyncMock()):
            response = self.client.post(
                "/api/screenplays/analyze",
                json={"fileName": "script.txt", "mimeType": "text/plain", "content": content},
                headers={"x-user-id": "same-user"},
            )
        self.assertEqual(response.status_code, 409)

    def test_rate_limit_rejects_repeated_analysis_calls(self) -> None:
        with (
            patch.object(main, "ANALYSIS_RATE_LIMIT", 1),
            patch.object(main, "run_bounded_analysis", AsyncMock()),
        ):
            first = self.client.post(
                "/api/screenplays/analyze",
                json={"fileName": "one.txt", "mimeType": "text/plain", "content": "one"},
                headers={"x-user-id": "rate-user"},
            )
            second = self.client.post(
                "/api/screenplays/analyze",
                json={"fileName": "two.txt", "mimeType": "text/plain", "content": "two"},
                headers={"x-user-id": "rate-user"},
            )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)

    def test_unapproved_cross_origin_preflight_is_rejected(self) -> None:
        response = self.client.options(
            "/api/screenplays/analyze",
            headers={
                "origin": "https://attacker.example",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_changing_claimed_user_id_does_not_bypass_ip_concurrency(self) -> None:
        main.analysis_slots["running"] = f"ip:{hashlib.sha256(b'testclient').hexdigest()}"
        response = self.client.post(
            "/api/screenplays/analyze",
            json={"fileName": "other.txt", "mimeType": "text/plain", "content": "other"},
            headers={"x-user-id": "forged-different-user"},
        )
        self.assertEqual(response.status_code, 429)

    def test_pdf_page_limit_is_enforced_before_text_extraction(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        buffer = BytesIO()
        writer.write(buffer)
        encoded = base64.b64encode(buffer.getvalue()).decode()
        with patch.object(main, "MAX_PDF_PAGES", 1):
            response = self.client.post(
                "/api/screenplays/analyze",
                json={
                    "fileName": "long.pdf",
                    "mimeType": "application/pdf",
                    "content": encoded,
                },
            )
        self.assertEqual(response.status_code, 413)

    def test_unsupported_mime_type_returns_415(self) -> None:
        response = self.client.post(
            "/api/screenplays/analyze",
            json={
                "fileName": "image.png",
                "mimeType": "image/png",
                "content": "not-an-image",
            },
        )
        self.assertEqual(response.status_code, 415)

    def test_concurrent_restarts_create_only_one_job(self) -> None:
        with main.db() as connection:
            connection.execute(
                """
                INSERT INTO screenplays (
                    id, file_name, mime_type, status, page_count, trace_json,
                    created_at, source_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "restart-race",
                    "failed.txt",
                    "text/plain",
                    "failed",
                    1,
                    "[]",
                    main.now(),
                    "FADE IN",
                ),
            )
        both_loaded = threading.Barrier(2)
        original_load = main.load_record
        load_count = 0
        load_lock = threading.Lock()

        def synchronized_load(screenplay_id: str):
            nonlocal load_count
            record = original_load(screenplay_id)
            with load_lock:
                load_count += 1
                should_wait = load_count <= 2
            if should_wait:
                both_loaded.wait(timeout=2)
            return record

        def owner_from_test_header(request):
            return f"ip:{request.headers['x-test-ip']}"

        with (
            patch.object(main, "load_record", side_effect=synchronized_load),
            patch.object(main, "request_ip_owner", side_effect=owner_from_test_header),
            patch.object(main, "run_bounded_analysis", AsyncMock()),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(
                self.client.post,
                "/api/screenplays/restart-race/restart",
                headers={"x-test-ip": "one"},
            )
            second = executor.submit(
                self.client.post,
                "/api/screenplays/restart-race/restart",
                headers={"x-test-ip": "two"},
            )
            responses = [first.result(timeout=3), second.result(timeout=3)]

        self.assertEqual(sorted(response.status_code for response in responses), [202, 409])
        self.assertEqual(len(main.analysis_slots), 1)


if __name__ == "__main__":
    unittest.main()