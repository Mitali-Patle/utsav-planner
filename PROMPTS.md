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
