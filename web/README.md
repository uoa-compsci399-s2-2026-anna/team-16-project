# Front end — Kai Commitment Food Waste Impact Calculator

The public application is plain HTML, CSS and JavaScript ES modules. There is no front-end
build step: the browser runs the files in this directory directly.

## Public pages

- `index.html` — calculator entry point (and the production `/` page)
- `home.html` — introduction, calculator call to action and latest Kai Commitment news
- `stats.html` — privacy-protected aggregate statistics and charts
- `methodology.html` — published methodology, factors and provenance

Every page links to the other three through the shared public navigation. The calculator
keeps its own in-page step navigation and reset controls.

## Structure

```text
web/
  home.html                 Home and news page
  index.html                Six-step calculator
  stats.html                Aggregate statistics page
  methodology.html          Documentation and published-factor page
  assets/                   Brand images and self-hosted fonts
  css/styles.css            Shared design system and responsive page layouts
  js/api.js                 The only module that calls fetch()
  js/news.js                WordPress post normalisation
  js/home.js                Home/news page entry point
  js/charts.js              Chart.js adapters for pie, bar and line charts
  js/stats.js               Statistics page entry point
  js/methodology.js         Documentation page entry point
  js/main.js                Calculator entry point
  js/calculator.js          Calculator wizard and delegated listeners
  js/results.js             Calculator results view
  js/improvement.js         Alternative scenario and comparison view
  js/state.js               Shared calculator state and subscribers
  js/units.js               User-entered mass conversions
  js/view.js                Shared rendering helpers
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
- Statistics renders destination, sector and food-category shares. Each breakdown has its
  own pie, bar or line selector, with pie as the default. Switching the chart changes only
  the presentation of the API's raw `share` values; tooltips format them as percentages.
- The line chart's horizontal axis follows category order from the service; it does not
  represent time or a trend. Each chart has a short explanation of what its share measures.
- Every statistics chart is followed by a complete text list of its buckets, counts, shares
  and cumulative quantities; the canvas is not the only representation of the data.
- Chart colours are deterministic for stable bucket codes (or labels when no code is
  supplied), and the palette continues to generate colours when additional buckets arrive.
- Chart animation is disabled when `prefers-reduced-motion: reduce` matches.
- A mock factor set produces a persistent, conditional warning in every results view.
- The returned anonymous session token is stored in `sessionStorage` and reused.
- Layouts are checked at 320px, 375px, the 481–849px tablet band and desktop widths.
