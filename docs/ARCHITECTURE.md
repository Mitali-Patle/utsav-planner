# Architecture: Utsav Planner

Status: v1, decided 21 Oct 2026. This document is binding on all agents (see `AGENTS.md`).

## 1. Principle
**AI interprets, reasons and generates. Code calculates and enforces. The user approves.**

| GenAI does | Code does |
|---|---|
| Turns a free-text change request into a structured change, or asks a clarifying question | All money and date arithmetic |
| Personalizes the plan to the event's context: ideas, budget emphasis, extra tasks, vendor priorities, each with a "why" | Validation of every input and every AI output |
| Suggests alternatives when the plan is over budget, with reasons | Enforcing protected items, idempotency and versioning |
| Declines off-topic requests | Persistence, access control, rate limits, caching |

The AI never writes to storage and never produces a number that code then trusts without checking.
Numbers the AI *extracts* from user text (for example "₹12,000", "3 days earlier") are validated against limits before use.

## 2. System context
```
Browser (HTML + htmx, no secrets)
   │  HTTPS, forms / htmx partial requests, CSRF token, signed session cookie
   ▼
Vercel Function: FastAPI app (region sin1, Singapore)
   ├── web/        routes, session, CSRF, rate limit, security headers, Jinja2 templates
   ├── services/   use cases: planning, change manager, alternatives
   ├── domain/     pure logic: budget, timeline, vendors, changes, catalog
   ├── ai/         LLMClient protocol → FallbackChain → Gemini / Groq providers
   └── persistence/ SQLAlchemy repositories
        │                                  │
        ▼                                  ▼
Neon Postgres (aws-ap-southeast-1)   Gemini API (primary) → Groq API (fallback)
events, proposals, ai_cache,
rate_limits
Static assets: public/static/** served by Vercel CDN
```

## 3. Decision log
| # | Decision | Choice | Why (rubric impact) | Rejected |
|---|---|---|---|---|
| D1 | Backend | FastAPI + Pydantic v2, sync endpoints | Types end to end; one Pydantic schema both requests and validates AI output (quality, security). Matches the developer's strength. | Express + Zod; Next.js (frontend LLM-call risk, two languages) |
| D2 | Frontend | Server-rendered Jinja2 + htmx 2 (vendored) + hand-written CSS | Core content is visible without JS (the evaluator may fetch raw HTML). Semantic HTML makes a11y cheap. Tiny payload (efficiency). One language. | React SPA (empty without JS, bigger bundle); Alpine.js |
| D3 | Storage | Neon Postgres via SQLAlchemy 2 + psycopg 3, NullPool, pooled connection string | Serverless-friendly, free with no card, no 7-day pause. One service holds plans, history, AI cache and rate limits. | Firestore (heavy client, long-lived service-account key); browser-only storage (no server rendering or history); Supabase (pauses) |
| D4 | Hosting | Vercel Hobby, Fluid compute, FastAPI zero-config | Free, no card, cold start ≈ 1–2 s (to be measured in the spike). | Cloud Run (needs billing); Render (30–60 s cold start); Koyeb |
| D5 | LLM | Chain: Gemini 3.5 Flash-Lite → Gemini 3.1 Flash-Lite → Groq `openai/gpt-oss-120b` → labelled non-AI fallback. Configured via `LLM_CHAIN`. | The free-tier Flash quota is about 20 requests/day; Flash-Lite is about 500/day per model. The chain gives about 2,000 AI calls/day and survives a single-provider outage. | Gemini only; Groq first |
| D6 | Failure policy | Per-call timeout, quota cooldown, one repair retry, labelled fallback, writes only on approval | The app is never broken or dishonest at judging time. | |
| D7 | AI transparency | "How this was generated" panel on every AI result | Proves genuine and dynamic GenAI to reviewers and in the video. | Badge only |
| D8 | Sessions | Anonymous signed-cookie session; events owned by session | Each judge gets private data with no login. | Accounts (scope); public event IDs (IDOR) |
| D9 | Tooling | uv, ruff (lint and format), mypy --strict, pytest + hypothesis + Playwright/axe, pre-commit, GitHub Actions | Visible quality signals; edge cases are covered. | |

## 4. Modules
```
app/
  index.py            Vercel entrypoint (`[tool.vercel] entrypoint = "app.index:app"`): `app = create_app()`
  main.py             create_app(settings, llm_client=None, clock=None, repo=None): DI root
  config.py           Settings (pydantic-settings, SecretStr); fails fast if a prod secret is missing
  content/persona.py  Persona: copy, tone guidance for prompts, example presets (swappable)
  domain/
    models.py         EventPlan, BudgetCategory, Expense, Task, VendorItem, Idea, enums
    catalog.py        Baseline data per event type: categories + weights, tasks + offsets, vendor types + lead times
    money.py          format_inr (Indian digit grouping), largest_remainder_split
    budget.py         allocate, summarize, add_expense, reduce_to_total, scale_for_guests, shortfall
    timeline.py       due dates from offsets, shift_event_date, compress_overdue, sort
    vendors.py        book_by dates, status transitions, priority ordering
    changes.py        ChangeOperation (defined here) → apply to an immutable copy → Proposal (before/after diff, affected items)
    clock.py          Clock protocol + SystemClock (Asia/Kolkata)
    errors.py         ValidationError, InfeasibleChange, StaleProposal, NotFound
  ai/
    client.py         LLMClient protocol, LLMRequest, LLMResult (provenance)
    providers/gemini.py, providers/groq.py
    chain.py          FallbackChain: timeouts, cooldowns, repair retry, ordering
    cache.py          AICache (in-memory LRU in front of the Postgres table)
    schemas.py        PlanSuggestions, ChangeIntent (reuses domain ChangeOperation), Alternatives
    prompts/          personalize.py, interpret_change.py, alternatives.py (SYSTEM, FEW_SHOTS, VERSION)
    guardrails.py     input normalization, data delimiting, post-validation (referenced IDs exist, bounds)
    fallbacks.py      deterministic, labelled non-AI outputs
  services/
    planning.py       create_event, regenerate_ideas
    change_manager.py request_change → Proposal | Clarification; approve; reject
    alternatives.py   suggest_alternatives (only on demand)
  persistence/
    db.py, tables.py, repositories.py   (EventRepository, ProposalRepository, CacheStore, RateLimitStore)
  web/
    routes/ pages.py events.py budget.py timeline.py vendors.py changes.py health.py
    deps.py           session, CSRF, rate limit, current event (ownership check)
    security.py       headers middleware (CSP etc.), body-size limit
    templates/        base.html, partials/*.html
public/static/        css/app.css, js/app.js, vendor/htmx.min.js
```
Dependencies point **inward**: web → services → (domain, ai, persistence). `domain` imports nothing from the app.

## 5. Data model
`EventPlan` (one Pydantic document, stored as JSON with a `version` column):
- id (UUID), owner_session (UUID), event_type (`wedding` | `birthday` | `community`), title, city, event_date, guest_count, total_budget_inr, preferences (≤ 500 chars), sub_events (for example haldi, mehendi, sangeet, ceremony, reception)
- `categories`: id, name, weight, allocated_inr, scales_with_guests, protected
- `expenses`: id, category_id, label, amount_inr, protected, source (`user` | `change`)
- `tasks`: id, title, category_id, offset_days, due_date, done, source (`catalog` | `ai` | `user`), why
- `vendors`: id, vendor_type, status (`todo` | `contacted` | `booked`), lead_days, book_by, priority, why, notes
- `ideas`: id, title, description, category_id, budget_impact (`saves` | `neutral` | `adds`), why
- `ai_meta`: provenance of the last personalization (task, model, prompt version, cached, fallback)
- `form_tokens`: last 50 processed idempotency tokens

Tables:
- `events` (id PK, owner_session idx, version, plan JSON, created_at, updated_at)
- `proposals` (id PK, event_id FK idx, base_version, request_text, intent JSON, proposal JSON, status `pending|approved|rejected|stale`, created_at, decided_at). This table is the **change history**.
- `ai_cache` (key PK = sha256(task, prompt_version, canonical payload), response JSON, provider, created_at, expires_at)
- `rate_limits` (bucket, window_start, count; PK bucket + window_start)

The schema is created by `python -m scripts.init_db`; tests use `create_all` on in-memory SQLite. Generic JSON type is used, so the code is portable between the two.

## 6. AI design
### 6.1 Tasks
There is exactly one LLM call per user action. Results are cached.

| Task | Trigger | Input (data, not instructions) | Output schema | Temp | Code then does |
|---|---|---|---|---|---|
| `personalize` | Create event; "Regenerate ideas" | Event context + baseline category, task and vendor IDs + persona tone | `PlanSuggestions`: 3–5 ideas with why; allocation emphasis per category (`increase`/`decrease` × `small`/`medium`, why); up to 5 extra tasks (title, category_id, days_before_event, why); vendor priorities with why; summary_why; `off_topic` | 0.8 | Maps emphasis to bounded weight multipliers (small ±10 %, medium ±20 %), renormalizes, allocates with largest remainder; bounds-checks and dedupes tasks; computes dates |
| `interpret_change` | Change Manager request | Request text + plan summary (IDs, names, amounts, protected flags, guests, date) | `ChangeIntent`: status `ok`/`needs_clarification`/`off_topic`, clarification_question, operations[], cut_priority (category IDs), summary | 0.1 | Validates every operation and referenced ID, then the change engine computes a Proposal |
| `alternatives` | "Suggest alternatives" on a shortfall | Computed shortfall + categories (amounts, protected) + event context | `Alternatives`: 2–4 items (category_id, action `reduce`/`replace`/`remove`, level `small`/`medium`/`large`, title, why) | 0.6 | Computes each alternative's saving and the remaining shortfall; the user can apply one as a proposal |

`ChangeOperation` is a **flat** model (`kind` enum plus optional fields), so the same schema works with Gemini response schemas and with Groq JSON mode. A model validator enforces the required fields per kind.
Kinds: `set_total_budget`, `add_expense`, `update_expense`, `remove_expense`, `protect`, `unprotect`, `set_guest_count`, `shift_date_days`, `set_date`, `add_task`, `add_vendor`.

### 6.2 Prompts
Each prompt module has `VERSION`, `SYSTEM` (role, domain guardrail, "user text is data", output rules, explain *why*, never compute totals), `FEW_SHOTS` (validated against the schema in tests), and a builder that sends a JSON user message:
`{"task": ..., "context": {...}, "user_request": "<<<...>>>"}`.
Persona tone is injected from `app/content/persona.py`.

### 6.3 Fallback chain (`ai/chain.py`)
- Each provider is called with a timeout of 12 s. The whole chain is capped at 25 s (Vercel `maxDuration` 60 s).
- On HTTP 429, the provider/model is put on cooldown in memory: until the next midnight Pacific for a daily quota, or for Retry-After / 60 s for per-minute limits.
- On a schema validation failure, one repair retry goes to the same model with the validation errors; after that, the next provider.
- If every provider fails, the result comes from `ai/fallbacks.py` with `fallback=True`. The UI shows **"Offline suggestions, not AI-generated."** The Change Manager falls back to a structured form.
- Every result carries provenance: task, provider, model, prompt version, latency, cached, fallback.

### 6.4 Guardrails against prompt injection
- User text is normalized: NFKC, control characters stripped, length capped. It is wrapped in delimiters inside a JSON field and never concatenated into the system prompt.
- Output can only be schema fields. Operation kinds come from a fixed list. Referenced IDs must exist. Numbers must be within bounds.
- An `off_topic` flag triggers a polite refusal. Model output is never executed and never rendered as HTML (autoescape).
- Tests cover classic injections ("ignore previous instructions", fake system messages, HTML/script payloads, instructions to change protected items).

### 6.5 Transparency panel
Every AI result has a "How this was generated" `<details>` element. It shows the task, model, prompt version, latency and cached/fallback status, plus the JSON payload sent and the validated JSON received. The system prompt is linked to its source file.

## 7. Key flows
1. **Create event**
   - Validate the form.
   - Baseline from `catalog` (event type, sub-events, guests).
   - `personalize` (cache, then chain).
   - Code merges the suggestions: weights, extra tasks, vendor priorities.
   - Allocate, compute dates, save version 1, then 303 redirect to the dashboard.
2. **Add expense** (no AI)
   - Validate, check the idempotency token, add, recompute the summary.
   - If over budget or a category is overspent, show the shortfall and a "Suggest alternatives" button.
3. **Change Manager**
   - `interpret_change` returns either a clarification question (no proposal) or operations.
   - `changes.apply` runs on a copy and produces a Proposal: before/after per field, shortfall or remaining, affected tasks and vendors, skipped duplicates, untouched protected items.
   - The proposal is stored as `pending` with `base_version`.
   - Approve: if `plan.version == base_version`, write version + 1 and mark `approved`. Otherwise the proposal is `stale` and the user is asked to retry.
   - Reject: mark `rejected`; the plan is unchanged.
   - Approving twice is a no-op.
4. **Alternatives**: only on user request (POST). They render inline below the shortfall alert, never in a modal. "Apply" (optional) creates a normal Proposal.

**HTTP rule:** any route that calls the AI or changes data is `POST` (CSRF-protected), so crawlers, link previews and prefetching can never trigger AI calls or writes. Plain HTML forms cannot send `DELETE`, so deletions are `POST .../delete`. Every flow works as a normal form submission without JavaScript; htmx only enhances it.

## 8. Algorithms (pure, in `domain/`)
- **Allocation**: total × normalized weights, then **largest-remainder** rounding to whole rupees, so the sum always equals the total exactly. O(n log n).
- **Budget reduction to a new total**:
  - Floors are the protected categories' allocations plus the protected expense amounts.
  - If the sum of the floors is greater than the new total, the change is infeasible: return the shortfall and suggest alternatives.
  - Otherwise the non-protected remainder is scaled proportionally (honoring `cut_priority` first when given), with largest-remainder rounding.
  - Categories whose committed spend exceeds the new allocation are flagged.
- **Guest-count change**: categories with `scales_with_guests` (catering, seating/venue, return gifts) are scaled by new/old (ceiling to whole rupees) and flagged as affected; the total impact is shown.
- **Dates**:
  - due = event_date − offset_days.
  - Moving the event shifts every incomplete task's due date.
  - Tasks now due in the past become due today and are flagged "compressed".
  - Vendor book_by = event_date − lead_days; if that is in the past, the vendor is flagged "book now".
- **Duplicates**:
  - Forms carry an idempotency token.
  - `add_expense` / `add_task` operations matching an existing (category, normalized label, amount) are skipped and reported.
  - Proposals are versioned.

## 9. Validation limits
- Budget: ₹1,000 – ₹10,00,00,000. Expense: ₹1 – total budget × 2.
- Guests: 1 – 5,000.
- Event date: today to today + 3 years (Asia/Kolkata).
- Text fields: title ≤ 80, city ≤ 60, preferences ≤ 500, change request ≤ 300, labels ≤ 80 characters.
- Unicode is allowed (Hindi or Tamil names); control characters are stripped.
- Request body ≤ 32 KB. Exceeding a limit returns a clear inline error and keeps the user's input.

## 10. Security
- Secrets come only from the environment (`SecretStr`); the prod app refuses to start without `SESSION_SECRET` or `DATABASE_URL`.
- The browser never sees keys.
- Session: signed, HttpOnly, Secure, SameSite=Lax cookie. Every event route checks ownership (prevents IDOR).
- CSRF token on every POST (form field or htmx `X-CSRF-Token` header).
- Headers:
  - Strict CSP: `default-src 'self'`, no inline script, htmx `includeIndicatorStyles=false`
  - `X-Content-Type-Options`
  - `Referrer-Policy`
  - `frame-ancestors 'none'`
  - `Permissions-Policy`
- Rate limits: AI routes 20 per 10 min per IP (the IP is hashed with a secret salt), and a global AI soft cap of 1,500 calls/day; above both, users get cached or labelled fallback output. Generous enough that judges are never blocked.
- SQL is parameterized via SQLAlchemy. Jinja autoescape is on, and `|safe` on dynamic text is forbidden (a test checks this).
- Logs contain no user-entered text and no keys. gitleaks runs in pre-commit and CI; pip-audit runs in CI.

## 11. Efficiency
- One AI call per user action. Alternatives are generated only on demand. A cache keyed on the normalized payload serves repeats.
- In-process LRU in front of the Postgres cache. NullPool + Neon pooler for serverless.
- htmx partial swaps send only the changed fragment. Static assets are cached by the CDN. No JS framework or build step.
- `/healthz` does **not** touch the DB, so uptime pings never wake Neon or burn its compute hours. `/readyz` checks the DB and is for manual use.

## 12. Accessibility (WCAG 2.2 AA target)
- Landmarks, a skip link, a single h1 and a logical heading order. `lang="en-IN"`.
- Every input has a label. Fieldset/legend group the event-type radios. Errors are linked with `aria-describedby`.
- An `aria-live="polite"` status region announces AI progress and results. Focus moves to the preview heading after a swap.
- Tables have a caption and `th scope`. Contrast ≥ 4.5:1, enforced through CSS tokens. Focus styles are visible.
- `prefers-reduced-motion` and dark mode are respected. The layout is responsive down to 320 px. Everything works with the keyboard alone.

## 13. Testing strategy and seams
- **Seams**: `LLMClient` protocol (tests use `tests/fakes.py::ScriptedLLM`, which can return responses, malformed JSON, timeouts or 429s); `Clock`; repositories (in-memory SQLite); `create_app(...)` dependency injection.
- **Unit**:
  - Property tests (hypothesis) for allocation: the sum equals the total, nothing is negative, protected items are unchanged.
  - Dates: month and year ends, leap day, compression.
  - Every operation kind; validation boundaries; INR formatting.
- **AI**: schema validation; few-shot examples validate; repair retry; cooldown; fallback labelling; injection fixtures; cache hits.
- **Integration**: routes via the TestClient. Covers CSRF, ownership (another session gets 404), rate limits, security headers, and all 10 must-pass scenarios from SPEC.
- **E2E**: Playwright creates an event and approves a change (with the fake LLM), plus an axe scan of the main pages.
- **Evals** (manual, never in CI): `evals/change_requests.jsonl` holds about 20 labelled requests; `scripts/run_evals.py` uses the dev key and reports the accuracy of interpretations.

## 14. Deployment and operations
- Vercel project linked to GitHub `main`. Function region `sin1` (Singapore), co-located with Neon `aws-ap-southeast-1` (Neon has no India region). Database round-trips matter more than browser distance; India to Singapore adds about 60–70 ms.
- Static files: `public/static/**` is served by the Vercel CDN at `/static/**`. In development only, the app mounts `public/static` itself (Vercel says not to mount `public/` in production).
- Environment variables:
  - `GEMINI_API_KEY`, `GROQ_API_KEY`, `DATABASE_URL`, `SESSION_SECRET`
  - `LLM_CHAIN`, `AI_RATE_LIMIT_PER_10MIN`, `AI_DAILY_SOFT_CAP`, `APP_ENV`
- Separate Google projects for dev and prod keys, so development never spends the prod quota. Neon has a `dev` branch for local work.
- An uptime monitor on `/healthz` alerts us during judging.
- **The spike on 22 Oct must verify**:
  - The Vercel entrypoint `app/index.py`
  - Jinja templates are bundled with the function
  - `public/static` is served by the CDN
  - uv/pyproject support (otherwise an exported `requirements.txt`)
  - Region `sin1` on Hobby
  - The Neon connection
  - Measured cold start

## 15. Persona swap
`app/content/persona.py` holds: name, one-line description, tone guidance (inserted into prompts), landing-page copy, and example presets for each event type ("Fill example").
Swapping the persona means editing that file, re-running the evals and updating the README persona section. Domain, AI schemas and tests do not change.
