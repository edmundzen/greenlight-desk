from __future__ import annotations

import asyncio
import base64
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from pypdf import PdfReader


app = FastAPI(title="Greenlight Desk API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.getenv("GREENLIGHT_DB_PATH", "greenlight.db")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.6-flash")
TEXT_FALLBACK_MODEL = os.getenv("GEMINI_TEXT_FALLBACK_MODEL", "gemini-3.5-flash")
jobs: dict[str, asyncio.Task[None]] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS screenplays (
          id TEXT PRIMARY KEY,
          file_name TEXT NOT NULL,
          mime_type TEXT NOT NULL,
          status TEXT NOT NULL,
          page_count INTEGER,
          report_json TEXT,
          trace_json TEXT NOT NULL,
          key_art_url TEXT,
          decision TEXT,
          decision_at TEXT,
          created_at TEXT NOT NULL,
          source_text TEXT NOT NULL
        )
        """
    )
    return connection


class ScreenplayAnalyzeInput(BaseModel):
    fileName: str = Field(min_length=1, max_length=255)
    mimeType: Literal["text/plain", "application/pdf"]
    content: str = Field(min_length=1)


class DecisionInput(BaseModel):
    decision: Literal["approved", "rejected"]


class TraceEvent(BaseModel):
    id: str
    label: str
    detail: str
    status: Literal["complete", "active", "pending", "error"]
    createdAt: str


class ComparableTitle(BaseModel):
    title: str
    reason: str


class CoverageReport(BaseModel):
    logline: str
    synopsis: str
    strengths: list[str]
    weaknesses: list[str]
    comparableTitles: list[ComparableTitle]
    recommendation: Literal["Pass", "Consider", "Recommend"]


def trace_item(label: str, detail: str, status: str = "complete") -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "label": label,
        "detail": detail,
        "status": status,
        "createdAt": now(),
    }


def initial_trace() -> list[dict[str, Any]]:
    return [
        trace_item("Queued screenplay", "Waiting for the coverage agent", "active"),
        trace_item("Read script", "Pending", "pending"),
        trace_item("Mapped story world", "Pending", "pending"),
        trace_item("Checked tone and genre", "Pending", "pending"),
        trace_item("Drafted recommendation", "Pending", "pending"),
        trace_item("Generated key art", "Pending", "pending"),
    ]


def parse_source(payload: ScreenplayAnalyzeInput) -> tuple[str, int | None]:
    if payload.mimeType == "text/plain":
        text = payload.content
        page_count = max(1, round(len(text) / 3000))
        return text, page_count

    try:
        encoded = payload.content.split(",", 1)[1] if payload.content.startswith("data:") else payload.content
        raw = base64.b64decode(encoded, validate=True)
        reader = PdfReader(BytesIO(raw))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return text, len(reader.pages)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read PDF: {exc}") from exc


def load_record(screenplay_id: str) -> dict[str, Any] | None:
    with db() as connection:
        row = connection.execute(
            "SELECT * FROM screenplays WHERE id = ?", (screenplay_id,)
        ).fetchone()
    if not row:
        return None
    record = dict(row)
    import json

    record["trace"] = json.loads(record.pop("trace_json"))
    report_json = record.pop("report_json")
    record["report"] = json.loads(report_json) if report_json else None
    record.pop("source_text", None)
    record["keyArtUrl"] = record.pop("key_art_url")
    record["fileName"] = record.pop("file_name")
    record["mimeType"] = record.pop("mime_type")
    record["pageCount"] = record.pop("page_count")
    record["createdAt"] = record.pop("created_at")
    record["decisionAt"] = record.pop("decision_at")
    return record


def save_trace(screenplay_id: str, trace: list[dict[str, Any]], status: str | None = None) -> None:
    import json

    with db() as connection:
        if status:
            connection.execute(
                "UPDATE screenplays SET trace_json = ?, status = ? WHERE id = ?",
                (json.dumps(trace), status, screenplay_id),
            )
        else:
            connection.execute(
                "UPDATE screenplays SET trace_json = ? WHERE id = ?",
                (json.dumps(trace), screenplay_id),
            )


def update_record(screenplay_id: str, **values: Any) -> None:
    if not values:
        return
    import json

    values = {
        ("report_json" if key == "report" else "key_art_url" if key == "keyArtUrl" else key): (
            value.model_dump_json() if isinstance(value, BaseModel) else json.dumps(value)
            if key == "report"
            else value
        )
        for key, value in values.items()
    }
    assignments = ", ".join(f"{key} = ?" for key in values)
    with db() as connection:
        connection.execute(
            f"UPDATE screenplays SET {assignments} WHERE id = ?",
            (*values.values(), screenplay_id),
        )


async def add_trace(screenplay_id: str, label: str, detail: str, status: str = "complete") -> None:
    record = load_record(screenplay_id)
    if not record:
        return
    trace = record["trace"]
    for item in trace:
        if item["status"] == "active":
            item["status"] = "complete"
    existing = next((item for item in trace if item["label"] == label and item["status"] == "pending"), None)
    if existing:
        existing["detail"] = detail
        existing["status"] = status
        existing["createdAt"] = now()
    else:
        trace.append(trace_item(label, detail, status))
    save_trace(screenplay_id, trace)
    await asyncio.sleep(0.55)


def gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Add it in Replit Secrets before analyzing a screenplay."
        )
    return genai.Client(api_key=api_key)


def key_art_error_message(error: Exception) -> str:
    detail = str(error)
    if "429" in detail or "RESOURCE_EXHAUSTED" in detail or "quota" in detail.lower():
        return "Gemini image quota is exhausted. Coverage remains ready."
    if "404" in detail or "NOT_FOUND" in detail:
        return "The Gemini image model is unavailable. Coverage remains ready."
    return "Key art could not be generated. Coverage remains ready."


async def generate_key_art(
    screenplay_id: str, source_text: str, report: dict[str, Any] | CoverageReport
) -> None:
    report_data = report.model_dump() if isinstance(report, CoverageReport) else report
    art_prompt = f"""
Create one cinematic key-art image for this screenplay. It must feel like a polished
festival one-sheet with no readable words, logos, or credits. Infer the genre, setting,
and central visual metaphor from the saved coverage and script. Use strong composition,
atmospheric lighting, and a restrained film-poster palette.

SAVED COVERAGE:
{report_data}

SCREENPLAY EXCERPT:
{source_text[:12000]}
"""
    art_response = await asyncio.to_thread(
        gemini_client().models.generate_content,
        model=IMAGE_MODEL,
        contents=art_prompt,
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )
    image_part = next(
        (
            part
            for candidate in (art_response.candidates or [])
            for part in (candidate.content.parts if candidate.content else [])
            if part.inline_data and part.inline_data.data
        ),
        None,
    )
    if not image_part:
        raise RuntimeError("Gemini returned no image data for key art.")
    mime = image_part.inline_data.mime_type or "image/png"
    encoded = base64.b64encode(image_part.inline_data.data).decode()
    update_record(screenplay_id, keyArtUrl=f"data:{mime};base64,{encoded}")


async def run_analysis(screenplay_id: str) -> None:
    record = load_record(screenplay_id)
    if not record:
        return

    try:
        with db() as connection:
            row = connection.execute(
                "SELECT source_text FROM screenplays WHERE id = ?", (screenplay_id,)
            ).fetchone()
        source_text = row["source_text"] if row else ""

        await add_trace(screenplay_id, "Read script", f"{record['pageCount'] or '—'} pages ingested", "active")
        character_count = len(
            set(re.findall(r"(?m)^[A-Z][A-Z0-9 .'-]{2,28}(?=\s*\n)", source_text))
        )
        location_count = len(
            set(re.findall(r"(?im)^(?:INT\.|EXT\.)\s+([A-Z0-9 .'-]+)", source_text))
        )
        await add_trace(
            screenplay_id,
            "Mapped story world",
            f"Extracted {max(1, character_count)} characters, {max(1, location_count)} locations",
        )
        await add_trace(screenplay_id, "Checked tone and genre", "Testing premise, stakes, and tonal consistency", "active")

        client = gemini_client()
        prompt = f"""
You are a senior film development executive writing internal script coverage.
Analyze the screenplay below and return ONLY valid JSON matching this schema:
{CoverageReport.model_json_schema()}

Be specific, candid, and concise. Comparable titles must be real films or series and
include one sentence explaining the comparison. Recommendation must be exactly Pass,
Consider, or Recommend.

SCREENPLAY:
{source_text[:160000]}
"""
        response = None
        text_error: Exception | None = None
        for model in dict.fromkeys((TEXT_MODEL, TEXT_FALLBACK_MODEL)):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=CoverageReport.model_json_schema(),
                        temperature=0.25,
                    ),
                )
                break
            except Exception as exc:
                text_error = exc
                error_detail = str(exc)
                if not any(marker in error_detail for marker in ("503", "UNAVAILABLE", "404", "NOT_FOUND")):
                    raise
        if response is None:
            raise text_error or RuntimeError("Gemini did not return coverage.")
        report = CoverageReport.model_validate_json(response.text)
        update_record(screenplay_id, report=report)
        await add_trace(screenplay_id, "Drafted recommendation", f"{report.recommendation} based on story and market fit")

        try:
            await generate_key_art(screenplay_id, source_text, report)
            await add_trace(screenplay_id, "Generated key art", "Created one visual direction from the script’s genre and setting")
        except Exception as art_error:
            # Key art is an optional visual add-on. Image quota/model failures must
            # not prevent a producer from reviewing or deciding on the coverage.
            art_detail = key_art_error_message(art_error)
            await add_trace(screenplay_id, "Generated key art", art_detail, "error")
        save_trace(screenplay_id, load_record(screenplay_id)["trace"], "ready")
    except Exception as exc:
        record = load_record(screenplay_id)
        trace = record["trace"] if record else []
        for item in trace:
            if item["status"] == "active":
                item["status"] = "error"
        trace.append(trace_item("Analysis stopped", str(exc), "error"))
        save_trace(screenplay_id, trace, "failed")
    finally:
        jobs.pop(screenplay_id, None)


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/screenplays")
async def list_screenplays() -> list[dict[str, Any]]:
    with db() as connection:
        rows = connection.execute(
            "SELECT id, file_name, status, report_json, created_at FROM screenplays ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
    import json

    result = []
    for row in rows:
        report = json.loads(row["report_json"]) if row["report_json"] else None
        result.append(
            {
                "id": row["id"],
                "fileName": row["file_name"],
                "status": row["status"],
                "recommendation": report.get("recommendation") if report else None,
                "createdAt": row["created_at"],
            }
        )
    return result


@app.post("/api/screenplays/analyze", status_code=202)
async def analyze_screenplay(payload: ScreenplayAnalyzeInput) -> dict[str, str]:
    source_text, page_count = parse_source(payload)
    if not source_text.strip():
        raise HTTPException(status_code=400, detail="The screenplay did not contain readable text.")
    screenplay_id = str(uuid.uuid4())
    initial_trace = initial_trace()
    import json

    with db() as connection:
        connection.execute(
            """
            INSERT INTO screenplays
            (id, file_name, mime_type, status, page_count, trace_json, created_at, source_text)
            VALUES (?, ?, ?, 'analyzing', ?, ?, ?, ?)
            """,
            (screenplay_id, payload.fileName, payload.mimeType, page_count, json.dumps(initial_trace), now(), source_text),
        )
    jobs[screenplay_id] = asyncio.create_task(run_analysis(screenplay_id))
    return {"screenplayId": screenplay_id, "status": "analyzing"}


@app.get("/api/screenplays/{screenplay_id}")
async def get_screenplay(screenplay_id: str) -> dict[str, Any]:
    record = load_record(screenplay_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    return record


@app.post("/api/screenplays/{screenplay_id}/restart", status_code=202)
async def restart_screenplay(screenplay_id: str) -> dict[str, str]:
    record = load_record(screenplay_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    if record["status"] != "failed":
        raise HTTPException(status_code=409, detail="Only failed analyses can be restarted.")
    if screenplay_id in jobs:
        raise HTTPException(status_code=409, detail="This analysis is already running.")

    import json

    update_record(
        screenplay_id,
        status="analyzing",
        report=None,
        keyArtUrl=None,
        decision=None,
        decision_at=None,
    )
    save_trace(screenplay_id, initial_trace(), "analyzing")
    jobs[screenplay_id] = asyncio.create_task(run_analysis(screenplay_id))
    return {"screenplayId": screenplay_id, "status": "analyzing"}


@app.post("/api/screenplays/{screenplay_id}/decision")
async def decide_screenplay(screenplay_id: str, payload: DecisionInput) -> dict[str, Any]:
    record = load_record(screenplay_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    if record["status"] != "ready" or not record["report"]:
        raise HTTPException(status_code=409, detail="Coverage is not ready for a decision.")
    update_record(
        screenplay_id,
        decision=payload.decision,
        decision_at=now(),
        status="approved" if payload.decision == "approved" else "rejected",
    )
    return load_record(screenplay_id)


@app.post("/api/screenplays/{screenplay_id}/key-art/retry")
async def retry_screenplay_key_art(screenplay_id: str) -> dict[str, Any]:
    record = load_record(screenplay_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenplay not found.")
    if record["status"] != "ready" or not record["report"]:
        raise HTTPException(status_code=409, detail="Coverage must be ready before retrying key art.")
    if record["keyArtUrl"]:
        raise HTTPException(status_code=409, detail="Key art has already been generated.")

    with db() as connection:
        row = connection.execute(
            "SELECT source_text FROM screenplays WHERE id = ?", (screenplay_id,)
        ).fetchone()
    try:
        await generate_key_art(
            screenplay_id, row["source_text"] if row else "", record["report"]
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=key_art_error_message(exc)) from exc
    return load_record(screenplay_id)
