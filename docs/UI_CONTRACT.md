# UI contract: templates, data and behaviour

How the server and the interface fit together, so the design can change without breaking
features, accessibility or security. Routes and view models (`app/web/`) belong to
Claude Code; templates and CSS (`app/web/templates/`, `public/static/`) belong to Antigravity.

**Change freely:** layout, element order within a section, wrappers, classes you add,
colours, type, spacing, icons, copy that is not listed under [Fixed text](#fixed-text).

**Keep as they are:** everything listed in this file. If a design needs something
different (a new variable, a different swap target), ask for it in the handoff note.

After any template or CSS change, run `uv run pytest -W error`. The tests in
[Guards](#guards) check this contract.

## 1. Pages and endpoints

| Method and path | Renders | htmx response |
|---|---|---|
| `GET /` | `landing.html` | |
| `POST /events` | 303 to the dashboard; `landing.html` with 422 on invalid input | |
| `GET /events/{id}` | `dashboard.html` | |
| `POST /events/{id}/personalize` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/expenses` | 303 to the dashboard; 422 with the form and its errors | `partials/plan_area.html` |
| `POST /events/{id}/expenses/{eid}/update`, `/delete`, `/protect`, `/unprotect` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/categories/{cid}/protect`, `/unprotect` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/tasks/{tid}/done`, `/undone` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/vendors/{vid}/status` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/changes` (field `request`) | `dashboard.html` with the answer in place | `partials/change_result.html` |
| `POST /events/{id}/changes/form` | `dashboard.html` with the answer in place | `partials/change_result.html` |
| `POST /events/{id}/proposals/{pid}/approve` | 303 to the dashboard; 409 or 422 with the reason | `HX-Redirect` to the dashboard; 409 or 422 with `change_result.html` |
| `POST /events/{id}/proposals/{pid}/reject` | 303 to the dashboard | `HX-Redirect` to the dashboard |
| `POST /events/{id}/alternatives` (optional `proposal_id`) | `dashboard.html` with alternatives | `partials/alternatives.html` |

Not-found and domain errors (404, 422) render `error.html`; for htmx requests the server
sends `partials/notice.html` with `HX-Retarget: #status-region`. A missing CSRF token (403)
or a body over 32 KB (413) is stopped by the request guard before routing, with a bare
`<p role="alert">` message.

## 2. Progressive enhancement

Every action must work **without JavaScript**.

- Each form keeps `method="post"` and its `action`, and contains `{{ csrf(csrf_token) }}`.
- htmx attributes (`hx-post`, `hx-target`, `hx-swap`) are added on top of a working form,
  never in place of one. No links or buttons that work only with JavaScript.
- `<body hx-headers=...>` sends the CSRF token on htmx requests. Keep it on `<body>`.
- The `htmx-config` meta tag in `base.html` makes 404, 409 and 422 swap like 200. Keep it.

## 3. Fixed ids and swap targets

| id | Where | Why it is fixed |
|---|---|---|
| `main` | `<main>` | Skip link target |
| `status-region` | `base.html`, outside `<main>` | `aria-live` region; flashes, out-of-band notices and htmx errors land here |
| `plan-area` | Root of `plan_area.html` | Target of every plan edit (`outerHTML`) |
| `personalization` | `plan_area.html` | Fires `hx-trigger="load"` once to personalize |
| `change-result` | `dashboard.html` | Target of change requests and approvals (`innerHTML`) |
| `change-result-heading` | `change_result.html` | Focusable heading (`tabindex="-1"`) of the answer |
| `change-busy` | `dashboard.html` | `hx-indicator` of the change request form |
| `plan-alternatives` | `plan_area.html` | Target for alternatives when the plan is over budget |
| `alternatives-{proposal id}` | `proposal.html` | Target for alternatives to a proposal |

Form field names are fixed: they are the server's API. Field ids follow
`{prefix}-{name}` from the `field` macro (`f-` on the landing page, `x-` for expenses).

## 4. Template data

All templates receive `csrf_token` and `flashes` (list of `{message, kind}`).
Money values are already formatted strings such as `₹1,25,000`; dates are formatted
strings such as `2 Dec 2026`. Templates never compute; ask for a new field instead.

### `landing.html`
- `form`: `form.values[name]`, `form.errors[name]` (and `form.errors["form"]`)
- `today` (ISO date for the date input's `min`)
- `event_types`: list of `(value, label)`
- `sub_events`: list of `{event_type, label, options: [(value, label)]}`
- `events`: list of `{id, title, type_label, date}`

### `dashboard.html` and `partials/plan_area.html`
- `event`: `{id, version, title, type_label, city, date, days_left, guests, preferences}`
- `plan.summary`: `{total, spent, remaining, per_guest, guests, shortfall}`; `shortfall` is `None` unless over budget
- `plan.categories`: `{id, name, allocated, spent, remaining, overspent, protected}`
- `plan.expenses`: `{id, label, category, amount, amount_inr, protected}`
- `plan.tasks`: `{id, title, due, done, overdue, compressed, source, ai, why, category}`
- `plan.vendors`: `{id, name, priority, book_by, urgent, status, status_label, next: [(value, label)], why}`
- `ideas`: `{title, description, why, impact}`; `impact` is `saves`, `neutral` or `adds`
- `ai`: `None` until personalized, then `{provider, fallback, cached, latency_ms, prompt_version, attempts, request, response}`
- `personalization_pending`: `True` until the first personalization
- `history`: `{request, status, created}`; `status` is `approved` or `rejected`
- `expense_form`, `categories_for_select`
- `notice` (only in htmx responses): `{message, kind}`, swapped out of band into `#status-region`
- `change_result`, `alternatives`: `None` unless the no-JavaScript path shows an answer

### `partials/change_result.html` (variable `result`)
- `kind`: `proposal`, `clarification`, `off_topic`, `unavailable`, `invalid` or `nothing_to_change`
- `title`, `message`, `ai_note`, `show_form`, `event_id`, `categories_for_select`
- `proposal` (see below) or `None`

### `partials/proposal.html` (variable `proposal`)
`{id, request, status, feasible, shortfall, fields, budget, expenses, tasks, vendors,
new_tasks, skipped, protected_kept, summary_before, summary_after}`. Budget rows are
`{name, before, after, delta, reason, overcommitted}`; `reason` is already a plain-language label.

### `partials/alternatives.html` (variable `alts`)
`{event_id, shortfall, summary, ai_note, options}`; each option is
`{category, title, why, action, level, saving, shortfall_after}`.

## 5. Accessibility rules

- Exactly one `h1` per page; headings do not skip levels.
- Every control has a visible `<label>` (or a `<legend>` for a group). Visually hidden
  text uses `.sr-only`, never `display: none`.
- Repeated buttons ("Protect", "Mark done", "Save") keep their `.sr-only` suffix naming the item.
- Toggle buttons keep `aria-pressed`. Invalid fields keep `aria-invalid` and `aria-describedby`.
- Errors use `role="alert"`; other updates use `role="status"` or the `aria-live` status region.
- Text contrast is at least 4.5:1 (3:1 for large text and for borders of controls) in
  **both** light and dark themes. Recompute every pair you change.
- Keep visible focus (`:focus-visible`), the skip link and `prefers-reduced-motion`.
- Touch targets are at least 24 × 24 px. The layout works from 320 px wide and at 200% zoom.
- Information is never carried by colour alone: badges always contain text.

## 6. Security rules

- The Content Security Policy is `default-src 'self'; script-src 'self'; style-src 'self';
  img-src 'self' data:; connect-src 'self'`. So: no inline `<script>` or `style=""`,
  no event-handler attributes (`onclick`), no CDN files, no web fonts from other hosts.
  Fonts, if any, are self-hosted under `public/static/` and kept small.
- Autoescaping stays on. Never use `|safe` on user or AI text.
- Third-party files go in `public/static/vendor/` with a checksum in `VENDOR.md`.

## 7. Fixed text

These labels are an honesty rule (AGENTS.md): AI output is labelled, non-AI output is not.

- `Suggested by AI` (class `label-ai`) and `Standard ideas (not AI-generated).` (class `label-not-ai`)
- The `ai_note` lines (class `ai-note`), which come from the server
- `How this was generated` (the `ai-panel` disclosure)
- The busy message: `This can take up to 10 seconds.`

## 8. CSS hooks

| Class | Meaning |
|---|---|
| `notice`, `notice-info`, `notice-success`, `notice-error` | Status messages |
| `field`, `field-invalid`, `field-hint`, `field-error`, `choice` | Form layout and validation |
| `badge`, `badge-error`, `badge-warning`, `badge-ai` | Short status tags |
| `why` | The reason under every suggestion; this is the product's main idea, make it easy to spot |
| `label-ai`, `label-not-ai`, `ai-note`, `ai-panel` | Where an answer came from |
| `ideas`, `summary`, `tasks` (`li.done`), `task-title`, `task-due`, `actions` | Plan sections |
| `change-result`, `change-{kind}`, `proposal`, `proposal-actions`, `field-changes` | Change Manager |
| `change-form`, `change-form-toggle`, `alternatives` | Fallback form and alternatives |
| `history`, `history-status`, `event-list`, `event-header`, `preferences`, `lede`, `create-form`, `sub-events` | Other blocks |
| `htmx-indicator` | Busy message, hidden until htmx adds `htmx-request` |
| `sr-only`, `skip-link` | Accessibility utilities |

## 9. Guards

| Test | Checks |
|---|---|
| `tests/integration/test_web_flows.py` | Every flow with and without htmx; label audit (labels, unique ids, named buttons, one `h1`) on every page and fragment |
| `tests/integration/test_pages.py` | Landmarks, skip link, description, icon, no inline script or style |
| `tests/integration/test_security_headers.py` | CSP and other headers |
| `tests/unit/test_stylesheet.py` | Busy indicator hidden by default, `.sr-only`, skip link, focus, reduced motion, dark theme |
| `tests/unit/test_vendor.py` | htmx checksum |

Planned: Playwright with axe in CI for real-browser checks (contrast, focus order, zoom).
