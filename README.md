# Kai Commitment Food Waste Impact Calculator

COMPSCI 399 project repository for Team 16 — 502 Bad Gateway.

The public front end is implemented as plain HTML, CSS and JavaScript ES modules in `web/`. It has no Node.js dependency and no front-end build step.

## Front-end structure

```text
web/
  index.html          Calculator entry point
  methodology.html    Published methodology view
  assets/             Brand and hero images
  css/styles.css      Shared responsive design system
  js/api.js           The only module that calls fetch()
  js/state.js         Single shared front-end state object
  js/units.js         Unit-to-kilogram conversion
  js/calculator.js    Calculator steps and interaction
  js/results.js       Server result rendering
  js/methodology.js   Published factor rendering
  js/main.js          Application bootstrap
```

The data contract is defined in `docs/interfaces.md`. Do not rename request or response fields in the front end without following the contract-change process in that document.

## Run the application

The production application expects the front end and FastAPI service on the same origin. The API base path is `/api/v1`.

Once the backend application is available, configure it to serve the `web/` directory as static files and open its calculator URL in a browser. The front end calls:

- `GET /api/v1/taxonomy`
- `POST /api/v1/calculate`
- `GET /api/v1/factors`
- `GET /api/v1/stats` (available through the shared API module)

A static file server by itself can display the application shell, but the calculator will intentionally show an unavailable state until `GET /api/v1/taxonomy` is reachable.

## Front-end constraints

- Taxonomy options come from the API and are not hard-coded in UI components.
- Impact calculations are performed only by the backend.
- Decimal quantities are submitted as strings.
- The returned session token is stored in `sessionStorage` and reused during the browser session.
- A non-dismissible warning is shown whenever `factor_set.is_mock` is true.
- The responsive baseline is 375px.
