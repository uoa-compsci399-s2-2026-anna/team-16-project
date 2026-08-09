# Front end — Kai Commitment Food Waste Impact Calculator

Plain HTML, CSS and JavaScript ES modules. No Node.js, no framework, no build step: what is
in this directory is what the browser runs.

This file exists because the same prose was written into the repository's root `README.md`,
which is the course template established by Asma Shakil and is not ours to edit. The content
is worth keeping — the `?mock=1` documentation below is how C, D and E all develop while the
backend is unmerged — so it lives here, next to the code it describes.

## Structure

```text
web/
  index.html            Calculator entry point
  methodology.html      Published methodology view
  README.md             This file
  assets/               Brand images and the self-hosted brand fonts
  css/styles.css        The whole design system, mobile-first from 375px
  js/api.js             The only module that calls fetch()
  js/state.js           Single shared state object with a subscriber set
  js/units.js           Every mass conversion the front end performs
  js/view.js            escapeHtml, formatNumber, slug, buttonRow
  js/calculator.js      The six-step wizard and its delegated listeners
  js/results.js         The results screen
  js/improvement.js     The alternative scenario and the comparison screen
  js/methodology.js     Entry point for methodology.html
  js/main.js            Entry point for index.html
```

`docs/interfaces.md` §7 is the contract for these modules and is the authority where this
file and it disagree. Do not rename a request or response field without following the
contract-change process in §0 of that document.

## Run the application

In production the front end and the FastAPI service are on the same origin, and the API base
path is `/api/v1`. Configure the backend to serve this directory as static files and open its
calculator URL. The front end calls:

- `GET /api/v1/taxonomy`
- `POST /api/v1/calculate`
- `GET /api/v1/factors`
- `GET /api/v1/stats` (through the shared API module; not yet used by a page)

A static file server on its own will display the shell, but the calculator deliberately shows
an unavailable state until `GET /api/v1/taxonomy` is reachable.

### Run against the contract fixtures, with no backend

From the **repository root** — not from `web/`:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://127.0.0.1:8000/web/index.html?mock=1
```

`?mock=1` is read once at module load and routes every call to the JSON files in
`tests/fixtures/`; production URLs continue to use `/api/v1`. Error states are reachable by
adding one of:

```text
&mockError=validation_error
&mockError=unknown_code
&mockError=rate_limited
&mockError=formula_error
&mockError=no_published_factor_set
```

> **The document root has to be an ancestor of both `web/` and `tests/`.** The fixture URL is
> resolved against `api.js`'s own module URL (`web/js/` → `../../tests/fixtures/`), so it
> follows the page wherever it is served from — but a browser clamps `../` at the origin
> root, so serving `web/` *as* the root makes the fixtures unreachable and every mock call
> 404s. That is why the command above is run from the repository root. Mock mode therefore
> cannot work under the production FastAPI static mount as it stands; a dev-only mount that
> exposes `tests/fixtures/` is B's to add.

## Constraints these modules are written to

Stated in full as §7.6 of `docs/interfaces.md`. In short:

- **The front end performs no impact calculation.** Every number on screen comes from the
  API, including cross-entry totals — read `totals` and `net_benefit` from the response,
  never a sum over `entries[]`. The single exception is mass conversion on what the *user*
  typed, and it lives in `units.js`.
- **Taxonomy options come from the API.** No code, name or unit is hard-coded in a view;
  adding a metric is meant to cost one database row and one formula.
- **Decimals travel as strings.** JavaScript's `Number` is a double, so a decimal from the
  API is coerced for display only.
- **A negative value is real and must look negative.** A downstream factor may be an offset,
  so a metric total may be below zero; charts draw it from a centre line rather than
  discarding the sign.
- **`factor_set.is_mock` puts a non-dismissible warning on every results view and every
  export**, and the warning is conditional on that flag rather than unconditional.
- **The returned session token is written back to `sessionStorage`** after every successful
  calculation and reused for the rest of the browser session.
- **Mobile-first, baseline 375px.**
