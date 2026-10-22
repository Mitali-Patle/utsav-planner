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
- "How this was generated" panel showing the model, prompt version, attempts, the data sent and the validated answer.
- `docs/UI_CONTRACT.md`: the template, data and behaviour contract between the server and the interface.

### Changed
- Upgraded vendored htmx to official unmodified `htmx.org@2.0.11` without added headers (`public/static/vendor/htmx.min.js`).
- Adjusted light and dark theme tokens in `public/static/css/app.css` to guarantee all text/background pairs achieve $\ge$ 4.5:1 contrast and UI borders / focus outlines achieve $\ge$ 3.0:1 contrast.
- Removed redundant ARIA landmark roles from native `<header>`, `<nav>`, and `<footer>` elements in `app/web/templates/base.html`.

### Fixed
- The "Interpreting your request" busy message no longer shows when nothing is loading.
- Priced alternatives are labelled "Suggested by AI" instead of "Interpreted by AI".
