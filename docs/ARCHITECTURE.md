# Architecture: Utsav Planner

Status: v1.3, decided 21 Oct 2026 and synced with the code on 23 Oct 2026 (evening). This document is
binding on all agents (see `AGENTS.md`). Items marked **(planned)** are designed but not built yet.

## 1. Principle
**AI interprets, reasons and generates. Code calculates and enforces. The user approves.**

| GenAI does | Code does |
|---|---|
| Turns a free-text change request into structured operations, or asks one clarifying question | All money and date arithmetic |
| Personalizes a drafted plan: ideas, budget emphasis, extra tasks, vendor notes, each with a "why" | Validation of every input and every AI output |
| Suggests ways to close a shortfall, with reasons | Protected items, exact totals, idempotency and versioning |
| Declines off-topic requests and prompt-injection attempts | Persistence, access control, rate limits, caching |

The AI never writes to storage and never produces a number that code trusts without checking.
It copies numbers the user states; relative requests ("10 more guests", "cut ₹2,000") use
`adjust_*` operations so that code, not the model, does the arithmetic.

## 2. System context
```
Browser (HTML + htmx, no secrets)
   │  HTTPS, forms / htmx partial requests, CSRF token, signed session cookie
   ▼
Vercel Function: FastAPI app (region sin1, Singapore)
   ├── web/         routes, security headers, Jinja2 templates
   ├── services/    use cases: planning, change manager, edits, alternatives
   ├── domain/      pure logic: catalog, budget, timeline, vendors, changes
   ├── ai/          Provider protocol → FallbackChain → Groq / Gemini
   └── persistence/ engine and repositories
        │                                  │
        ▼                                  ▼
Neon Postgres (aws-ap-southeast-1)   Groq API (primary) → Gemini API (backup)
Static assets: public/static/** served by the Vercel CDN
```

## 3. Decision log
| # | Decision | Choice | Why (rubric impact) | Rejected |
|---|---|---|---|---|
| D1 | Backend | FastAPI + Pydantic v2, sync endpoints | Types end to end; Pydantic schemas both request and validate AI output. Matches the developer's strength. | Express + Zod; Next.js (frontend LLM-call risk, two languages) |
| D2 | Frontend | Server-rendered Jinja2 + htmx 2 (vendored, checksum-tested) + hand-written CSS | Core content visible without JS; semantic HTML makes a11y cheap; tiny payload; one language. | React SPA; Alpine.js |
| D3 | Storage | Neon Postgres via SQLAlchemy 2 + psycopg 3, NullPool, pooled connection string | Serverless-friendly, free with no card, no 7-day pause; one service for plans, history, AI cache and rate limits. | Firestore; browser-only storage; Supabase (pauses) |
| D4 | Hosting | Vercel Hobby, Fluid compute, FastAPI zero-config | Free, no card. Measured: cold start 2.1 s for `/`, warm 0.25–0.4 s, from India. | Cloud Run (needs billing); Render (30–60 s cold start); Koyeb |
| D5 | LLM | Groq `openai/gpt-oss-120b` → Gemini 3.5 Flash-Lite → Gemini 3.1 Flash-Lite → labelled non-AI fallback, via `LLM_CHAIN` | Evals on 22 Oct 2026 (`evals/RESULTS.md`): Groq passed 36/36 change requests in both runs with a median of about 1 s, while Gemini 3.5 took 4–5 s and gave no answer to 5–10 of 36 change requests during Google's capacity problems. Groq's free tier (8K tokens/minute, 200K/day) runs out first, but a quota refusal is instant, so Gemini still answers without waiting. Two vendors, three models. | Gemini first (the original order, changed on 22 Oct after the evals); Gemini only; racing providers |
| D6 | Failure policy | 10 s per call, same-vendor skip after a timeout, circuit breaker, quota cooldowns, one repair, labelled fallback, writes only on approval | The app is never broken or dishonest at judging time. | |
| D7 | AI transparency | "How this was generated" panel on every AI result: model, prompt version, attempts, the JSON sent and the validated answer | Proves genuine, dynamic GenAI to reviewers and in the video. | Badge only |
| D8 | Sessions | Anonymous signed-cookie session (one year, renewed on every visit; HttpOnly, SameSite=Lax, Secure in production); events owned by a hash of the session id | Each judge gets private data with no login. | Accounts; public event IDs (IDOR) |
| D9 | Tooling | uv, ruff, mypy --strict (app and tests), pytest + hypothesis + doctests, 95 % branch-coverage gate, Playwright + axe, pre-commit, GitHub Actions, AI evals | Visible quality signals; edge cases covered. | |

## 4. Modules
```
app/
  index.py              Vercel entrypoint (`[tool.vercel] entrypoint = "app.index:app"`)
  main.py               create_app(settings, engine): dependency-injection root
  config.py             Settings (SecretStr); production fails fast without DATABASE_URL or a strong SESSION_SECRET
  domain/               pure: no I/O, no global clock
    clock.py            Clock protocol, SystemClock (Asia/Kolkata)
    errors.py           DomainError with stable codes: InvalidInputError, NotFoundError, StaleProposalError, InfeasibleChangeError
    text.py             normalize_text (keeps ZWJ/ZWNJ for Indian scripts, strips control and bidi-override characters)
    money.py            format_inr (lakh/crore grouping), largest_remainder_split (exact, no floats)
    models.py           EventPlan and parts (frozen, tuples, typed ids), limits, evolve(), id_sequence()
    catalog/            India catalog: one module per event type (wedding, birthday, community), specs, vendor questions; sources cited in __init__
    baseline.py         opening budget from event type, sub-events and guest-count bands
    budget.py           allocate, apply_emphasis, summarize, the total-budget rule
    expenses.py         add, update and remove expenses; protection; duplicate detection
    rebalance.py        rebalance() to a new total with pins, protection and reason codes
    timeline.py         deadlines, compression, rescheduling
    vendors.py          book-by dates, urgency, priority, status changes
    calendar.py         plan_calendar(): event day, open tasks and vendors to book as RFC 5545 all-day entries (F14)
    planning.py         EventDetails, draft_plan(): complete plan without AI
    change_set.py       ChangeKind, ChangeOperation, ChangeSet: the language of plan changes
    proposal.py         Proposal and its parts (field, expense and schedule changes, skips)
    changes.py          propose() → Proposal, accept(): applies a ChangeSet in a fixed order
    savings.py          SavingAction/Level, estimate_saving, saving_change(), freed_inr(): ways to save
  ai/
    client.py           Provider protocol, typed errors, LLMRequest, AITask, Provenance, AIResult
    providers/          gemini.py (SDK loaded on first call), groq.py (httpx), build_providers()
    chain.py            FallbackChain: cache, limits, skip-after-timeout, circuit breaker, cooldowns, repair, fallback
    cache.py            CacheStore protocol, MemoryCache (TTL + LRU), TieredCache (memory, then Postgres)
    contexts.py         PersonalizeContext, ChangeContext, AlternativesContext
    schemas.py          PlanSuggestions, ChangeIntent, Alternatives
    prompts/            personalize.py, interpret_change.py, alternatives.py (VERSION, SYSTEM, FEW_SHOTS, build_payload)
    guardrails.py       quoting user text, plan snapshot, output checks, intent_to_change_set()
    fallbacks.py        deterministic answers labelled "not AI-generated"
    tasks.py            PERSONALIZE, INTERPRET_CHANGE, SUGGEST_ALTERNATIVES
  services/             planning (draft, personalize), change_manager (request, form, approve, reject, history),
                        edits (expenses, protection, tasks, vendors), alternatives; build_services()
  content/persona.py    persona copy, tone and presets (planned)
  persistence/
    db.py               engine (NullPool, psycopg 3), readiness check
    tables.py           events, proposals, ai_cache, rate_limits
    repositories.py     Database.transaction(): one unit of work per request
    events.py           EventRepository: ownership enforced in SQL, optimistic versioning
    proposals.py        ProposalRepository: pending, approved, rejected and stale previews
    cache_store.py      SqlCacheStore: the AI answer cache in Postgres
    rate_limits.py      RateLimiter: per-client counters
    common.py           utc_now, owner_key (hashed session), dialect-aware upsert
  web/
    routes/health.py    /healthz (no database), /readyz (database)
    routes/             pages (/, favicon), events (create, dashboard, personalize), edits, changes
                        (request, form, approve, reject, alternatives)
    deps.py             services, session, today, AI rate limit
    guard.py            CSRF check (header or form field) and 32 KB body limit
    session.py, limits.py, errors.py   session id, CSRF token, flash messages; AI rate limit; error pages
    views/              view models, one module per concern: formatting, forms, landing, dashboard, proposals
    responses.py        redirect-after-POST or htmx fragment
    security.py         security headers middleware (CSP, HSTS in production)
    templating.py       Jinja2 with autoescape and StrictUndefined
    templates/          base, landing, dashboard, error and feature partials (contract: docs/UI_CONTRACT.md)
public/static/          css/app.css (with print rules), js/ (theme, sections, reel, print, keep-place, forms), img/ (favicon.svg, celebrations/ photos), vendor/htmx.min.js (+ VENDOR.md checksum)
scripts/                hooks/ (pre-commit checks), ai_smoke.py (manual live check), init_db.py
evals/                  scored cases for the three AI tasks, runner, report (evals/RESULTS.md)
.github/workflows/      ci.yml: hooks and tests, browser and axe tests, secrets, dependencies and size
```
Dependencies point **inward**: web → services → (domain, ai, persistence). `domain` imports nothing from the app.

## 5. Data model
`EventPlan` is one frozen Pydantic document (stored as JSON next to its `version` column):
- event_type (`wedding` | `birthday` | `community`), title, city, event_date, guest_count, total_budget_inr, preferences (≤ 500), sub_events, version
- `categories`: id, name, weight (relative), allocated_inr, scales_with_guests, protected, custom
- `expenses`: id, category_id, label, amount_inr, protected, source (`user` | `change`)
- `tasks`: id, title, category_id, offset_days (−30 to 1,095; negative is after the event), due_date, done, compressed, source (`catalog` | `ai` | `user` | `change`), why
- `vendors`: id, vendor_type, name, category_id, status (`todo` | `contacted` | `booked`), lead_days, book_by, priority, urgent, why, notes
- `ideas`: id, title, description, category_id, budget_impact (`saves` | `neutral` | `adds`), why

Invariants checked on every construction: unique ids per collection, every reference points to an
existing category, and category allocations sum exactly to the total budget.

Tables: `events` (id, owner_session, version, plan JSON, AI provenance JSON, timestamps),
`proposals` (id, event_id, base_version, request_text, intent JSON, proposal JSON, status
`pending|approved|rejected|stale`, timestamps; this is the **change history**), `ai_cache`
(key, response JSON, provider, expires_at), `rate_limits` (bucket, window_start, count).
Form idempotency tokens are not built: a repeated submission is caught by duplicate detection
(see §8) and proposals can be decided only once.

## 6. AI design
### 6.1 Tasks
There is exactly one LLM call per user action, and answers are cached.

| Task | Trigger | Input | Output | Temp | Code then does |
|---|---|---|---|---|---|
| `personalize` | Right after an event is created; "Suggest different ideas" (F12) | Event details (quoted user text), category ids, existing tasks, vendor types, tone; for F12 also `previous_ideas` (quoted titles), cache bypassed | `PlanSuggestions`: 3–5 ideas, ≤ 4 budget emphases (increase/decrease × small/medium), ≤ 5 extra tasks, ≤ 6 vendor notes, each with a why; `off_topic` | 0.8 | Emphasis → bounded weight change (±10 / ±20 %) → exact allocation; tasks deduplicated and dated (`personalize_plan`). F12 keeps only ideas whose titles differ from `previous_ideas` and replaces just the plan's ideas; budget, tasks and vendors change only through approved proposals |
| `interpret_change` | Change request | Plan snapshot (ids, amounts, protected flags) + quoted request + today | `ChangeIntent`: status `ok` / `needs_clarification` / `off_topic`, operations, cut_priority, summary | 0.1 | Converted to a domain `ChangeSet`, then `propose()` computes the Proposal |
| `alternatives` | "Suggest alternatives" on a shortfall | Plan snapshot + shortfall + `frees_inr`: rupees each option would free per line, computed by `freed_inr()` | `Alternatives`: items (category_id, reduce/replace/remove, small/medium/large, title, why) | 0.6 | `saving_change()` sets the line to its plan minus `estimate_saving`, never below what is spent on it; the saving and the gap left come from `propose()` on that change, and applying the option creates that proposal (F13) |

Operation kinds (flat `ChangeOperation`, each kind checks its own fields): `set_total_budget`,
`adjust_total_budget`, `add_expense`, `update_expense`, `remove_expense`, `protect` / `unprotect`
(exactly one of `category_id` or `expense_id`), `set_guest_count`, `adjust_guest_count`,
`shift_date_days`, `set_date`, `add_task`, `add_category`, `set_category_budget` (the line is
pinned at the new amount and the other unprotected lines are rebalanced; a line protected
before the request is skipped; set and protect in one request keeps the new amount). At most
five per request.

### 6.2 Prompts
Each prompt module has `VERSION`, a structured `SYSTEM` prompt (role, task, output rules and the
shared guardrails) and `FEW_SHOTS` validated in tests. The user message is JSON, for example
`{"task": "interpret_change", "today": ..., "plan": {...}, "request": "<<<...>>>"}`; examples are
sent as prior turns. A test fails if the domain gains an operation kind the interpret prompt does
not document. Measured prompt sizes (v1): personalize ≈ 1.7k tokens, interpret ≈ 2.2k, alternatives ≈ 1.0k;
v2 adds one interpret example (amounts on expense and budget-line operations) and gap-sizing rules
for alternatives, both prompted by the evals (`evals/RESULTS.md`).
Tone is a context field (default in `ai/contexts.py`; persona file planned).

### 6.3 Fallback chain (`ai/chain.py`)
- Cache first (SHA-256 of task, prompt version and canonical payload; 7 days for personalize,
  1 day otherwise). Fallback answers are never cached.
- 10 s per call (Gemini rejects server deadlines under 10 s), 25 s for the whole chain.
- After a timeout, the vendor's other models are skipped for that request; the next vendor gets the time.
- HTTP 429: cooldown until midnight Pacific for daily quotas, or the provider's retry delay (60 s default).
- Circuit breaker: two consecutive outages (5xx, network, rejected request) rest a provider for 30 s;
  a success resets the count.
- Invalid answer: one repair attempt on the same model, telling it exactly what was wrong; then the next provider.
- Everything failed: the task's fallback, marked `fallback=True` and labelled "not AI-generated".
- Every result carries provenance: task, prompt version, provider, latency, cached, fallback, each attempt and its outcome, and the request payload.

### 6.4 Guardrails against prompt injection
- User text is normalized, length-capped and quoted with `<<< >>>`; delimiters inside it are removed so it cannot close its own quotes.
- Answers can only be schema fields; operation kinds come from a fixed list.
- Checks against the plan: lenient for personalize and alternatives (invalid items dropped; an alternative on a protected line or one with nothing unspent is invalid), strict for change interpretation (unknown ids rejected with reasons for one repair; clarification and off-topic answers never carry operations).
- The domain then applies its own rules: protected items are skipped and reported even if an answer targets them (tested with a "compromised model" answer).
- Model output is never executed and never rendered as raw HTML (autoescape).

### 6.5 Transparency panel
Every AI result gets a "How this was generated" `<details>` element showing the provenance above,
the JSON sent and the validated JSON received, and a link to the prompt's source file.

## 7. Key flows
1. **Create event**: validate `EventDetails` → `draft_plan()` (catalog, opening budget, dated tasks and vendors) → save version 1 → redirect to the dashboard at once → the dashboard requests `personalize` (htmx `load`, or a button without JavaScript) → `personalize_plan()` merges the suggestions → save version 2. The page is complete before the AI answers.
2. **Add expense** (no AI): validate, add, recompute the summary. Money is never moved between categories automatically; overspend or shortfall is shown with a "Suggest alternatives" button.
3. **Change Manager**:
   - `interpret_change` returns a clarification question, an off-topic notice, or operations.
   - `propose()` applies them to a copy in a fixed order: protection → date (deadlines move) → guests (scaled categories pinned) → expenses (duplicates and protected items skipped) → custom categories → one `rebalance` → user tasks and follow-up tasks for booked vendors.
   - The Proposal shows before/after values, per-category changes with reason codes, moved deadlines, new tasks, skipped items, protected items kept, and budget summaries, or the exact shortfall.
   - `accept()` saves version + 1 only if the plan is still at the proposal's base version; otherwise the proposal is stale. Rejecting needs no code: the saved plan is never modified.
4. **Alternatives**: only on user request (POST), shown inline below the shortfall alert. Each
   option has a "Preview this change" button that posts only its line, action and level; the
   server rebuilds the change from the saved plan (`ChangeManager.propose_saving`) and shows an
   ordinary proposal to approve or reject. An
   infeasible proposal gets no AI alternatives: it fails because protected lines and committed
   money exceed the new total, which trimming cannot fix, so the preview states the exact
   extra total needed and which protected lines could be released instead.

**HTTP rule:** any route that calls the AI or changes data is `POST` (CSRF-protected), so crawlers,
link previews and prefetching can never trigger AI calls or writes. Deletions are `POST .../delete`.
Every flow works as a plain form submission; htmx only enhances it.

## 8. Algorithms (pure, in `domain/`)
- **Allocation**: total × relative weights, **largest-remainder** rounding with a fixed tie-break, using exact fractions. O(n log n).
- **Opening budget**: catalog weight + sub-event bonuses, × guest band (≤ 50: 90 %, ≤ 200: 100 %, ≤ 500: 110 %, more: 120 %) for guest-scaled categories.
- **Rebalance** (`rebalance.rebalance`):
  - Protected categories keep their allocation; pinned categories get their pin, raised to their protected-expense floor if needed.
  - No category drops below the total of its protected expenses.
  - Extra money first covers categories whose spending exceeds their allocation (in proportion
    to each overspend); the rest is shared in proportion to current allocations (weights only
    as a fallback). So a raised total or a cut line closes a shortfall where it is.
  - Cuts come from `cut_priority` categories first, then in proportion to what each category can give.
  - If floors and pins exceed the total, the result is infeasible with the exact shortfall.
  - Every category gets a reason code: `unchanged`, `added`, `protected`, `guest_scaled`, `cut_first`, `proportional`, `floor_reached`.
- **Guests**: guest-scaled categories are pinned at ⌈allocation × new / old⌉; the total stays fixed. When guests decrease and nothing else changes the budget, the freed money goes to contingency (proportional if contingency is missing or protected).
- **Dates**: due = event date − offset; a deadline already past becomes today and is flagged compressed. Book-by = event date − lead time, flagged urgent if past. Moving the event reschedules open tasks and unbooked vendors only.
- **Vendors**: priority = unbooked first, then earliest book-by, then longer lead time. Status moves: to-do → contacted/booked; contacted → to-do/booked; booked → contacted.
- **Follow-ups**: a date change adds "Confirm the new date with …" for each booked vendor; a guest change adds "Update …: new guest count …" for booked vendors in guest-scaled categories. Both are due today.
- **Duplicates**: a repeated expense (same category, label ignoring case, amount) or task title is skipped and reported; proposals are versioned.

## 9. Validation limits
- Budget: ₹1,000 – ₹10,00,00,000. Expense: ₹1 – twice the total budget.
- Guests: 1 – 5,000. Event date: today to three years ahead (Asia/Kolkata).
- Text: title ≤ 80, city ≤ 60, preferences ≤ 500, change request ≤ 300, labels ≤ 80 characters.
- Up to five operations per change request; at most three custom budget lines per plan.
- Unicode is allowed (Hindi, Tamil, Malayalam), including joiners; control and bidi-override characters are removed.
- Request body ≤ 32 KB. Errors are shown inline in plain language and keep the user's input.

## 10. Security
- Built:
  - Secrets come only from the environment (`SecretStr`); production refuses to start without `DATABASE_URL` or a 32+ character `SESSION_SECRET`.
  - The browser never sees keys; all AI calls are server-side.
  - Headers on every response: strict CSP (`default-src 'self'`, no inline script or style, no eval), `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, COOP, HSTS in production; `nosniff` on CDN static files.
  - Jinja autoescape and StrictUndefined. `/readyz` never reveals why the database is unavailable.
  - Provider errors and logs carry codes and statuses only, never keys or user text.
  - gitleaks, file-size, branch and `.env` guards in pre-commit; htmx checksum test.
  - Signed session cookie; every event and proposal query is filtered by owner in SQL, so another session gets 404 (tested in the browser too).
  - CSRF token on every POST (header for htmx, hidden field without JavaScript); 32 KB body limit.
  - AI rate limits: 20 calls per 10 minutes per HMAC-hashed IP and a global soft cap of 1,500 a day; over the limit, the app answers with labelled standard suggestions instead of failing.
  - CI: gitleaks over the full history, pip-audit on production dependencies, actions pinned to commit SHAs, read-only token.

## 11. Efficiency
- One AI call per user action; alternatives only on demand; cached answers reused.
- The circuit breaker and cooldowns stop wasted calls to failing or rate-limited providers.
- The Gemini SDK is imported on first use (0.4 s), keeping cold starts small for pages without AI.
- NullPool + Neon pooler for serverless. htmx swaps send only changed fragments. No JS build step.
- `/healthz` never touches the database, so uptime pings don't wake Neon.

## 12. Accessibility (WCAG 2.2 AA target)
- Built in `base.html` and `app.css`: `lang="en-IN"`, skip link, landmarks, `aria-live` status region, visible focus styles, reduced motion, dark mode, responsive to 320 px, contrast tokens checked by a test for every colour pair as used, in both themes.
- Built per feature: labels for every input, fieldset/legend for choices, errors linked with `aria-describedby`, table captions and `th scope`, screen-reader names on repeated buttons, keyboard-scrollable data panels.
- Checked in a real browser by axe (WCAG 2.2 A/AA) on six page states, light and dark, phone and desktop.
- After an htmx swap, a change preview takes focus (`autofocus`) and is announced through the
  status region; plan buttons keep stable ids, so focus returns to the same button.

## 13. Testing strategy and seams
- **Seams:** `Provider` protocol (`tests/fakes.py::ScriptedProvider` scripts answers, timeouts, 429s and outages; `FakeClock` controls time), `Clock` (`FixedClock`), injected engine (in-memory SQLite), `create_app(settings, engine)`.
- **Unit and property tests:** money, text, models, catalog, baseline, budget, timeline, vendors, planning, changes; Hypothesis checks exact totals, protection and immutability over thousands of generated plans and requests (a reproducible `ci` profile and a 5,000-example `explore` profile).
- **AI:** chain paths (cache, timeout, vendor skip, cooldowns, circuit breaker, repair, fallback, time budget), adapters (fake SDK client, httpx mock transport), prompts (examples validate; every operation kind documented), guardrails and injection cases, fallbacks, and the full loop from a model answer to an approvable proposal.
- **Doctests** run in `app/domain` and `app/ai`. A 95 % branch-coverage gate applies to `app/`.
- **Live smoke** (manual, dev keys): `python -m scripts.ai_smoke`.
- **Web:** HTTP flow tests for every route with and without htmx, including CSRF, ownership and rate limits, plus a label audit of every page and fragment.
- **Browser** (`pytest -m e2e`): Playwright journeys with and without JavaScript; axe; 320 px overflow; skip link; a console free of errors and CSP violations. The app runs in a thread with a task-aware fake AI and a temporary SQLite file.
- **AI evals** (`evals/`, real models, run by hand): 63 cases scored by code through the guardrails and change engine; results in `evals/RESULTS.md`.
- **CI** runs all of the above except the evals on every push to `main`.

## 14. Deployment and operations
- Vercel project linked to GitHub `main`; production URL `https://utsav-planner.vercel.app` (the team-scoped alias is behind Vercel SSO and is never shared).
- Function region `sin1`, next to Neon `aws-ap-southeast-1` (Neon has no India region).
- `public/static/**` is served by the CDN; the app mounts it only in development.
- Environment: `GEMINI_API_KEY`, `GROQ_API_KEY`, `DATABASE_URL`, `SESSION_SECRET`, `LLM_CHAIN`, `AI_RATE_LIMIT_PER_10MIN`, `AI_DAILY_SOFT_CAP`, `APP_ENV`.
- Separate Google projects for dev and prod keys; a Neon `dev` branch for local work.
- Spike results (22 Oct, verified): entrypoint, template bundling, CDN static files, uv/pyproject builds, region `sin1`, Neon connection; cold start 2.1 s (`/`), `/readyz` 0.37–0.63 s warm.
- Planned: uptime monitor on `/healthz` during judging.

## 15. Persona swap (planned)
`app/content/persona.py` will hold the persona's name, description, tone (passed to prompts as the
`tone` field), landing copy and example presets. Swapping the persona changes that file and the
README persona section only; domain, AI schemas and tests stay the same.
