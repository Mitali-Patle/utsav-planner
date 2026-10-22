# AGENTS.md: Utsav Planner

The only rules file for every coding agent in this repo (Antigravity and Claude Code).
Do not create `GEMINI.md`, `.agents/rules/` or `.agent/rules/`. Keep this file under 10,000 characters.

## Project
Utsav is a GenAI planning assistant for Indian **weddings, birthdays and community events**.
It covers **budgets, timelines, vendor checklists and personalized ideas**.
Core principle: **AI interprets, reasons and generates; code calculates and enforces; the user approves.**

- `docs/ARCHITECTURE.md` is binding. If a change seems necessary, propose it; never silently deviate.
- `docs/SPEC.md` holds the features, acceptance criteria and the 4 capabilities × 3 event types matrix.
- Read the sections relevant to your task before planning.

## Hard constraints
- Work on `main` only. Never create branches or worktrees, and never force-push.
- The repo must stay under 10 MB including history. No file over 500 KB. No screenshots, videos, recordings or dumps in the repo.
- Never commit secrets. Keys come from server environment variables only. Only `.env.example` is committed.
- The browser never calls an LLM. Every AI call goes through `app/ai/`.
- The LLM never does arithmetic and never writes to storage. AI output is validated against `app/ai/schemas.py`, then code computes.
- No plan change is saved without user approval (proposal → approve/reject).
- Non-AI fallback output is labelled "not AI-generated". Never put an AI label on non-AI output.
- Never claim tests passed or a deploy works unless you ran or verified it. Docs describe only what is built and tested.
- The persona lives only in `app/content/persona.py`. Core logic must not depend on it.

## Stack
Python 3.12, FastAPI, Pydantic v2, pydantic-settings, Jinja2 + htmx (vendored) + hand-written CSS,
SQLAlchemy 2 (sync) on Neon Postgres (SQLite in tests), google-genai (Gemini), httpx (Groq),
pytest + hypothesis + Playwright/axe, ruff, mypy --strict, uv, pre-commit. Hosted on Vercel.

## Layout and ownership
Edit only folders your tool owns. Put cross-lane requests in your handoff note.

| Path | Purpose | Owner |
|---|---|---|
| `app/domain/` | Pure logic: models, budget, timeline, vendors, changes, catalog | Claude Code |
| `app/ai/` | LLM client protocol, providers, fallback chain, cache, prompts, schemas, guardrails | Claude Code |
| `app/services/` | Use-case orchestration | Claude Code |
| `app/persistence/` | DB engine, tables, repositories | Claude Code |
| `app/web/` except `templates/` | Routes, dependencies, security middleware | Claude Code |
| `app/web/templates/`, `public/static/` | HTML templates, CSS, minimal JS | Antigravity |
| `app/content/` | Persona and UX copy | Antigravity drafts, developer approves |
| `tests/`, `evals/`, `scripts/` | Tests, AI evals, tooling | Claude Code |
| `docs/`, `README.md`, `PROMPTS.md`, `CHANGELOG.md` | Documentation | Developer (agents propose) |

## Coding standards
- Full type hints; `mypy --strict` passes. Google-style docstrings on every public module, class and function.
- `app/domain/` is pure: no I/O, no network, no global clock (inject `Clock`), deterministic.
- Money is integer rupees (`int`), never float. Allocations use largest-remainder rounding so parts sum exactly to the total.
- Dates are `datetime.date` in Asia/Kolkata, obtained via `Clock`.
- Keep functions small with early returns. No dead or commented-out code, no `print` (use `logging`). Never log user-entered text.
- Domain errors are exceptions in `app/domain/errors.py`; the web layer maps them to friendly messages.
- Validate every input with Pydantic, using the limits in ARCHITECTURE §9.
- Templates: autoescape on; never `|safe` on user or AI text. Every input has a visible label. Use semantic HTML and landmarks. Announce htmx updates through an `aria-live` region. Never remove focus outlines.
- Adding a dependency requires a stated reason in the handoff note.
- Tests: every feature gets edge-case tests and every bug fix gets a regression test. The AI is always faked in tests (`tests/fakes.py`); tests never hit a real API.

## Commits
- Conventional Commits subject, for example `feat(budget): proportional reduction with protected items`.
- Body = the handoff note (see procedures).
- Trailer: `Tool: antigravity (<model>)` or `Tool: claude-code (<model>)`.
- Stage explicit paths only. Never `git add -A` or `git add .`.

## Procedures
These are also available as `/plan`, `/handoff`, `/review` and `/ship-check` in both tools.

### plan
1. Run `git status`. If the tree is not clean, stop and ask the developer.
2. Read the last handoff note with `git log -1`.
3. Read the relevant sections of ARCHITECTURE and SPEC.
4. State the plan: goal, files to touch (owned folders only), tests to add, risks, and anything that needs another lane.
5. Do not edit files until the developer approves.

### handoff
1. Run `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy app tests` and `uv run pytest -W error --cov=app`. For template, CSS or web changes also run `uv run pytest -W error -m e2e` (browser and axe tests). Report the real results.
2. Append the session's key prompts to `PROMPTS.md`: date, tool, model, prompt (short), outcome, what changed in the next iteration.
3. If the change is user-visible, add it to `CHANGELOG.md` under "Unreleased".
4. Stage explicit paths and commit. The body has four parts:
   - **Changed**
   - **Tested**: exact commands and results
   - **Left / limitations**
   - **Requests for other lane**
5. Summarize the files changed and any remaining limitations for the developer.

### review
Start with fresh context and make no edits. Review against:
- Code quality
- Alignment with the SPEC matrix
- Security
- Efficiency
- Testing
- Accessibility

Each finding has a severity (critical, high, medium or minor), file:line, reproduction steps, suggested fix and area. Report to the developer.

### ship-check (before every submission)
- CI is green on `main`. Lint, types and tests pass locally.
- A review was run on a **fresh `git clone`** of the remote plus the live URL.
- Repo size including history ≤ 8 MB. gitleaks finds nothing in the history.
- `git ls-remote --heads origin` shows only `main`.
- The repo is public, checked in an incognito window.
- Live app, checked in incognito:
  - Create one event of each type.
  - The AI panel shows a real model.
  - The Change Manager handles clarify, approve and reject.
  - An over-budget expense shows a shortfall and alternatives.
  - An invalid amount is rejected.
  - `/healthz` returns 200.
- Remaining AI quota is checked in AI Studio and the Groq console.
- The README links the live app, video and blog. `CHANGELOG.md` has this version's entry.
- The submission-form texts (changes; GenAI services and where) are drafted, ≤ 1,024 characters each, and match the code.
