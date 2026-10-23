# Changelog

All notable changes to Utsav Planner will be documented in this file.

## [Unreleased]
### Added
- Frontend foundational assets and base layout template (SPIKE-A01):
  - Vendored pinned htmx 2.0.4 at `public/static/vendor/htmx.min.js`.
  - Accessible design system stylesheet with WCAG 2.2 AA contrast tokens, light/dark themes via `prefers-color-scheme`, `:focus-visible` styles, `prefers-reduced-motion`, and responsive layout down to 320 px at `public/static/css/app.css`.
  - Strict-CSP compliant Jinja2 base template with `lang="en-IN"`, skip link to `#main`, semantic landmarks, polite aria-live status region, and disabled htmx indicator styles at `app/web/templates/base.html`.
- SVG favicon at `public/static/img/favicon.svg` and linked via `<link rel="icon">` in `base.html`.
- One-sentence non-persona `<meta name="description">` in `base.html`.
- Vendored static asset documentation with SHA-256 checksums at `public/static/vendor/VENDOR.md`.
- Plan an event in a browser: create a wedding, birthday or community event and get a complete budget, timeline and vendor checklist at once, then AI-personalized ideas, each with a reason.
- Edit the plan directly: expenses, protected budget lines and expenses, task progress and vendor booking status.
- Smart Change Manager: describe a change in plain words (or use a form that works without AI), preview exactly what changes, then approve or reject; approved and rejected changes appear in a history.
- Priced ways to close a budget gap, from AI or labelled standard suggestions.
- Ways to save show exactly what they free and what gap would remain, and each has a "Preview this change" button that turns it into a Change Manager preview to approve or reject.
- "How this was generated" panel showing the model, prompt version, attempts, the data sent and the validated answer.
- `docs/UI_CONTRACT.md`: the template, data and behaviour contract between the server and the interface.
- README with how the app works, where AI is used, evaluation results, security, accessibility, testing and limitations.
- The new-event form reminds users that an AI model reads their preferences, so personal details should be left out.

### Changed
- The change history shows what each change did (for example "Total budget ₹15,000 → ₹12,000", "3 budget lines changed").
- Pages load with less database work (one transaction for the dashboard, three for a change request).
- Change requests that state an amount ("add a line for a magician, ₹5,000") are understood reliably by the backup AI models, and "postponed by 10 days" always moves the date later.
- When a change frees money or raises the total, it goes first to budget lines already spent past their plan, so the shortfall closes where it is.
- Suggested savings skip budget lines whose money is already spent, and the standard (not AI-generated) suggestions rank lines by the money still unspent.
- "Suggest different ideas" asks the AI for new ideas that avoid the current ones; only the ideas are replaced, and the old ones stay if no model answers.
- AI suggestions are sized to the gap more reliably: the model is told exactly how much each option would free.

### Fixed
- Text typed into the add-expense form while the AI is still personalizing a new plan is no longer cleared when the ideas arrive.
- New plans are limited per connection per hour, and a plan holds at most 200 expenses and 200 tasks.
- Set one budget line's planned amount, in plain words ("put ₹10,000 into decorations") or with the change form; the other lines are rebalanced and the total stays exact.
- Upgraded vendored htmx to official unmodified `htmx.org@2.0.11` without added headers (`public/static/vendor/htmx.min.js`).
- Adjusted light and dark theme tokens in `public/static/css/app.css` to guarantee all text/background pairs achieve $\ge$ 4.5:1 contrast and UI borders / focus outlines achieve $\ge$ 3.0:1 contrast.
- Removed redundant ARIA landmark roles from native `<header>`, `<nav>`, and `<footer>` elements in `app/web/templates/base.html`.
- Festive, warmer design for every page in light and dark themes: cards for ideas, reasons highlighted, a clear AI panel, budget tables that become cards on phones, and highlighted main actions.
- The new-event form shows only the functions of the chosen event type.
- Budget comes before ideas on the dashboard.
- AI answers usually arrive in about a second: Groq is now tried first, with Gemini as the backup.
- Change requests that add or update expenses and budget lines are understood more reliably, and money-saving suggestions are sized to the gap they need to close.

### Fixed
- Only the newest preview of a change can be approved; older ones are marked out of date instead of staying "Pending" forever, and the history lists approved and rejected changes only.
- History dates are shown in India time.
- Unknown pages show the friendly error page instead of raw JSON.
- When the AI was unavailable or busy, the standard ideas stayed forever; a "Personalize with AI" button now asks again, and a busy repeat no longer relabels AI ideas as standard ones.
- A change that cannot fit the budget now says exactly how to make it fit (the extra total needed, or which protected lines to release) instead of offering savings that cannot help.
- Change previews and money-saving suggestions now have their own "How this was generated" panel, like personalization.
- Screen readers hear when a change preview is ready, focus moves to it, and keyboard focus stays on a button after Mark done, Protect or Save.
- Form labels stay correct when the change form appears twice on a page.
- A budget line spent past its plan now shows the plan as over budget, even while total spending is under the budget, and the alert names the lines that cost more than planned.
- Approving and rejecting the same change at the same moment (two tabs) can no longer leave the plan changed while the history says rejected.
- The "Interpreting your request" busy message no longer shows when nothing is loading.
- Priced alternatives are labelled "Suggested by AI" instead of "Interpreted by AI".
- Links and phone-layout labels now meet 4.5:1 contrast in the light theme.
- Swapping parts of the page no longer logs Content Security Policy errors in the browser console.
- Form controls outside the main forms (vendor status, expense amounts, the change request) are styled and readable in dark mode.
- The data panels in "How this was generated" can be scrolled with the keyboard.
