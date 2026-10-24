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
| `GET /` | `landing.html`; `?example=wedding`, `birthday` or `community` fills the form (`EXAMPLES` in `views.py`); an unknown name gives the empty form | |
| `GET /plans` | `plans.html`: this visitor's plans, soonest first, or an empty state; "+ New plan" goes to the form | |
| `POST /events` | 303 to the dashboard; `landing.html` with 422 on invalid input | |
| `GET /events/{id}` | `dashboard.html` | |
| `POST /events/{id}/personalize` | 303 to the dashboard; repeating it after standard (non-AI) ideas asks the AI again | `partials/plan_area.html` |
| `GET /events/{id}/calendar.ics` | `.ics` download (`no-store`): the event day, unfinished tasks and vendors still to book | |
| `POST /events/{id}/ideas` | 303 to the dashboard's `#ideas`; replaces only the ideas with different AI ideas, or keeps them with a notice | `partials/plan_area.html` |
| `POST /events/{id}/expenses` | 303 to the dashboard; 422 with the form and its errors | `partials/plan_area.html` |
| `POST /events/{id}/expenses/{eid}/update`, `/delete`, `/protect`, `/unprotect` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/categories/{cid}/protect`, `/unprotect` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/tasks/{tid}/done`, `/undone` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/vendors/{vid}/status` | 303 to the dashboard | `partials/plan_area.html` |
| `POST /events/{id}/changes` (field `request`) | `dashboard.html` with the answer in place | `partials/change_result.html` |
| `POST /events/{id}/changes/form` | `dashboard.html` with the answer in place | `partials/change_result.html` |
| `POST /events/{id}/proposals/{pid}/approve` | 303 to the dashboard; 409 or 422 with the reason | `HX-Redirect` to the dashboard; 409 or 422 with `change_result.html` |
| `POST /events/{id}/proposals/{pid}/reject` | 303 to the dashboard | `HX-Redirect` to the dashboard |
| `POST /events/{id}/alternatives` | `dashboard.html` with alternatives for the plan's shortfall | `partials/alternatives.html` |
| `POST /events/{id}/alternatives/apply` (fields `category_id`, `action`, `level`) | `dashboard.html` with the preview in place | `partials/change_result.html` into `#change-result` |

Not-found and domain errors (404, 422) render `error.html`; for htmx requests the server
sends `partials/notice.html` with `HX-Retarget: #status-region`. A missing CSRF token (403)
or a body over 32 KB (413) is stopped by the request guard before routing, with a bare
`<p role="alert">` message.

## 2. Progressive enhancement

Every action must work **without JavaScript**.

- Each form keeps `method="post"` and its `action`, and contains `{{ csrf(csrf_token) }}`.
- htmx attributes (`hx-post`, `hx-target`, `hx-swap`) are added on top of a working form,
  never in place of one. No links or buttons that work only with JavaScript. The one
  exceptions are `#print-plan` and `#theme-toggle`, which carry `hidden` and are shown by
  `print.js` and `theme.js` only when the scripts run; without JavaScript the browser's own
  print gives the same printout and the theme follows the device.
- `<body hx-headers=...>` sends the CSRF token on htmx requests. Keep it on `<body>`.
- The `htmx-config` meta tag in `base.html` makes 404, 409 and 422 swap like 200, turns off
  htmx's inline indicator styles and leaves `style` out of `attributesToSettle`, so swaps never
  trip the CSP. Keep all three settings.

## 3. Fixed ids and swap targets

| id | Where | Why it is fixed |
|---|---|---|
| `main` | `<main>` | Skip link target |
| `status-region` | `base.html`, outside `<main>` | `aria-live` region; flashes, out-of-band notices and htmx errors land here |
| `plan-area` | Root of `plan_area.html` | Target of every plan edit (`outerHTML`) |
| `personalization` | `plan_area.html` | Fires `hx-trigger="load"` once to personalize |
| `change-result` | `dashboard.html` | Target of change requests and approvals (`innerHTML`) |
| `change-result-heading` | `change_result.html` | Heading of the answer; `tabindex="-1"` and `autofocus`, so htmx moves focus to it |
| `change-busy` | `dashboard.html` | `hx-indicator` of the change request form |
| `plan-alternatives` | `plan_area.html` | Target for alternatives when the plan is over budget |
| `add-expense-form` | `plan_area.html` | Carries `hx-preserve` when `keep_typed_input` is true, so an AI swap keeps what the user typed |
| `retry-personalize` | `plan_area.html` | Asks the AI again when the ideas are standard ones |
| `new-ideas`, `ideas-busy` | `plan_area.html` | "Suggest different ideas" button and its `hx-indicator`; shown only when the ideas came from AI |
| `print-plan` | `dashboard.html` | "Print or save as PDF"; `hidden` until `/static/js/print.js` shows it and calls `window.print()` |
| `calendar-export` | `dashboard.html` | "Add to calendar": the `.ics` download, in the event banner |
| `theme-toggle` | `base.html` | Header switch cycling Auto, Light and Dark; `theme.js` (loaded in `<head>`, no `defer`) applies the saved choice before paint as `data-theme` on `<html>` |
| `add-plan` | `plans.html` | "+ New plan": the only new-plan button; links to the form on the home page |
| `type-{type}`, `type-{type}-hint` | `landing.html` | Event type cards (radio plus description); a "Fill in a sample …" link for the chosen type appears below them through CSS |
| `ideas-heading` | `plan_area.html` | Gets `tabindex="-1"` and `autofocus` when `focus_ideas` is true, so focus lands on the new ideas |
| `{panel}-sent`, `{panel}-answer` | `ai_panel.html` | Headings naming the panel's data regions; `{panel}` is `plan-ai`, `change-ai` or `alternatives-ai`, so several panels can share a page |
| `protect-line-{id}`, `protect-expense-{id}`, `update-{id}`, `task-{id}`, `save-{id}`, `add-expense`, `suggest-savings` | `plan_area.html` | Stable button ids: htmx returns keyboard focus to the same button after the plan area is swapped |

Form field names are fixed: they are the server's API. Field ids follow
`{prefix}-{name}` from the `field` macro (`f-` on the landing page, `x-` for expenses). The
change form can appear twice on a page, so its ids use `form_prefix` (`cf-` on the dashboard,
`cfr-` inside a result).

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
- `plan.summary`: `{total, spent, remaining, per_guest, guests, shortfall, over_plan}`; `shortfall` is `None` unless over budget; `over_plan` names the lines spent past their plan
- `plan.categories`: `{id, name, allocated, spent, remaining, overspent, protected}`
- `plan.expenses`: `{id, label, category, amount, amount_inr, protected}`
- `plan.tasks`: `{id, title, due, done, overdue, compressed, source, ai, why, category}`
- `plan.vendors`: `{id, name, priority, book_by, urgent, status, status_label, next: [(value, label)], why}`
- `ideas`: `{title, description, why, impact}`; `impact` is `saves`, `neutral` or `adds`
- `ai`: `None` until personalized, then `{provider, fallback, cached, latency_ms, prompt_version, attempts, request, response}`
- `personalization_pending`: `True` until the first personalization
- `history`: `{request, status, created}`; `status` is `approved` or `rejected`
- `expense_form`, `categories_for_select`
- `keep_typed_input`: `True` only in the personalization and different-ideas responses, which arrive seconds after the click or the page
- `focus_ideas`: `True` only in the different-ideas response
- `notice` (only in htmx responses): `{message, kind}`, swapped out of band into `#status-region`
- `change_result`, `alternatives`: `None` unless the no-JavaScript path shows an answer

### `partials/change_result.html` (variable `result`)
- `kind`: `proposal`, `clarification`, `off_topic`, `unavailable`, `invalid` or `nothing_to_change`
- `title`, `announce` (sent to the status region), `message`, `ai_note`, `ai` (panel data or `None`), `show_form`, `event_id`, `categories_for_select`
- `proposal` (see below) or `None`

### `partials/proposal.html` (variable `proposal`)
`{id, request, status, feasible, shortfall, fields, budget, expenses, tasks, vendors,
new_tasks, skipped, protected_kept, protected_lines, summary_before, summary_after}`. When
`feasible` is false, `protected_lines` names what could be unprotected to make it fit. Budget rows are
`{name, before, after, delta, reason, overcommitted}`; `reason` is already a plain-language label.

### `partials/alternatives.html` (variable `alts`)
`{event_id, shortfall, summary, ai_note, ai, options}`; each option is
`{category_id, category, title, why, action, level, applicable, saving, shortfall_after}`.
`saving` and `shortfall_after` come from the preview that applying the option creates; only
`applicable` options get the "Preview this change" form (button id `apply-option-{n}`).

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
| `ideas`, `ideas-ai`, `summary`, `budget-meter`, `meter`, `meter-over`, `budget-table`, `month`, `tasks` (`li.done`), `task-title`, `task-due`, `actions`, `toggle` | Plan sections; `meter` is a decorative `<progress>` (the amounts are in the text) |
| `event-header-main`, `event-header-side`, `event-meta`, `event-actions`, `banner-button`, `countdown`, `countdown-number`, `kicker` | Dashboard banner |
| `dashboard-layout`, `dashboard-main`, `section-nav` (`a[aria-current]`), `rail-cta` | Section menu: a sticky bar below 1100 px, a side rail above; `sections.js` sets `aria-current="location"` on the section in view |
| `landing-top`, `landing-hero`, `how-compact`, `how`, `landing-main`, `panel` | Landing page: headline and "How it works" (`partials/how_it_works.html`) on the left, 70:30 with the picture panel |
| `reel` (`[data-reel]`, `is-running`, `is-paused`), `reel-window`, `reel-track`, `reel-slide`, `reel-frame`, `reel-tone-{n}`, `reel-toggle` | Celebration pictures: `CELEBRATION_SLIDES` in `views.py` (an image in `public/static/img/celebrations/`, or a placeholder tone); the slides are listed twice for a seamless loop, the copy `aria-hidden`; `reel.js` starts it and shows "Pause pictures" (WCAG 2.2.2); still without JavaScript or with reduced motion |
| `plans-header`, `add-plan`, `plan-grid`, `plan-tile`, `plan-tile-type`, `plan-tile-meta`, `plan-tile-days`, `empty-plans` | My plans page |
| `form-step`, `step-number`, `legend-note`, `type-cards`, `type-card`, `type-card-hint`, `samples`, `sample[data-event-type]`, `field-row`, `input-adorned`, `adornment`, `functions`, `chips`, `chip`, `form-submit` | New-plan form |
| `site-brand`, `brand-mark`, `brand-sub`, `nav-plans` (`aria-current="page"` on /plans), `nav-icon`, `theme-toggle`, `theme-icon`, `theme-label` | Header |
| `ideas-new` | Ideas just replaced by "Suggest different ideas"; they fade in one after another |
| `change-result`, `change-{kind}`, `proposal`, `proposal-actions`, `field-changes` | Change Manager |
| `change-form`, `change-form-toggle`, `alternatives` | Fallback form and alternatives |
| `history`, `history-status`, `event-header`, `preferences`, `lede`, `create-form`, `sub-events` | Other blocks |
| `button-primary` | The main action of a form (create, preview, approve, add, personalize); one per form |
| `sub-events[data-event-type]`, `sub-events-hint` | Function groups; CSS shows only the chosen event type's group |
| `htmx-indicator` | Busy message, hidden until htmx adds `htmx-request` |
| `sr-only`, `skip-link` | Accessibility utilities |
| `no-print`, `print-only`, `print-button`, `calendar-export` | Print view (F15): hidden on paper, shown only on paper, the print button, the `.ics` link (hidden on paper) |

## 9. Guards

| Test | Checks |
|---|---|
| `tests/integration/test_web_flows.py` | Every flow with and without htmx; label audit (labels, unique ids, named buttons, one `h1`) on every page and fragment |
| `tests/integration/test_pages.py` | Landmarks, skip link, description, icon, no inline script or style, htmx config fits the CSP |
| `tests/integration/test_security_headers.py` | CSP and other headers |
| `tests/unit/test_stylesheet.py` | WCAG contrast of every colour pair as used (4.5:1 text, 3:1 focus and borders) in both themes; busy indicator hidden by default; `.sr-only`, skip link, focus, reduced motion, dark theme |
| `tests/unit/test_vendor.py` | htmx checksum |
| `tests/e2e/` (`pytest -m e2e`) | Real Chromium: journeys with and without JavaScript; axe WCAG 2.2 A/AA on six page states in light and dark at 375 and 1280 px; no horizontal scrolling at 320 px; skip link; a clean console with no CSP violations |

When a colour is used in a new combination, add the pair to `CONTRAST_PAIRS` in
`tests/unit/test_stylesheet.py`.

CI (`.github/workflows/ci.yml`) runs all of these on every push to `main`.
