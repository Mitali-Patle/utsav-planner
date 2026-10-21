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

### Changed
- Upgraded vendored htmx to official unmodified `htmx.org@2.0.11` without added headers (`public/static/vendor/htmx.min.js`).
- Adjusted light and dark theme tokens in `public/static/css/app.css` to guarantee all text/background pairs achieve $\ge$ 4.5:1 contrast and UI borders / focus outlines achieve $\ge$ 3.0:1 contrast.
- Removed redundant ARIA landmark roles from native `<header>`, `<nav>`, and `<footer>` elements in `app/web/templates/base.html`.
