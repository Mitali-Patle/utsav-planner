# Changelog

All notable changes to Utsav Planner will be documented in this file.

## [Unreleased]
### Added
- Frontend foundational assets and base layout template (SPIKE-A01):
  - Vendored pinned htmx 2.0.4 at `public/static/vendor/htmx.min.js`.
  - Accessible design system stylesheet with WCAG 2.2 AA contrast tokens, light/dark themes via `prefers-color-scheme`, `:focus-visible` styles, `prefers-reduced-motion`, and responsive layout down to 320 px at `public/static/css/app.css`.
  - Strict-CSP compliant Jinja2 base template with `lang="en-IN"`, skip link to `#main`, semantic landmarks, polite aria-live status region, and disabled htmx indicator styles at `app/web/templates/base.html`.
