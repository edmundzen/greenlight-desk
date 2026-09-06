# Greenlight Desk

Greenlight Desk helps producers turn screenplay drafts into traceable, reviewable coverage with Google Gemini.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the Python FastAPI service (port 8080)
- `pnpm --filter @workspace/greenlight-desk run dev` — run the React producer workspace
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- Required secret: `GEMINI_API_KEY` — Google Gemini API key, stored in Replit Secrets

## Stack

- pnpm workspaces, Node.js 24, Python 3.13
- API: FastAPI + Uvicorn, google-genai, pypdf
- Persistence: SQLite for screenplay metadata, trace state, coverage, and decisions
- Frontend: React + Vite + TanStack Query
- API codegen: Orval (from OpenAPI spec)
- Build: Vite

## Where things live

- `main.py` — FastAPI service, Gemini coverage analysis, deterministic SVG key art, trace orchestration, and decision gate
- `artifacts/greenlight-desk/src/App.tsx` — producer workspace UI
- `artifacts/greenlight-desk/src/index.css` — Greenlight Desk visual language
- `lib/api-spec/openapi.yaml` — API contract source of truth

## Architecture decisions

- The analysis job runs asynchronously so the client can poll the detail endpoint and reveal trace events progressively.
- Coverage is generated through the Google Gemini SDK; missing credentials fail explicitly. Key art is rendered locally as deterministic SVG from the report’s inferred genre and tone.
- Decisions are rejected by the API until a ready report exists, keeping approval as a hard human gate.

## Product

- Upload PDF or TXT screenplay drafts.
- Review incremental agent trace events, structured coverage, and deterministic algorithmic key art.
- Approve or reject coverage after the report is ready.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

_Populate as you build — sharp edges, "always run X before Y" rules._

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
