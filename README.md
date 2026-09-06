# Greenlight Desk

An AI producer's assistant that automates the first read on a submitted script.

## What it does

Greenlight Desk takes a submitted script and produces a full coverage report — a recommendation, comparable titles, and supporting analysis — the same job a studio's "reader" does manually. A human then reviews and explicitly approves or rejects the recommendation before anything is finalized. Nothing ships without that human-in-the-loop step.

The pipeline also attempts to generate promotional key art for the project alongside the written coverage.

## How it works

1. A script is submitted and analyzed with Gemini.
2. A coverage report is generated: recommendation, comparable titles, supporting rationale.
3. Key art generation is attempted in parallel.
4. A human reviewer approves or rejects the coverage report.
5. The decision is logged.

Every step is visible in an Agent Trace so a reviewer can see exactly what ran and why.

## Known limitation

Key art generation can fail with `RESOURCE_EXHAUSTED` on a free-tier Gemini API key — free tier carries zero image-generation quota by design, not a depleted balance. This is a billing-tier limitation, not a bug, and is fixed by using an API key issued from a billing-linked (Blaze plan) Google Cloud project.

Importantly, this failure is **non-blocking**: the coverage report still completes and reaches "Ready for review" even when key art fails, so a producer can still read and act on the recommendation.

## Tech stack

TypeScript, Python, Gemini API, built and deployed on Replit.

## Running it

Requires a `GEMINI_API_KEY` secret (from a billing-linked Google Cloud project for full functionality, including key art). Built to run directly on Replit.

## License

MIT — see [LICENSE](./LICENSE).
