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

## 2026-10-21: Frontend foundations and accessible base template (SPIKE-A01)
- **Tool / model:** Antigravity (Gemini 3.8 Flash).
- **Prompt (summary):** "Vendor htmx 2.x pinned minified at public/static/vendor/htmx.min.js. Create public/static/css/app.css with design tokens (>= 4.5:1 contrast, spacing, type scale), light/dark themes via prefers-color-scheme, :focus-visible, prefers-reduced-motion, responsive down to 320 px. Create app/web/templates/base.html with lang='en-IN', skip link, landmarks, aria-live='polite' region, title/content blocks, deferred htmx, includeIndicatorStyles: false, no inline scripts/styles."
- **Outcome:**
  - Vendored pinned htmx 2.0.4 with 0BSD license comment preserved (50 KB).
  - Designed `app.css` tokens with all tested color pairings achieving >= 5.1:1 contrast (WCAG AA), responsive layout down to 320 px, skip link, and focus rings.
  - Implemented semantic `base.html` adhering to strict CSP with zero inline scripts or styles.
- **Next iteration:** Claude Code lane to add integration tests for template landmarks, skip links, and static asset delivery.

## 2026-10-21: Review of SPIKE-A01, then templates and security headers (SPIKE-C03)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Antigravity finished SPIKE-A01. Review it, then do C03."
- **Outcome (review):**
  - The vendored htmx body is byte-identical to the official 2.0.4 file. It differs only by an added license comment and a trailing newline that a pre-commit fixer added.
  - Recomputed contrast: every pair passes except dark-mode muted text on the subtle surface, at 4.06:1 (below 4.5:1). The handoff's "all pairs >= 5.1:1" claim was not accurate for this pair.
  - Other findings: redundant ARIA roles on native landmarks; htmx 2.0.11 is the current release.
- **Outcome (C03):**
  - Jinja2 environment with autoescape and StrictUndefined. `/` renders `base.html`.
  - Pure-ASGI security headers: strict CSP with no inline script or style and no eval, plus nosniff, frame denial, referrer and permissions policies, and HSTS in production only.
  - Template accessibility-contract tests. Static files are mounted in development only.
  - Pre-commit fixers now skip `public/static/vendor/`, so vendored files stay byte-identical to upstream.
- **Next iteration:** send the review fixes back to the Antigravity lane (contrast token, ARIA roles, htmx upgrade with a recorded checksum, favicon, meta description).

## 2026-10-21: Fix SPIKE-A01 review findings (contrast, htmx 2.0.11, favicon, meta, roles)
- **Tool / model:** Antigravity (Gemini 3.8 Flash).
- **Prompt (summary):** "Fix SPIKE-A01 review findings in the Antigravity lane: adjust tokens so all text/bg pairs >= 4.5:1 and UI borders/focus >= 3:1 (list ratios in handoff); remove redundant roles from header, nav, footer; replace htmx with unmodified official 2.0.11 and record version/URL/sha256 in VENDOR.md; add SVG favicon (< 2 KB) and link in base.html; add one-sentence non-persona meta description."
- **Outcome:**
  - Token contrast updated in `app.css`: dark `--color-text-muted` to `#cbd5e1` (6.94:1 on subtle), dark `--color-border` to `#94a3b8` (>= 4.02:1 on all surfaces), light `--color-border` to `#767676` (>= 3.99:1 on all surfaces), dark `--color-error` to `#fca5a5` (>= 5.43:1 on all surfaces). Every pair meets or exceeds WCAG AA.
  - Native landmark elements cleaned up in `base.html` with redundant ARIA roles removed while retaining `role="list"`.
  - Pinned unmodified `htmx.org@2.0.11` (52,182 bytes, SHA-256 `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717`) vendored and documented in `public/static/vendor/VENDOR.md`.
  - Festive diya SVG favicon created at `public/static/img/favicon.svg` (1,156 bytes) and linked in `base.html`.
  - Non-persona `<meta name="description">` added to `base.html`.
- **Next iteration:** Claude Code lane to add integration tests verifying the favicon link and meta description tag in `test_pages.py`.

## 2026-10-21: Spike deploy and review of the Antigravity fixes
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Here are the Vercel URLs" → measure the spike. Then "Antigravity finished the review fixes" → review them.
- **Outcome (deploy):**
  - Production `https://utsav-planner.vercel.app` is public. Requests enter at the Mumbai edge and run in Singapore (`bom1::sin1`).
  - Templates are bundled, `/static` is served from the CDN, and the CSP and HSTS headers are present.
  - Warm timings from India: `/` about 0.24 s; `/readyz` 0.37–0.63 s (it was 1.2–2.1 s from the laptop), with one 5.4 s outlier.
  - The team-scoped alias is behind Vercel SSO, so only the production URL is shared.
- **Outcome (review):**
  - htmx 2.0.11 is verified byte-identical to upstream.
  - Recomputing every token pair from the CSS found one remaining text pair below 4.5:1: light `--color-success` on `--color-surface-subtle`, at 4.41:1. It is sent back to the Antigravity lane.
- **Next iteration:**
  - Tests for the meta description and icon, plus a checksum test that ties `htmx.min.js` to `VENDOR.md`.
  - A `/favicon.ico` redirect.
  - `vercel.json` headers for `/static` (nosniff, one-day cache for vendored files).

## 2026-10-22: Domain foundations, models and budget engine (CC-DOM-01, 02, 03, 05)
- **Tool / model:** Claude Code (Claude Opus 5.5), plan-first; the developer approved the plan.
- **Prompt (summary):** "Plan approved, but review it again and tell me if it can be improved." Then: "Fold them in and let's start."
- **Self-review changed the plan before any code:**
  - Text normalization must keep the zero-width joiner and non-joiner, which Indian scripts need. Only control characters and bidirectional overrides are stripped.
  - Category weights are relative, not "must sum to 10,000", which broke when sub-events vary.
  - `rebalance` returns a reason code for every category, so the preview can explain each number without an AI call.
  - Increases follow the user's current proportions.
  - Typed ids, tested docstring examples, 95 % branch-coverage gate, a reproducible Hypothesis profile, an immutability property, and error codes.
- **Outcome:**
  - Pure domain layer: clock, errors with codes, text normalization, INR formatting, exact largest-remainder splitting, frozen models with invariants, and the budget engine.
  - Worked example verified: ₹15,000 → ₹12,000 keeping the cake gives catering ₹3,846, decor ₹2,308, cake ₹2,000, other ₹3,846.
  - 145 tests, 99.86 % branch coverage. The property tests also passed a 5,000-example randomized run.
- **Bugs caught along the way:**
  - The editor wrote real bidirectional-control characters into `text.py` instead of `\u` escapes. Ruff (PLE2502) caught it, the file was fixed, and the repo was scanned clean. Tests now build such characters with `chr()`.
  - One test expectation was wrong (proportional cuts don't reach a floor unless spare capacity is exhausted). The test was fixed, not the code.
- **Next iteration:** CC-DOM-04 catalog (India-specific categories, tasks and vendor lead times), then timeline/vendors (DOM-06) and the change engine (DOM-07).

## 2026-10-22: India event catalog and opening budget (CC-DOM-04)
- **Tool / model:** Claude Code (Claude Opus 5.5), plan-first; the developer approved all four recommended decisions.
- **Prompt (summary):** "Write the DOM-04 plan." Then: "Approved, all recommended."
- **Research step:** before writing any numbers, a web search was run for Indian wedding budget splits, wedding and birthday timelines, and pandal permission windows.
  - The sources are mostly planning-industry guides, and their ranges vary.
  - Midpoints were used, with the links cited in the `catalog.py` docstring.
  - A test checks that the wedding split stays inside the published ranges (venue and catering 20–30 %, decor 10–16 %).
- **Outcome:**
  - Typed catalog for all three event types. Each has 8–11 budget categories with contingency at 5–10 %, 12–18 core tasks plus tasks per sub-event, vendors with lead times, 3–5 sub-events, and three non-AI fallback ideas with reasons.
  - The opening budget depends on the event type, the chosen sub-events (for example, a sangeet raises music) and guest-count bands (×0.9 to ×1.2 on per-guest costs).
  - Tests prove the output changes with context. City stays AI-only, to avoid unverifiable price tables.
  - Tasks may now fall up to 30 days after the event, for cleanup and settling accounts.
  - 187 tests, 99.88 % branch coverage.
- **Self-correction:** the first docstring claimed wedding lead times were shortened "to fit the three-year window". That was wrong; the real reason is that many families plan within a few months. Corrected before commit.
- **Next iteration:** DOM-06 (timeline and vendor dates, compression, book-now flags), then DOM-07 (change engine).

## 2026-10-22: Timeline, vendors and the opening plan (CC-DOM-06)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Write the plan for DOM-06 and DOM-07." The developer then asked, "Are we pre-defining the number of events, and where is GenAI?" and "We still handle last-minute changes, right?" Those questions added two items to the plan: custom budget categories (6) and automatic follow-up tasks for booked vendors (7).
- **Outcome:**
  - Deadlines are counted back from the event date. A deadline that has already passed becomes due today and is flagged as compressed.
  - Vendor book-by dates come from lead times. Vendors past their window are flagged "book now", and the checklist is ordered by urgency.
  - Moving the event date reschedules only open items (SPEC scenario 5).
  - Vendor status changes are rule-checked.
  - `draft_plan` builds a complete opening plan without AI. An event 10 days away gets compressed tasks and urgent vendors.
  - 215 tests, 99.90 % branch coverage. The draft-plan property also passed a deep randomized run.
- **Self-correction:** one assertion in the draft-plan test was always true (it checked a set against a union that already contained it). It was replaced with real checks before commit.
- **Next iteration:** DOM-07, the change engine.

## 2026-10-22: Smart Change Manager core (CC-DOM-07)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Yes, all recommended plus 6 and 7" (custom budget lines; follow-up tasks for booked vendors).
- **Outcome:**
  - `ChangeOperation` and `ChangeSet`: 11 operation kinds. Each kind checks its own fields, conflicting requests are rejected, and a request has at most five operations.
  - `propose()` applies a request to a copy of the plan in a fixed order and returns a `Proposal` with:
    - before/after values for the total, guests and date
    - category changes with reason codes
    - expense changes
    - moved deadlines and book-by dates
    - new and follow-up tasks
    - skipped duplicates and protected items, and the protected items that were kept
    - budget summaries
    - or the exact shortfall
  - `accept()` refuses stale or infeasible proposals.
  - Fewer guests sends the freed money to contingency. Booked vendors get due-today follow-up tasks when the date or headcount changes.
  - 252 tests, 99.92 % branch coverage.
  - Over 5,000 random plans and requests (69 % feasible, 22 % shortfall), the saved plan was never modified, protected items never moved, totals were exact, and approving twice was refused.
- **Bug caught by coverage:** after a guest decrease, categories held still (so the savings reach contingency) were treated as "scaled", so a booked decorator would have got a bogus headcount follow-up. Fixed by computing the scaled set before the holds. A regression test was confirmed to fail on the old code and pass on the fix.
- **Next iteration:** the AI layer (CC-AI-01 to 04). `interpret_change` must produce exactly this `ChangeSet`.

## 2026-10-22: AI layer, part 1 — real-API spike, client, fallback chain, cache (CC-AI-01, 04)
- **Tool / model:** Claude Code (Claude Opus 5.5). The developer approved the AI-layer plan ("all recommended").
- **Step 0, a spike with six real calls on the dev key:**
  - Gemini 3.5 Flash-Lite returned schema-valid JSON in 1.9–2.6 s.
  - Asking it for "low" thinking made it 3× slower (7.8 s) with the same answer, so thinking is left off.
  - Groq `gpt-oss-120b` rejects strict JSON-schema mode unless every object sets `additionalProperties: false`. Non-strict schema mode works in 1.0–1.3 s.
  - **Finding:** for "keep the cake", 3 of 5 answers put the id in `category_id` rather than the designed `target_id`. `protect`/`unprotect` now take `category_id` or `expense_id` (exactly one). Otherwise about 60 % of these requests would have needed a repair call.
- **Outcome:**
  - `Provider` protocol with typed errors. `AITask` bundles prompt, schema, plan checks and fallback. `Provenance` on every result.
  - `FallbackChain`: cache first; per-call and total time limits; daily-quota cooldown until midnight Pacific, retry delay for per-minute limits, and a short cooldown after outages; one repair attempt that tells the model what was wrong; a labelled fallback when everything fails. Fallbacks are never cached as AI answers.
  - `MemoryCache` with expiry and least-recently-used eviction, behind a `CacheStore` interface for Postgres later.
  - 270 tests, 99.94 % branch coverage.
- **Self-correction:** one chain test wrongly assumed a timeout benches a provider. It doesn't, by design (timeouts may be transient), so the test now asserts that the AI is retried.
- **Next iteration:** Gemini and Groq adapters.

## 2026-10-22: AI layer, part 2 — Gemini and Groq adapters (CC-AI-03)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Outcome:**
  - `GeminiProvider` (google-genai, imported on first use, with a per-request timeout) and `GroqProvider` (httpx, non-strict JSON-schema mode, `reasoning_effort` only for gpt-oss models).
  - Every failure is mapped to a typed error. Gemini 429s are read for daily vs per-minute quota (`PerDay` quota ids) and the `RetryInfo` delay. Groq 429s use `retry-after` and "per day / RPD" in the message.
  - `build_providers` follows `LLM_CHAIN`, skips providers without keys and fails fast on malformed entries.
  - Adapter tests use a fake SDK client and httpx's mock transport.
  - A live check through the real chain (dev keys) succeeded on both providers.
- **Measured:**
  - The Gemini free-tier latency varies widely for identical small calls: 1.75 s, 6.4 s and 12.3 s. Loading the SDK costs only 0.4 s. Groq was steady at about 1.3 s.
  - With a 12 s per-call limit and two Gemini models first, a slow day could use most of the 25 s budget before Groq is tried.
- **Next iteration:** propose a chain-tuning change to the developer (shorter Gemini limit; skip the same vendor after a timeout).

## 2026-10-22: Chain tuning for Gemini latency (option A)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** the developer chose option A of three: an 8 s per-call limit plus skipping the same vendor after a timeout. The others were Groq first, or racing both providers.
- **Outcome:**
  - The per-call limit is 8 s, down from 12 s.
  - After a timeout, the other models from the same vendor are skipped for that request only, and the attempt is recorded as `skipped_after_timeout`.
  - Rate limits still move on to the next model from the same vendor.
  - Worst case on a slow Gemini day: about 8 s plus Groq at about 1.3 s, where the old settings could reach the fallback after about 25 s.
  - 302 tests.

## 2026-10-22: AI layer, part 3 — schemas, prompts, guardrails, fallbacks (CC-AI-02)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompts (v1):**
  - `personalize`, `interpret-change` and `alternatives`, each structured as role, task, output rules and shared guardrails.
  - The guardrails cover: Indian events only; text inside `<<< >>>` is data; ids only from the request; never calculate; short specific "why"; JSON only.
  - Requests are sent as JSON, with few-shot examples as prior turns.
  - A test fails if the domain gains an operation kind that the interpret prompt does not explain.
- **Design change found while writing the prompt:**
  - "10 more guests" and "cut by ₹2,000" would have made the LLM do arithmetic.
  - Added `adjust_guest_count` and `adjust_total_budget`: the model copies the number, and code computes the result.
- **Guardrails:**
  - Lenient for `personalize`: unknown ids are cleared or dropped, lists trimmed, duplicates removed.
  - Strict for `interpret_change`: the answer is converted to the domain `ChangeSet`, unknown ids are rejected with reasons for one repair, and clarifications and off-topic answers never carry operations.
  - Alternatives never target protected categories.
  - Tests show that even a "compromised model" answer deleting a protected expense is skipped by the domain.
- **Bugs found only by the live smoke test (`scripts/ai_smoke.py`):**
  1. The adapter logged only "unavailable", which hid the cause. Error messages now carry the HTTP code and status, never keys.
  2. A single transient Gemini error benched it for 30 s, overloading Groq's 8k tokens/minute. Now there is a circuit breaker: a provider rests only after 2 consecutive failures, and a success resets the count.
  3. **The 8 s per-call limit broke Gemini entirely.** Gemini answered `400 INVALID_ARGUMENT: "Manually set deadline 8s is too short. Minimum allowed deadline is 10s."` The limit is now 10 s, the Gemini adapter enforces the minimum, and a regression test covers it.
- **Prompt sizes measured with count_tokens:** personalize about 1.7k tokens, interpret about 2.2k, alternatives about 1.0k. The interpret examples were slimmed.
- **Final live smoke run (dev keys), 7 calls, 0 fallbacks:**
  - Gemini answered personalization (2.4 s) and all five change requests (0.9–1.3 s, one at 9.1 s), with correct operations.
  - It asked a specific clarifying question for "make it nicer" and declined the prompt-injection attempt as off-topic.
  - For alternatives, Gemini timed out, its second model was skipped, and Groq answered (11.3 s total).
- 339 tests, 99.95 % branch coverage.
- **Next iteration:** persistence (Postgres cache and plans), services, web routes, then the UI.

## 2026-10-22: Docs sync
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (summary):** "Do the docs sync first, then the persistence plan."
- **Outcome:**
  - `docs/ARCHITECTURE.md` is v1.1, matched to the code. Every not-yet-built item is marked **(planned)**, so the doc never claims more than exists.
  - It now records the measured numbers (cold start, prompt sizes, the Gemini 10 s minimum), the real module map, and the decisions made since v1: protect field change, `adjust_*` operations, custom budget lines, follow-up tasks, the contingency rule, the circuit breaker and same-vendor skip.
  - `docs/SPEC.md` adds F16–F18. The must-pass table names the real test files with an honest status (domain proven, route planned).
  - `AGENTS.md` handoff commands now match what is actually run.
- **Found while checking the docs against reality:** pytest collected doctests only from `app/domain`, so four AI-layer doctests had never run. Added `app/ai` to `testpaths`; they pass (343 tests).

## 2026-10-22: Persistence, part 1 — tables and repositories
- **Tool / model:** Claude Code (Claude Opus 5.5). The plan was approved with all recommended decisions.
- **Outcome:**
  - Tables: `events`, `proposals` (the change history), `ai_cache` and `rate_limits`, using portable types.
  - `EventRepository`: owner stored as a SHA-256 hash; ownership and version checks inside the SQL itself.
  - `ProposalRepository`: a proposal can be decided only once.
  - `SqlCacheStore` and `TieredCache` (memory, then Postgres), and a fixed-window `RateLimiter`. Expired rows are purged occasionally during writes.
  - `scripts/init_db.py` created the tables on the Neon dev branch.
  - 16 repository tests on SQLite.
- **Live check on Neon (Postgres):** every repository path worked, including the dialect-specific insert-or-update and `RETURNING`.
- **Measured from India:** a new connection per operation costs about 1.06 s, against 0.48 s with a reused pool. Pooling was rejected because idle pooled connections can keep Neon awake beyond its 100 free compute-hours. Proposed instead: one connection and one transaction per request, which also makes approval atomic.

## 2026-10-22: Unit of work (option C)
- **Tool / model:** Claude Code (Claude Opus 5.5). The developer chose option C (one connection and one transaction per request) over the current per-call connections or a reused pool.
- **Outcome:**
  - `Database.transaction()` gives event and proposal repositories that share one connection and transaction. It commits on success and rolls back on any error.
  - The AI cache and rate limiter keep their own short connections, so no transaction stays open during an AI call.
  - `save` now uses `UPDATE … RETURNING`.
- **Tests:** a new test proves a failed approval changes nothing (360 tests).
- **Live on Neon:** an atomic approval (save version 2 and mark the proposal approved) took 1.43 s from India in one connection, compared with about 3.2 s for three separate connections. Rollback, stale and ownership checks were verified on Postgres.

## 2026-10-22: Domain additions for the services
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Outcome:**
  - `Personalization` value object and `personalize_plan()`: bounded emphasis with an exact re-split, AI tasks dated with their reasons, ideas, and vendor notes. The function uses domain types only, so the domain still never imports the AI layer.
  - `estimate_saving()`: remove frees 100 %; reduce or replace frees 10/25/50 %, rounded down.
  - `SavingAction` and `SavingLevel` now live in the domain, and the AI schema reuses them, as it does for emphasis.
  - `TaskSpec` carries an optional `why`.
  - 370 tests.
- **Self-correction:** one test compared category weights, but emphasis scales every weight by 100, so the weights differ while the rupee allocations are identical. The test now compares allocations, which is what matters.

## 2026-10-22: Services — the full loop (persistence part 3)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Outcome:**
  - `PlanningService.create_event`: draft, then AI personalization or the labelled fallback, then saved as version 1 with the AI record (answer and provenance). The AI call runs before the transaction opens.
  - `ChangeManager`: `request_change` returns a proposal, clarification, off-topic, unavailable, invalid or nothing-to-change outcome. `propose_from_form` is the no-AI path. `approve` saves the new version and marks the proposal in one transaction. `reject` and `history` are also there.
  - `PlanEditor` saves direct edits immediately. `AlternativesService`: the AI suggests and code prices each option; no AI call is made when within budget.
  - `build_services` wires the providers, the tiered cache and the database.
  - 392 tests, 99.88 % branch coverage. Scenarios 1, 6, 7, 12 and 13 are now covered through the services.
- **Live loop on real systems (Neon dev, dev AI keys):**
  - A Jaipur wedding was created with 5 ideas and 5 AI tasks (Gemini timed out and Groq answered).
  - "20 more guests are confirmed, but keep photography as it is" became `adjust_guest_count` plus `protect`: guest-scaled categories went up, the rest were cut proportionally, and photography was kept.
  - The approval saved version 2 with 170 guests, and the history shows it approved.
- **Measured:** event creation took 20.4 s end to end when Gemini timed out first, which is too slow for a good first impression. To address in the web plan.

## 2026-10-22: Web layer, part 1 — progressive personalization and request safety
- **Tool / model:** Claude Code (Claude Opus 5.5). The developer approved the web plan with all recommended decisions.
- **Outcome:**
  - **Progressive personalization:** `create_event` is split into `create_draft` (instant, code-built, version 1) and `personalize`.
    - `personalize` is idempotent and makes its AI call outside any transaction.
    - It re-splits the budget only while the plan is untouched (`is_untouched`); otherwise it adds ideas only, so it never overwrites the user's edits.
    - It retries once on a concurrent save. `chain.offline()` gives the labelled fallback when a client is rate-limited.
  - **`RequestGuardMiddleware`:** a 32 KB body limit and a CSRF check on every unsafe request, done centrally so no route can forget them. The token comes from a header (htmx) or a form field, is compared in constant time, and the body is replayed to the app.
  - **Sessions:** a signed 30-day cookie (HttpOnly, SameSite=Lax, Secure in production), a random session id and a CSRF token, plus one-time status messages.
  - **`AIRateLimit`:** HMAC-hashed client address, per-client and global daily limits, behind a `CounterStore` protocol. It never blocks on database errors.
  - **Errors:** friendly pages (404 not found, 409 stale, 422 invalid), or a fragment for htmx. The CSRF token is set on `<body>` via `hx-headers`, with no inline script.
  - 418 tests, 99.64 % branch coverage. New tests include a two-tab personalization race, "the user's edit wins over the AI", and the guard replaying the request body.
- **Note (lane exception, approved):** Claude Code wrote the first plain templates (`error.html`, `partials/notice.html`) and edited `base.html` (the CSRF header and the status messages). Antigravity owns the design pass.

## 2026-10-22/23: Web layer, part 2 — routes, view models and plain templates
- **Tool / model:** Claude Code (Claude Opus 5.5). Resumed after a usage-limit pause, from the state saved in memory.
- **Outcome:**
  - **Routes:** landing; create event (instant draft, then 303); dashboard; progressive personalization; direct edits (expenses, protection, tasks, vendors); the Change Manager (plain-language request, a no-AI form, approve, reject); priced alternatives. Every route works as a plain form and as htmx.
  - **`views.py`:** all formatting and labels in tested Python, including reason labels for every budget change and an honest one-line AI note ("Interpreted by AI (provider, ms)" or "Standard suggestions (not AI-generated)").
  - **htmx:** configured to swap 404/409/422 so errors stay visible with correct status codes; error notices go to the live region via `HX-Retarget`.
  - **Plain semantic templates** (approved lane exception): a field macro wires labels, hints and errors (`aria-describedby`, `aria-invalid`); every table has a caption and `scope`; buttons carry screen-reader names; the "How this was generated" panel shows the payload sent and the validated answer.
  - 449 tests. 31 web-flow tests run through HTTP with and without JavaScript, including scenarios 9 and 12 and an automated audit of every page and fragment: every control labelled, unique ids, named buttons, one `h1`.
- **Bug found by the tests:** an empty expense label showed Pydantic's internal wording, "Value should have at least 1 item after validation, not 0". Validation errors now map to plain messages by error type ("This field is required.", "Must be at least 1.", "Enter a whole number.").
- **Self-corrections:**
  - Four test expectations were wrong: HTML escaping of literal text versus variables, the exact stale-proposal wording, and a fake that raised `AssertionError` instead of a provider outage.
  - A file patch silently did not apply after ruff reformatted its target, so the file was rewritten whole.
  - Two route "noqa" shortcuts were replaced by a documented per-folder lint exemption for FastAPI handlers.

## 2026-10-22: Production go-live and web layer, part 3 — the UI contract
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (short):** the developer created the tables on the production database; push web part 2, verify the live site, then write the UI contract for Antigravity's design pass.
- **Outcome:**
  - **Live check on the production URL** (scripted HTTP, real AI): create 303 in 0.5 s, personalize labelled "Suggested by AI", plain-language change previewed with the protected line kept, approve produced version 3 and a history entry; a POST without a CSRF token got 403; a second browser session got 404 for the same event; the session cookie is `Secure; HttpOnly; SameSite=Lax`.
  - **Provider latency:** Gemini 3.5 Flash-Lite was degraded that afternoon (10–17 s and 504s even for a tiny prompt; 3.1 Flash-Lite answered in 5–7 s). The fallback chain kept every live answer AI-generated; one burst of seven smoke-test calls hit Groq's per-minute limit and got a labelled fallback. The chain order was left unchanged, pending more data.
  - **`docs/UI_CONTRACT.md`:** endpoints and what each returns with and without htmx, fixed ids and swap targets, the data each template receives, accessibility and CSP rules, honesty labels, CSS hooks, and the tests that guard them.
  - **`tests/unit/test_stylesheet.py`:** guards the CSS rules that behaviour and accessibility depend on, so a redesign cannot silently drop them.
- **Bugs found:**
  - Writing the contract showed that the busy message was always visible: htmx's built-in indicator styles are off because the CSP forbids inline styles, and nothing else hid it. A three-rule CSS block now hides it until a request is in flight (small lane exception in `public/static/`, recorded here).
  - Alternatives said "Interpreted by AI"; they now say "Suggested by AI", with a test.
- **Self-corrections:** the first contract draft stated the wrong date format and said 403 and 413 render `error.html`; both were checked against the code and corrected before commit.

## 2026-10-22: Design pass — Antigravity's redesign, finished and verified by Claude Code
- **Tool / model:** Antigravity (Claude Opus 4.6, Planning mode) for the redesign; Claude Code (Claude Opus 5.5) to finish and verify it.
- **Prompt (short):** design pass per `docs/UI_CONTRACT.md` — warm festive Indian look, readable, mobile-first; style every hook; fix the 4.41:1 pair; meta description mentions personalized ideas; compute contrast with a script; run the tests; hand off.
- **What happened:** Antigravity planned and wrote about 770 lines of CSS plus `data-label` attributes for phone tables, then hit its account quota (resets 28 Oct) before reviewing or committing. The developer approved finishing it in Claude Code as a recorded lane exception.
- **Verification by Claude Code:**
  - Recomputed contrast independently. Antigravity's script reported a full pass, but it judged the orange accent against 3:1, while links and bold phone labels use it as normal-size text (3.99–4.42:1 against 4.5:1). Light `--color-primary` moved from `#c05621` to `#b04f1e` (same hue; 4.61:1 or better on every light background, white button text 5.27:1). This is the third wrong self-check from that lane, so contrast is now a test (`CONTRAST_PAIRS`, built from how `app.css` uses each colour), which fails on the old colour.
  - Drove every state in real Chrome (Playwright on the installed Chrome; the Chromium download failed because the disk was full) at 320/375 px and 1280 px, light and dark, with full-page and per-section screenshots. Checks: no horizontal overflow anywhere; table semantics survive the phone card layout; console clean apart from the expected 422 and 404.
- **Bugs found in the browser and fixed:**
  - Content Security Policy errors on every plan edit: htmx's settle step copies `style` attributes, and the strict CSP blocks that. Fixed by removing `style` from `attributesToSettle` in the htmx config, with a test; the error was traced with a `securitypolicyviolation` listener (`htmx.min.js:1:10457`).
  - Controls outside `.field` (change request, vendor status, expense amount) were unstyled: white boxes in dark mode. All text controls now share one style.
  - On phones, table captions collapsed into a narrow column and in-cell forms were cramped; alternatives kept indented bullets.
  - Main actions looked identical to secondary ones; they now use `button-primary`.
  - The create form listed every event type's functions; CSS `:has()` now shows only the chosen type's group, with no JavaScript.
- **Self-corrections:** stopping the local server with `pkill -f` matched the shell's own command line and ended it; checks were rerun separately.

## 2026-10-22: CI and real-browser tests
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (short):** plan approved — GitHub Actions on every push (pre-commit hooks, tests with the coverage gate, Playwright + axe browser tests, full-history secret scan, dependency audit, size budget); browser tests run the app with a task-aware fake AI and a temporary SQLite file.
- **Outcome:**
  - `.github/workflows/ci.yml`: three parallel jobs, read-only token, actions pinned to commit SHAs, per-job timeouts, a weekly scheduled run for newly published vulnerabilities. gitleaks runs as the release binary with a checksum instead of a third-party action, and scans the whole history (26 commits, no leaks locally). pip-audit found no known vulnerabilities.
  - `tests/e2e/`: four journeys (including the whole flow with JavaScript turned off) and accessibility checks — 24 axe scans, 320 px overflow, skip link, console. 36 tests, about 75 s. `TaskAwareProvider` in `tests/fakes.py` answers by task, so the live server can take requests in any order.
  - axe is read from the `axe-playwright-python` package (axe 4.12.1); its untyped wrapper is not used. Vendoring axe was ruled out because the file is 652 KB, over the 500 KB limit.
- **Bug found by axe:** the JSON panels in "How this was generated" scrolled but could not get keyboard focus (`scrollable-region-focusable`). They are now focusable named regions.
- **Self-corrections:** the first version of the tests passed with two of today's bugs put back. axe only scanned desktop width, where the faint orange never appears as normal text, and skipped collapsed panels; it now also scans at 375 px with every `<details>` opened, and was shown to fail on the old colour. The console test was shown to catch an injected inline style. The htmx settle bug itself could not be reproduced without screenshot tooling, so the htmx-config integration test remains its guard. The rate-limit setting in the e2e server first exceeded the settings' maximum (1,000).

## 2026-10-22: AI evals — harness and the first baseline (Groq)
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Prompt (short):** plan approved — about 50 scored cases across the three AI tasks, run against the real models; scoring by code through the app's own guardrails and change engine (no LLM judge); results and a report committed.
- **Outcome:**
  - `evals/`: 54 cases (36 change requests, 12 personalizations, 6 alternatives). The harness commit `d3c2c80` says 56; that was an arithmetic slip, corrected here.
  - Change answers are judged by the plan they produce, so equivalent operations pass and unrequested changes fail. "Keep X as it is" must leave X protected and unchanged, because that is what the prompt asks for. Probing the domain showed why: cutting guests never touches the cake line, so "unchanged" alone could not tell whether the model listened.
  - **Groq `openai/gpt-oss-120b`: 53/54.** Change requests 36/36 (all first try, median 1.2 s), personalization 12/12, alternatives 5/6. The miss (`b-far-over`, a gap of half the budget) is real: sensible options but mostly "small" cuts, covering 17% while claiming to close the gap. It is a candidate for alternatives prompt v2 once every model has a baseline.
  - Gemini 3.5 and 3.1 Flash-Lite could not be measured: Google returned 504 timeouts and 503 "This model is currently experiencing high demand" all evening (a trivial prompt took 19 s on 3.5). Their baselines run when Google recovers.
- **Bug found in my scorer:** the model wrote "alcohol‐free" with a Unicode hyphen (U+2010), and the negation check only knew the ASCII hyphen, so a correct answer was failed. The check now accepts any dash, with a doctest for that character. A new `--rescore` option re-scores saved answers with no new API calls; the Groq run went from 52 to 53 that way.
- **Self-corrections:** two doctest examples and one coverage expectation in my tests were wrong (the scorer was right); the typed-id dictionaries needed `dict[str, ...]` annotations under `mypy --strict`, which the pre-commit hook (and so CI) now also runs on `tests/` and `evals/`.

## 2026-10-22 (night): Gemini baselines and what they showed
- **Tool / model:** Claude Code (Claude Opus 5.5).
- **Outcome:**
  - Google recovered around 23:20 IST (3.5 answered in 1.9–2.8 s, 3.1 in 0.9 s), and both Gemini runs started at once.
  - **Gemini 3.5 Flash-Lite:** 40/54. When it answered: change requests 28/31, personalization 8/8, alternatives 4/6. Nine cases (17%) got no answer because of Google timeouts and 500/503 errors. Median latency for a change was 4.1 s, against 1.2 s for Groq.
  - **Gemini 3.1 Flash-Lite:** the run was discarded. Google went back to 503 "high demand" right after the health check, and 51 of 54 calls got no answer, so the run measured the outage, not the model. It will be rerun.
  - **Root cause of the three Gemini 3.5 rejections:** it left out `amount_inr` on add_category, add_expense and update_expense, even after the repair message named the missing field. The prompt lists the field, but no few-shot example shows one of those operations, and Gemini follows the examples' shape more literally than Groq. This makes it a prompt v2 candidate (one worked add_expense example).
- **Runner fix:** an answer the guardrails rejected twice was being counted as "no answer", like an outage. It is the model's failure, so it now counts as a failed answer, and `--rescore` reclassifies old files.

## 2026-10-22 (night): Prompt v2, before and after
- **Tool / model:** Claude Code (Claude Opus 5.5). The developer approved prompt v2 ("do A now") and the Groq-first chain ("do B"), then let the work continue overnight without further approval.
- **Changes:**
  - `interpret-change-v2`: one worked example with update_expense, add_expense and add_category, each carrying `amount_inr`. A test now fails if any amount-bearing kind lacks an example; it was shown to fail on v1.
  - `alternatives-v2`: states what small, medium, large and remove free up, built from the domain's own `SAVING_PERCENT` so prompt and code cannot drift, and asks for options that reach the shortfall or say plainly that they cannot.
- **Results, passed out of answered** (`evals/RESULTS.md`, run history):
  - Groq: change requests 36/36 again, personalization 5/5. Its free daily quota (200K tokens) ran out at case 41, because the morning run, the v2 run and live traffic share it; alternatives v2 is re-run with `--retry-errors` after the reset.
  - Gemini 3.5: change requests 24/26 (v1: 28/31); the expense case is fixed, but it still drops `amount_inr` for "add a line for a ghodi for the baraat, ₹25,000", even after the repair message. Known limitation; Groq handles it. Alternatives 4/5 (v1: 4/6).
  - Gemini 3.1: discarded again; 47 of 54 calls hit Google's 503 "high demand".
- **Scorer bugs found by reading the answers, not the totals:** "strictly omit onion, garlic…" was a negation the checker did not know; "zero plastic decor" was a preference mention the checker threw away because "zero" counts as a negation. Preferences now count any mention; forbidden items keep negation handling. Both have regression tests built from the real answers.
- **Report fix:** the summary divided by all cases, which made Groq's quota cut-off look like 0/6 alternatives. It now shows passed out of answered, with no-answer cases in their own column.
- **Tooling:** `--retry-errors FILE` re-runs only the unanswered cases of a run, if its prompt versions still match.
- **Operational lesson:** a Groq eval running at the same time as live traffic used up the shared per-minute quota, and live change previews fell back from Groq (1.1 s) to Gemini (about 6 s). Groq evals are not to be run while evaluators may be using the app.

## 2026-10-22 (night): Docs sync and README
- **Tool / model:** Claude Code (Claude Opus 5.5), continuing overnight as the developer asked.
- **Outcome:**
  - `README.md`: problem, what the app does, the 4 × 3 coverage matrix, how it works (with a diagram), where GenAI is used, eval results and what they changed, security, accessibility, testing and CI, performance, running locally, structure, assumptions and limitations, and how it was built. Every claim was checked against code or a measurement before it went in.
  - `docs/ARCHITECTURE.md` v1.2: about 20 "(planned)" items that are now built and tested were updated. The ones still unbuilt stay marked: form idempotency tokens (duplicates are caught by duplicate detection instead), moving focus after a swap, regenerating ideas, applying an alternative, the uptime monitor and the persona.
  - `docs/SPEC.md`: the must-pass table names the unit, web, browser and eval tests for each scenario; all 14 are covered.
  - The spec promised a privacy notice that did not exist. The preferences hint now tells users an AI model reads the text, so they should leave out personal details.
- **Corrections while checking the README:** community vendors listed "idol" (not in the catalog); wedding mehendi and choreographer vendors appear only with those functions; "change previews take 1–3 s" became the measured numbers (1.1 s via Groq, about 6 s when Gemini answers).

## 2026-10-23 (night): Fresh-eyes review and the first fixes
- **Tool / model:** Claude Code (Claude Opus 5.5). A general-purpose subagent reviewed a fresh clone with no prior context, against AGENTS.md's review procedure: read-only, three AI calls on the live site at most.
- **The review found 14 issues.** The serious ones:
  - The shortfall ignored lines still needing their money.
  - No way to set a budget line's amount.
  - A fallback personalization was permanent.
  - Alternatives were offered for infeasible proposals, where trimming cannot help.
  - The transparency panel appeared only for personalization.
  - Duplicate ids when the change form is shown twice.
  - Silent htmx updates and lost focus.
  - Up to seven database transactions per request.
  - No limit on non-AI writes.
  - An approve/reject race.
  - Some user text was unquoted in prompts.
  - History dates in UTC.
  - Docs drift.
  Each finding is verified here before it is fixed.
- **Fixed in this commit:**
  - **Shortfall:** each line needs the larger of its plan and what is committed to it, and the gap is that sum minus the total. Before, ₹7,000 spent on a ₹5,000 line of a ₹15,000 plan showed no alert. The alert now names the lines over their plan. Alternatives become coherent too, because trimming another line's plan really reduces what the plan needs. Seven tests encoded the old rule and were updated with the reason. The eval alternatives cases now build the same gaps as before through `over_by()`, so earlier results stay comparable.
  - **Approve/reject race:** approval reads the proposal with `FOR UPDATE` and rolls back if its decision does not land. Both new tests were shown to fail on the old code.
- **Fixed next (review findings 3–7, part of 14):**
  - **Retry after standard ideas:** a fallback personalization is no longer final. The fallback adds only ideas, so a retry drops them and applies the AI answer. Nothing is saved unless a model answers. The rate limit is asked only when a model would be called, so a busy repeat on an AI plan keeps its AI label. The retry button deliberately has no auto-trigger, so a down AI cannot loop.
  - **Infeasible proposals:** they no longer offer AI alternatives, which could not help. The preview states the extra total needed and the protected lines that could be released, computed by code. `get_proposal` and the proposal-level `shortfall_inr` became dead code and were removed.
  - **Transparency panel:** it now appears on change results and alternatives, with per-panel id prefixes so several can share a page.
  - **Duplicate ids:** the change form shown twice now uses `cf-` and `cfr-` prefixes. A full-page audit test was shown to fail on the old templates with exactly the four ids the reviewer found.
  - **Accessibility:** previews are announced through the status region and take focus (htmx focuses `[autofocus]`, confirmed in its source). Plan buttons have stable ids, so htmx restores focus after a swap (also confirmed in its source). Two new browser tests cover keyboard Mark done and the preview focus.
  - **A test locator the new panel broke:** the clarifying question now also appears in the panel's JSON, so the locator was made exact.
- **Fixed next (review finding 2): set one budget line's amount.** This is the `set_category_budget` operation, the 14th kind. The line is pinned at its new amount and the other unprotected lines are rebalanced, so the total stays exact. Through the rebalance a line protected before the request is skipped and reported. One subtlety: the engine applies protection first, so "set decor to ₹6,000 and keep it" would have silently left decor unchanged, because a protected line overrides any pin. Protection of a line that the same request also sets is now applied after the rebalance.
  - `interpret-change-v3` documents the operation with one worked example. The "every amount-bearing kind has an example" test now derives its list from the engine's field rules, so a future kind cannot be missed.
  - The no-AI change form gained "Set one budget line".
  - Two new eval cases: English, and Hinglish "photography ka budget 1 lakh kar do".
  - `--retry-errors` now checks only the prompts of the tasks it retries, so the scheduled Groq retry is not blocked by this unrelated prompt bump.
  - The README no longer claims users can change every date; task and vendor dates follow the event date.
