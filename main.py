from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Iterator, Literal

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
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.6-flash")
TEXT_FALLBACK_MODEL = os.getenv("GEMINI_TEXT_FALLBACK_MODEL", "gemini-3.5-flash")
jobs: dict[str, asyncio.Task[None]] = {}
KEY_ART_CLAIM_TTL = timedelta(minutes=10)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH)
    try:
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS key_art_retry_claims (
              screenplay_id TEXT PRIMARY KEY,
              owner_id TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              FOREIGN KEY (screenplay_id) REFERENCES screenplays(id) ON DELETE CASCADE
            )
            """
        )
        with connection:
            yield connection
    finally:
        connection.close()


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


def claim_key_art_retry(
    screenplay_id: str,
    owner_id: str,
    *,
    claimed_at: datetime | None = None,
) -> bool:
    claimed_at = claimed_at or datetime.now(timezone.utc)
    expires_at = claimed_at + KEY_ART_CLAIM_TTL
    with db() as connection:
        cursor = connection.execute(
            """
            INSERT INTO key_art_retry_claims (screenplay_id, owner_id, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(screenplay_id) DO UPDATE SET
              owner_id = excluded.owner_id,
              expires_at = excluded.expires_at
            WHERE key_art_retry_claims.expires_at <= ?
            """,
            (
                screenplay_id,
                owner_id,
                expires_at.isoformat(),
                claimed_at.isoformat(),
            ),
        )
        return cursor.rowcount == 1


def release_key_art_retry(screenplay_id: str, owner_id: str) -> None:
    with db() as connection:
        connection.execute(
            """
            DELETE FROM key_art_retry_claims
            WHERE screenplay_id = ? AND owner_id = ?
            """,
            (screenplay_id, owner_id),
        )


def renew_key_art_retry(
    screenplay_id: str,
    owner_id: str,
    *,
    renewed_at: datetime | None = None,
) -> bool:
    renewed_at = renewed_at or datetime.now(timezone.utc)
    with db() as connection:
        cursor = connection.execute(
            """
            UPDATE key_art_retry_claims
            SET expires_at = ?
            WHERE screenplay_id = ? AND owner_id = ?
            """,
            (
                (renewed_at + KEY_ART_CLAIM_TTL).isoformat(),
                screenplay_id,
                owner_id,
            ),
        )
        return cursor.rowcount == 1


async def maintain_key_art_retry_claim(
    screenplay_id: str, owner_id: str, stop: asyncio.Event
) -> None:
    renewal_interval = min(
        30.0, max(0.01, KEY_ART_CLAIM_TTL.total_seconds() / 3)
    )
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=renewal_interval)
            return
        except TimeoutError:
            if not renew_key_art_retry(screenplay_id, owner_id):
                return


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


def replace_trace_event(
    screenplay_id: str, label: str, detail: str, status: str = "complete"
) -> None:
    record = load_record(screenplay_id)
    if not record:
        return
    trace = record["trace"]
    existing = next((item for item in reversed(trace) if item["label"] == label), None)
    if existing:
        existing["detail"] = detail
        existing["status"] = status
        existing["createdAt"] = now()
    else:
        trace.append(trace_item(label, detail, status))
    save_trace(screenplay_id, trace)


def gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not configured. Add it in Replit Secrets before analyzing a screenplay."
        )
    return genai.Client(api_key=api_key)


def key_art_error_message(error: Exception) -> str:
    return "Key art could not be rendered. Coverage remains ready."


GENRE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "thriller": ("thriller", "danger", "threat", "secret", "flee", "escape", "tension", "mystery"),
    "horror": ("horror", "haunt", "terror", "nightmare", "supernatural", "ghost", "monster"),
    "science-fiction": ("science fiction", "sci-fi", "space", "future", "robot", "alien", "technology"),
    "romance": ("romance", "romantic", "love", "relationship", "heart", "intimacy"),
    "comedy": ("comedy", "comic", "funny", "humor", "hilarious", "satire"),
    "adventure": ("adventure", "quest", "journey", "expedition", "discovery", "world"),
    "drama": ("drama", "family", "grief", "identity", "character", "emotional"),
}

TONE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tense": ("tense", "tension", "urgent", "danger", "threat", "escape", "desperate", "stakes"),
    "dark": ("dark", "grim", "violent", "death", "terrifying", "sinister", "night"),
    "mysterious": ("mystery", "mysterious", "secret", "unknown", "unseen", "discovery"),
    "warm": ("warm", "tender", "intimate", "family", "love", "heartfelt"),
    "hopeful": ("hope", "hopeful", "uplifting", "triumph", "redemption", "joy"),
}

TONE_PALETTES: dict[str, tuple[str, str, str, str]] = {
    "tense": ("#102f2d", "#071b1a", "#e26f45", "#f4d6b8"),
    "dark": ("#241d30", "#090a12", "#b7464b", "#d9b6a3"),
    "mysterious": ("#18313a", "#0a1821", "#b86b54", "#d7c6a8"),
    "warm": ("#56352f", "#241b1b", "#e19a6a", "#f5dfc2"),
    "hopeful": ("#315d59", "#162f36", "#e0b85d", "#f5ead0"),
}


def _coverage_text(report: dict[str, Any]) -> str:
    values: list[str] = [
        str(report.get("logline", "")),
        str(report.get("synopsis", "")),
        *[str(item) for item in report.get("strengths", [])],
        *[str(item) for item in report.get("weaknesses", [])],
    ]
    return " ".join(values).lower()


def _best_keyword_match(text: str, groups: dict[str, tuple[str, ...]], default: str) -> str:
    scores = {
        name: sum(text.count(keyword) for keyword in keywords)
        for name, keywords in groups.items()
    }
    winner = max(scores, key=scores.get)
    return winner if scores[winner] else default


def render_key_art_svg(report: dict[str, Any]) -> str:
    text = _coverage_text(report)
    genre = _best_keyword_match(text, GENRE_KEYWORDS, "drama")
    tone = _best_keyword_match(text, TONE_KEYWORDS, "mysterious")
    background, shadow, accent, highlight = TONE_PALETTES[tone]
    digest = hashlib.sha256(
        json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
    ).digest()
    shift_x = 40 + digest[0] % 150
    shift_y = 20 + digest[1] % 110
    rotation = -18 + digest[2] % 37

    compositions = {
        "thriller": f'<path d="M0 810 L{420 + shift_x} 90 L720 810 Z" fill="{accent}" opacity=".16"/><path d="M180 810 L690 210 L1010 810 Z" fill="{highlight}" opacity=".07"/>',
        "horror": f'<circle cx="{760 + shift_x}" cy="{180 + shift_y}" r="235" fill="{accent}" opacity=".14"/><path d="M520 810 Q650 300 780 810 Z" fill="#000" opacity=".32"/>',
        "science-fiction": f'<ellipse cx="790" cy="310" rx="360" ry="120" fill="none" stroke="{accent}" stroke-width="9" opacity=".28" transform="rotate({rotation} 790 310)"/><circle cx="790" cy="310" r="72" fill="{highlight}" opacity=".18"/>',
        "romance": f'<circle cx="{420 + shift_x}" cy="{250 + shift_y}" r="250" fill="{accent}" opacity=".13"/><circle cx="{680 + shift_x}" cy="{250 + shift_y}" r="250" fill="{highlight}" opacity=".09"/>',
        "comedy": f'<circle cx="{350 + shift_x}" cy="{220 + shift_y}" r="210" fill="{accent}" opacity=".22"/><rect x="650" y="130" width="310" height="310" rx="70" fill="{highlight}" opacity=".10" transform="rotate({rotation} 805 285)"/>',
        "adventure": f'<path d="M0 720 L330 310 L510 560 L760 180 L1200 720 V810 H0 Z" fill="{accent}" opacity=".17"/><circle cx="900" cy="180" r="105" fill="{highlight}" opacity=".20"/>',
        "drama": f'<rect x="{210 + shift_x}" y="100" width="330" height="760" fill="{accent}" opacity=".12" transform="rotate({rotation} 375 480)"/><circle cx="850" cy="260" r="190" fill="{highlight}" opacity=".10"/>',
    }
    motif = compositions[genre]
    genre_label = html.escape(genre.replace("-", " ").upper())
    tone_label = html.escape(tone.upper())
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="810" viewBox="0 0 1200 810">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="{background}"/><stop offset="1" stop-color="{shadow}"/></linearGradient>
  <radialGradient id="glow"><stop stop-color="{accent}" stop-opacity=".45"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>
  <filter id="grain"><feTurbulence baseFrequency=".72" numOctaves="3" seed="{digest[3]}"/><feColorMatrix values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 .08 0"/></filter>
</defs>
<rect width="1200" height="810" fill="url(#bg)"/>
<circle cx="{780 + shift_x}" cy="{190 + shift_y}" r="330" fill="url(#glow)"/>
{motif}
<g fill="none" stroke="{highlight}" opacity=".25">
  <circle cx="1035" cy="135" r="108"/><circle cx="1035" cy="135" r="155"/>
  <rect x="1050" y="650" width="70" height="105"/><rect x="1063" y="663" width="44" height="79"/>
</g>
<path d="M70 690 H470" stroke="{highlight}" opacity=".34"/>
<text x="70" y="730" fill="{highlight}" font-family="Arial, sans-serif" font-size="22" letter-spacing="8">{genre_label}</text>
<text x="70" y="770" fill="{accent}" font-family="Arial, sans-serif" font-size="15" letter-spacing="6">{tone_label} / VISUAL NORTH STAR</text>
<rect width="1200" height="810" filter="url(#grain)" opacity=".28"/>
</svg>"""


async def generate_key_art(
    screenplay_id: str, source_text: str, report: dict[str, Any] | CoverageReport
) -> None:
    report_data = report.model_dump() if isinstance(report, CoverageReport) else report
    svg = render_key_art_svg(report_data)
    encoded = base64.b64encode(svg.encode()).decode()
    update_record(screenplay_id, keyArtUrl=f"data:image/svg+xml;base64,{encoded}")


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
            await add_trace(screenplay_id, "Generated key art", "Rendered a deterministic visual direction from coverage genre and tone")
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
    trace = initial_trace()
    import json

    with db() as connection:
        connection.execute(
            """
            INSERT INTO screenplays
            (id, file_name, mime_type, status, page_count, trace_json, created_at, source_text)
            VALUES (?, ?, ?, 'analyzing', ?, ?, ?, ?)
            """,
            (screenplay_id, payload.fileName, payload.mimeType, page_count, json.dumps(trace), now(), source_text),
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
    if record["status"] not in {"ready", "approved", "rejected"} or not record["report"]:
        raise HTTPException(status_code=409, detail="Coverage must be ready before retrying key art.")
    if record["keyArtUrl"]:
        raise HTTPException(status_code=409, detail="Key art has already been generated.")
    claim_owner = str(uuid.uuid4())
    if not claim_key_art_retry(screenplay_id, claim_owner):
        raise HTTPException(
            status_code=409,
            detail="Key art generation is already in progress for this screenplay.",
        )

    stop_heartbeat = asyncio.Event()
    heartbeat: asyncio.Task[None] | None = None
    try:
        with db() as connection:
            row = connection.execute(
                """
                SELECT source_text, key_art_url
                FROM screenplays
                WHERE id = ?
                """,
                (screenplay_id,),
            ).fetchone()
        if row and row["key_art_url"]:
            raise HTTPException(
                status_code=409, detail="Key art has already been generated."
            )
        heartbeat = asyncio.create_task(
            maintain_key_art_retry_claim(screenplay_id, claim_owner, stop_heartbeat)
        )
        try:
            await generate_key_art(
                screenplay_id, row["source_text"] if row else "", record["report"]
            )
            replace_trace_event(
                screenplay_id,
                "Generated key art",
                "Rendered a deterministic visual direction from coverage genre and tone",
            )
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=503, detail=key_art_error_message(exc)) from exc
    finally:
        stop_heartbeat.set()
        heartbeat_error: Exception | None = None
        try:
            if heartbeat:
                await heartbeat
        except Exception as exc:
            heartbeat_error = exc
        finally:
            release_key_art_retry(screenplay_id, claim_owner)
        if heartbeat_error:
            raise HTTPException(
                status_code=503, detail=key_art_error_message(heartbeat_error)
            ) from heartbeat_error
    return load_record(screenplay_id)
