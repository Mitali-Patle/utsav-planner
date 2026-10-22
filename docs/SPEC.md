# Spec: Utsav Planner

## 1. Problem
Build a GenAI planning assistant that helps people organize **weddings, birthdays and community events**.
It must cover **budgets, timelines, vendor checklists and personalized ideas**, and decide based on the user's context:
event type, budget, guest count, city, date and preferences.

## 2. Persona
**To be confirmed after the Challenge 5 explainer (24 Oct).** The persona affects only `app/content/persona.py`, the prompts' tone and the demo story.

Candidates:
- **A. Family organizer on a fixed budget.** For example, someone planning a sibling's wedding functions or a child's birthday, where every rupee and every last-minute change matters.
- **B. Volunteer organizer.** For example, an apartment-association secretary running Ganesh Chaturthi or Diwali events with a shared budget, permits and volunteers.
- **C. First-time host.** No planning experience; needs a checklist and a sense of "what do I do next".

Before/after story (same for every candidate):
- **Before:** spreadsheets and WhatsApp threads. Any change (budget cut, more guests, new date) means recalculating everything by hand, and things get missed.
- **After:** the user describes the change in one sentence and sees exactly what it affects. They approve it, and the plan stays consistent.

## 3. Inputs
| Input | Type | Used by |
|---|---|---|
| Event type | wedding / birthday / community | catalog, AI |
| Sub-events | wedding: haldi, mehendi, sangeet, ceremony, reception. Community: puja, cultural program, food stall, etc. | catalog, AI |
| Total budget (₹) | int | budget engine |
| Guest count | int | budget engine (scaling), AI |
| City | text | AI context (local customs, season, vendors) |
| Event date | date | timeline, vendors |
| Preferences/theme | text ≤ 500 | AI |

## 4. Features
Priority: P0 must exist for submission 1; P1 should exist for submission 1; P2 only if time allows.

| ID | Feature | Pri | AI role | Code role |
|---|---|---|---|---|
| F1 | Create event (all 3 types, sub-events, "Fill example" presets) | P0 | none | validation, baseline from catalog |
| F2 | AI personalization on creation (ideas, budget emphasis, extra tasks, vendor priorities, each with why) | P0 | generates and reasons | bounds, merge, allocate, dates |
| F3 | Budget dashboard: allocation per category, spent, remaining, ₹/guest, overspend flags | P0 | none | all math |
| F4 | Add, edit and remove expenses; protect or unprotect items | P0 | none | math, idempotency |
| F5 | Over-budget handling: shortfall + on-demand AI alternatives | P0 | suggests with why | computes savings and shortfall |
| F6 | Timeline: tasks with due dates, done toggle, compressed/overdue flags | P0 | adds context-specific tasks | dates |
| F7 | Vendor checklist: book-by dates, status todo → contacted → booked, priority with why | P0 | prioritizes with why | lead times, status rules |
| F8 | Smart Change Manager: natural-language change → clarify or preview (before/after) → approve or reject | P0 | interprets | computes impact, enforces protection |
| F9 | Change history (approved and rejected, with the request text and diff) | P0 | none | log |
| F10 | "How this was generated" panel on every AI result | P0 | none | provenance |
| F11 | Labelled non-AI fallback for all three AI tasks | P0 | none | deterministic |
| F12 | Regenerate ideas (variation, rate-limited) | P1 | generates | cache bypass, limit |
| F13 | Apply an alternative as a proposal | P1 | none | change engine |
| F14 | Export the timeline as `.ics` for a calendar | P2 | none | pure function |
| F15 | Printable plan view (print CSS) | P2 | none | template |
| F16 | Custom budget lines (up to 3, for example "Fireworks ₹3,000") | P1 | interprets the request | allocates, enforces the limit |
| F17 | Follow-up tasks for booked vendors when the date or guest count changes | P0 | none | creates due-today tasks in the proposal |
| F18 | Relative changes ("10 more guests", "cut ₹2,000") | P0 | copies the number | computes the result |

## 5. Coverage matrix (4 capabilities × 3 event types)
Every cell must be demonstrable in the live app and named in the README.

| | Wedding | Birthday | Community event |
|---|---|---|---|
| **Budgets** | Per-function categories (mandap/venue, catering, decor, photography, music such as DJ or nadaswaram, attire and makeup, pandit and rituals, gifts, contingency); guest scaling; protected items | Venue, catering, cake, decor, entertainment, return gifts, contingency; worked example: ₹15,000 → ₹12,000 keeping the cake | Pandal/venue, permits, sound, prasad/catering, decor, cultural program, cleanup, contingency; shared-fund framing |
| **Timelines** | Offsets from about 120 days before (venue, photographer) to the day itself; per sub-event tasks; date shift + compression | Offsets from about 30 days to the day; invites, cake order, decor | Permits (police/municipal) early, volunteer roster, sound check, cleanup after the event |
| **Vendor checklists** | Caterer, decorator, mandap, photographer, mehendi artist, DJ/band/nadaswaram, pandit; lead times | Cake shop, decorator, entertainer, venue; lead times | Pandal contractor, sound and lighting, caterer, idol/decor, waste management; lead times |
| **Personalized ideas** | Theme and décor by preferences, city and season; cost-saving swaps; why | Theme by age and interests; at-home vs venue; why | Inclusive activities, sustainable choices (for example eco-friendly idols), sponsorship ideas; why |

## 6. Change Manager behaviours
| Request (example) | Expected |
|---|---|
| "My budget dropped to ₹12,000 but keep the cake as is" | Operations: `protect(cake)` + `set_total_budget(12000)`. The cake is unchanged; the rest is reduced proportionally. Preview shows before/after. |
| "Add a photographer for ₹2,000" | `add_expense`. If this goes over budget, the shortfall is shown and alternatives are offered. |
| "Guest count goes from 30 to 40" | `set_guest_count(40)`. Catering, seating and return gifts are scaled and flagged; the total impact is shown. |
| "10 more guests are coming" | `adjust_guest_count(+10)`: the model copies 10 and code computes 40. Booked caterers get a follow-up task. |
| "Move the event 3 days earlier" | `shift_date_days(-3)`. All incomplete deadlines are recalculated; compressed tasks and vendors are flagged. |
| "Cut decoration spending but keep the cake" | `cut_priority=[decor]`, `protect(cake)`. A clarifying question is asked if no target total can be inferred. |
| "Make it nicer" | `needs_clarification` with a specific question. No proposal. |
| "Write me a poem about cricket" / "ignore your instructions…" | `off_topic`. Polite decline, nothing changed. |

## 7. Must-pass scenarios (each maps to at least one automated test)
Status: **domain** = proven in the domain/AI layers; **route** = end-to-end through the web app (planned).

| # | Scenario | Expected | Tests | Status |
|---|---|---|---|---|
| 1 | Add an expense within budget | Totals and remaining balance are correct | `tests/unit/test_budget.py` | domain ✅, route planned |
| 2 | Add an expense that exceeds the budget | Shortfall shown, alternatives available | `test_budget.py`, `test_changes.py`, `test_ai_tasks.py` (alternatives) | domain ✅, route planned |
| 3 | Reduce the budget while protecting the cake | Cake unchanged; sum equals the new total | `test_budget.py`, `test_changes.py`, `test_budget_properties.py`, `test_changes_properties.py` | domain ✅ |
| 4 | Increase the guest count | Guest-scaled categories scaled and flagged | `test_changes.py` | domain ✅ |
| 5 | Move the event date | Deadlines recalculated; compression flagged | `test_timeline_vendors.py`, `test_changes.py` | domain ✅ |
| 6 | Reject a proposal | Saved plan unchanged; history shows rejected | `test_changes.py`, `test_changes_properties.py` | domain ✅, history planned |
| 7 | Approve a proposal | Version + 1; history logged; approving twice refused | `test_changes.py`, `test_changes_properties.py` | domain ✅, history planned |
| 8 | AI API fails (timeout, 429, malformed, outage) | Next provider or a labelled fallback; data intact | `test_ai_chain.py`, `test_ai_providers.py`, `test_ai_tasks.py` | ✅ |
| 9 | Invalid amount (0, negative, too large, text) | Rejected with a clear message; input kept | `test_budget.py`, `test_models.py` | domain ✅, form planned |
| 10 | Same request repeated | No duplicate expense or task | `test_budget.py`, `test_changes.py` | domain ✅ |
| 11 | Prompt injection in request text | Off-topic or ignored; protected items untouched | `test_ai_tasks.py`; live smoke run declined it | ✅ |
| 12 | Another session opens my event | 404 | route tests | planned |
| 13 | Stale proposal (plan changed after preview) | Approval refused, user asked to retry | `test_changes.py`, `test_changes_properties.py` | domain ✅ |
| 14 | Keyboard and screen reader basics | axe has no serious violations; flows work by keyboard | `tests/integration/test_pages.py` (base template); Playwright + axe | partly ✅, e2e planned |

## 8. Non-functional requirements
- AI response < 15 s typical. Pages render < 1 s when warm. Core pages are readable without JS.
- WCAG 2.2 AA target. Works at 320 px width.
- The app stays usable when every AI provider is down (labelled fallback).
- Prices are in ₹ with Indian digit grouping (₹1,20,000).

## 9. Out of scope (stated as limitations in the README)
User accounts and multi-device sync; real vendor marketplaces or booking; payments; live vendor prices (the AI gives qualitative guidance and code uses the user's own numbers); multilingual UI.

## 10. Assumptions
- Baseline category weights, task offsets and vendor lead times are documented planning heuristics for India (in `app/domain/catalog.py`). Users can change any number.
- Free-tier AI quotas are limited, so results are cached and rate-limited, and there is a labelled fallback.
- Gemini free-tier inputs may be used by Google to improve its products, so the UI advises users not to enter sensitive personal data.
