# Prompts

Key prompts used to **build** Utsav Planner: which tool and model, what came out, and what changed next.
The prompts the app itself sends to the LLM live in `app/ai/prompts/`.

## 2026-10-21: Requirements, setup and architecture
- **Tool / model:** Claude Code (Claude Opus 5.5), plan-first; no files until approved.
- **Prompt (summary):** "Read both planning briefs completely. Confirm the problem, rules and deliverables; list contradictions and risks. Critique the Antigravity + Claude Code setup. Propose the architecture decision by decision with 2–3 options each, judged against code quality, problem alignment, security, efficiency, testing and accessibility. Keep the persona swappable. Then draft AGENTS.md, CLAUDE.md, docs/ARCHITECTURE.md and docs/SPEC.md for approval."
- **Outcome:** 16 flagged ambiguities. A canary test showed that Antigravity loads `AGENTS.md`, `GEMINI.md` and both rules folders together, so `AGENTS.md` became the only rules file. Stack decided: FastAPI + Jinja2/htmx + Neon + Vercel. The LLM chain was redesigned after finding the free Gemini Flash quota is about 20 requests/day (Flash-Lite about 500/day).
- **Next iteration:** the developer delegated the remaining decisions. Drafts were approved and committed (`d3fba60`).

## 2026-10-21: Implementation plan
- **Tool / model:** Antigravity, Planning mode (Gemini 3.8 Flash, thinking level High).
- **Prompt (verbatim):** "Read AGENTS.md, docs/ARCHITECTURE.md and docs/SPEC.md fully. Produce an implementation plan and task list for all P0 features that follows the architecture exactly: the module layout, the ownership lanes and the priority order. Group tasks by lane (Claude Code / Antigravity), mark dependencies between tasks, and put the Vercel spike from ARCHITECTURE §14 first. Do not create or edit any files."
- **Outcome:** A phased plan (spike first, then domain → AI → persistence → services → web → tests), a traceability matrix and template data contracts.
- **Next iteration:** Claude Code reviewed it against the architecture and corrected six points. AI-triggering and state-changing routes must be POST (a GET alternatives route could be triggered by crawlers). No DELETE forms. Inline alternatives, not a modal. Fixed-window rate limit. The accessibility e2e test belongs to the tests lane. `ChangeOperation` belongs in the domain layer. Applied in `03639e8`.

## 2026-10-21: Spike, dependencies and app factory (SPIKE-C01, SPIKE-C02)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Start SPIKE-C01 and SPIKE-C02: dependency manifest, settings, app factory with /healthz, and the Vercel entrypoint."
- **Outcome:**
  - Full runtime dependency set installed now, so the spike measures a realistic cold start.
  - Settings fail fast in production without `DATABASE_URL` or a strong `SESSION_SECRET`.
  - `create_app()` factory with `/healthz` (never touches the database).
  - Vercel entrypoint `app.index:app`, region `sin1`, `maxDuration` 60.
- **Next iteration:** Starlette's test client raised a deprecation warning, so `httpx2` was added as a dev dependency and tests now run with warnings treated as errors.

## 2026-10-21: Neon connection and readiness check (SPIKE-C04)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Start C04: Neon connection layer and /readyz."
- **Outcome:**
  - The engine uses NullPool through Neon's pooler, so idle serverless instances hold no connections. `postgresql://` URLs are mapped to psycopg 3.
  - `/readyz` returns 503 without revealing why (no host, no credentials). `/healthz` stays database-free.
  - Tests cover the failure paths.
  - Checked live against the Neon `dev` branch: 200.
- **Next iteration:** from a laptop in India, `/readyz` took 1.2–2.1 s because NullPool opens a new TLS connection per request. Re-measure from Vercel `sin1` (same region as Neon) before deciding whether a small pool is worth it on Fluid compute.
