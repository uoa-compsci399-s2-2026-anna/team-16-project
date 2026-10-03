# Front end — Kai Commitment Food Waste Impact Calculator

The public application is plain HTML, CSS and JavaScript ES modules. There is no front-end
build step: the browser runs the files in this directory directly.

## Public pages

- `index.html` — **the production `/` page**, and the calculator. It opens on its own
  introduction screen: the eyebrow, the heading, "Start the calculator", the "What you
  will need" check-list and the privacy note. That screen costs one click before step
  one, and the team accepted it
- `stats.html` — privacy-protected aggregate statistics and charts
- `methodology.html` — published methodology, factors and provenance

Every page links to the other two through the drawer (`js/drawer.js`), which is on all
three; `stats.html` and `methodology.html` also carry the header navigation. The
calculator keeps its own in-page step navigation and reset controls.

### Retired, and why it is still here

- `home.html`, with `js/home.js` and `js/news.js` — **retired. Nothing links to it and
  `/` no longer serves it.** It was the landing page for one week. The team dropped it:
  it looked poor, and it duplicated the client's own website, which already carries this
  material.

  It is **retired rather than deleted because the client has not decided about the news
  feed** it carries, which is the one thing on it that exists nowhere else. Typing
  `/home.html` still works — the page renders, the feed loads when `KAICALC_NEWS_ORIGIN`
  is set, and every string on it is still translated in all twenty catalogues, because
  `tests/web/i18n_keys.py` reads the files in this directory and does not ask which of
  them anybody can reach.

  Reviving it is one line in `docker/nginx.conf`, one drawer row on three pages, and a
  decision about the calculator's introduction screen — the thing this page displaced.
  Deleting it also means three modules' keys out of twenty catalogues,
  `KAICALC_NEWS_ORIGIN`, `KAICALC_NEWS_IMAGE_ORIGINS` and the two CSP directives derived
  from them. **Neither is a tidy-up; both need the client's answer.**

## Structure

```text
web/
  index.html                Six-step calculator, served at `/`, opening on its
                            own introduction screen
  home.html                 RETIRED - see above. In the tree, reachable from
                            nothing, pending the client's decision on the news feed
  stats.html                Aggregate statistics page
  methodology.html          Documentation and published-factor page
  README.md                 This file
  assets/                   Brand images and the self-hosted brand fonts
  css/styles.css            The whole design system, mobile-first from 375px
  locales/                  One catalogue per language, plus the manifest
  js/api.js                 The only module that calls fetch()
  js/i18n.js                Catalogues, negotiation and the language chooser
  js/news.js                WordPress post normalisation - RETIRED with home.html
  js/home.js                Home/news page entry point - RETIRED with home.html
  js/charts.js              Chart.js adapters for doughnut, pie, bar and line charts
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
- Negative impact values retain their sign in text and signed Bar/Line charts; Pie rejects finite negative values before chart creation.
- Each non-empty statistics breakdown defaults to Pie and has its own Pie/Bar/Line selector. Line compares the service's count-ranked categories, with any combined Other bucket last; it is not a time trend. The page uses API-provided shares without reordering or re-suppression, and type/language changes use the cached response rather than another statistics request.
- Statistics chart tooltips and Bar/Line y-axis ticks use the same share `formatValue` callback as the share text list. The 16 brand `{fill, ink}` pairs give distinct keys collision-free colours up to 16; additional buckets remain visible but may reuse a brand colour. Long on-canvas legend labels are fitted while full labels remain in tooltip and text, and reduced-motion preferences are respected.
- A mock factor set produces a persistent, conditional warning in every results view.
- The returned anonymous session token is stored in `sessionStorage` and reused.
- Layouts are checked at 320px, 375px, the 481–849px tablet band and desktop widths.
