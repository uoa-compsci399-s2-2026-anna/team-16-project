# Front end — Kai Commitment Food Waste Impact Calculator

The public application is plain HTML, CSS and JavaScript ES modules. There is no front-end
build step: the browser runs the files in this directory directly.

## Public pages

- `home.html` — **the production `/` page.** What the tool is, what to have to hand,
  where the numbers come from, what the statistics are not, and the latest Kai Commitment
  news. Every word of it also appears in `methodology.html`, `stats.html` or the
  calculator, deliberately: the page adds no new English to translate into twenty
  catalogues, and it carries no placeholder waiting for copy the client may never send
- `index.html` — the calculator, and only the calculator. It opens on step one; the
  landing screen it used to open on repeated what `home.html` says and stood between a
  visitor and the first question
- `stats.html` — privacy-protected aggregate statistics and charts
- `methodology.html` — published methodology, factors and provenance

Every page links to the other three through the shared public navigation. The calculator
keeps its own in-page step navigation and reset controls.

## Structure

```text
web/
  home.html                 The home page, served at `/`
  index.html                Six-step calculator, opening on step one
  stats.html                Aggregate statistics page
  methodology.html          Documentation and published-factor page
  README.md                 This file
  assets/                   Brand images and the self-hosted brand fonts
  css/styles.css            The whole design system, mobile-first from 375px
  locales/                  One catalogue per language, plus the manifest
  js/api.js                 The only module that calls fetch()
  js/i18n.js                Catalogues, negotiation and the language chooser
  js/news.js                WordPress post normalisation
  js/home.js                Home/news page entry point
  js/charts.js              Chart.js adapters for bar and doughnut charts
  js/stats.js               Statistics page entry point
  js/methodology.js         Documentation page entry point
  js/main.js                Calculator entry point
  js/calculator.js          The six-step wizard and its delegated listeners
  js/results.js             The results screen
  js/improvement.js         The alternative scenario and the comparison screen
  js/state.js               Single shared state object with a subscriber set
  js/units.js               Every mass conversion the front end performs
  js/view.js                escapeHtml, formatNumber, slug, STEPS, stepNav
  vendor/chart.umd.min.js   Self-hosted Chart.js runtime
  vendor/chart.js.LICENSE.md
  vendor/chart.js.SOURCE.md
```

`docs/interfaces.md` §7 is the contract for these modules and remains authoritative if it
and this file disagree. Do not rename API fields without following the contract-change
process in §0.

## Run locally

In production the public files and API are same-origin, with API routes under `/api/v1`.
The Home page also requests the public Kai Commitment WordPress posts endpoint through
`api.js`. If that request is unavailable, the page keeps a useful calculator call to action.

For a fixture-backed local run, start a static server from the **repository root** (not from
`web/`):

```bash
python -m http.server 8000
```

Then open the public pages under `http://127.0.0.1:8000/web/`. Add `?mock=1` to the
calculator, statistics or documentation URL to load the JSON contract fixtures instead of
the same-origin API. For example:

```text
http://127.0.0.1:8000/web/index.html?mock=1
http://127.0.0.1:8000/web/stats.html?mock=1
http://127.0.0.1:8000/web/methodology.html?mock=1
```

The document root must include both `web/` and `tests/`, because mock URLs resolve to
`tests/fixtures/`. Serving `web/` as the document root makes those fixtures unreachable.

> Do not use `?mock=1` for a client demonstration. Mock calculation responses contain fixed
> fixture impact figures rather than figures derived from the amount entered. Demonstrate
> against the real API with the mock factor set loaded so the engine calculates from the
> submitted values and the required placeholder-factor warning remains visible.

Mock calculator error states can be exercised with `mockError`, for example
`?mock=1&mockError=validation_error`. Supported fixture names are documented in
`docs/interfaces.md` §7.1.

## Front-end constraints

- Only `api.js` performs network requests.
- Apart from conversion of user-entered mass in `units.js`, impact values come from the API.
- Taxonomy, metrics and equivalences are data-driven rather than hard-coded in views.
- Negative impact values retain their sign in text and charts.
- A mock factor set produces a persistent, conditional warning in every results view.
- The returned anonymous session token is stored in `sessionStorage` and reused.
- Layouts are checked at 320px, 375px, the 481–849px tablet band and desktop widths.
