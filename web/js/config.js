/**
 * Deployment configuration for the front end. Contract §7.9.
 *
 * **This file is the checked-in default, and a container overwrites it at start-up.**
 * `docker/web-config.sh` runs from nginx's `/docker-entrypoint.d/` before the server
 * binds, reads `KAICALC_NEWS_ORIGIN` and `KAICALC_API_ORIGIN` from the environment, and
 * rewrites this exact file inside the image. The same run builds the `connect-src` and
 * `img-src` values of the public Content-Security-Policy from the same two variables, so
 * **the policy and the page cannot disagree**: one environment variable feeds both, and
 * there is no second copy to forget. That is the whole point of the module — a domain
 * hard-coded here and again in `docker/nginx.conf` is two things that must agree, and
 * they drifted asymmetrically: a wrong policy makes the news quietly not load, a wrong
 * URL makes the browser go and ask a domain nobody chose.
 *
 * **Why a generated ES module and not a `<meta>`, a `sub_filter` or a config endpoint.**
 * The front end has no build step and no framework, so the value has to arrive at
 * runtime. A same-origin module is the only one of the four that costs nothing and
 * survives the policy unchanged: it is fetched under `script-src 'self'` like every other
 * module, it joins the module graph a deferred `<script type="module">` already loads
 * (no extra round trip before first paint, and no request that has to complete before
 * anything renders), and it works untouched from a plain checkout because the checked-in
 * defaults below are valid JavaScript. A `<meta>` element needs every HTML page rewritten
 * and gives the value to anything that can inject one; `sub_filter` rewrites every
 * response body and interacts badly with the `gzip on` two directives away; a
 * `GET /config.json` adds a request that the news feed has to wait on for no gain.
 *
 * **Nothing here is a secret and nothing here is trusted.** Both values are read only
 * from the operator's environment at container start — never from the URL, a query
 * parameter, `sessionStorage` or an element in the page — and `docker/web-config.sh`
 * refuses to start the container unless each is a bare `scheme://host[:port]`. The CSP is
 * generated from the same two strings, so the browser can only ever reach the origins the
 * operator declared: a value that arrived any other way is refused by the policy.
 *
 * @module config
 */

/**
 * Origin of the WordPress site whose posts the home page lists, as
 * `scheme://host[:port]` with no trailing slash — the REST path is appended by
 * `getNewsPosts` in `api.js`.
 *
 * **Empty means there is no news feed, and that is a supported deployment rather than an
 * error.** Most deployments of this calculator have no WordPress to read. Unset, the home
 * page removes its news section entirely instead of asking a guessed domain for posts or
 * leaving a "temporarily unavailable" notice standing for a service that was never
 * configured.
 *
 * @type {string}
 */
export const NEWS_ORIGIN = ''

/**
 * Origin of the public API, as `scheme://host[:port]` with no trailing slash.
 *
 * **Empty is the default and the designed topology**: `api.js` builds `/api/v1`, a
 * relative path, so every call goes to whatever origin served the page and nginx routes
 * it on. Same-origin means no CORS and no preflight, and it is what the shipped
 * `docker/compose.yaml` runs.
 *
 * It is configurable only because the alternative was worse. Split the API onto its own
 * subdomain and the relative path has to change here *and* `connect-src` has to change in
 * `docker/nginx.conf`, or every call is refused by our own policy with nothing in the
 * failure pointing at nginx — the same two-copies defect this module exists to remove.
 *
 * **Set it only with the same care as the nginx configuration itself.** This is the front
 * end of a tool whose numbers are the product; pointing it at an API the client does not
 * control means the client's branding over somebody else's arithmetic. Three things hold
 * that line: the value comes from the container's environment and from nowhere a visitor
 * can reach; the entrypoint refuses to start on anything that is not a bare origin, so a
 * typo is a container that does not come up rather than a calculator quietly showing the
 * wrong figures; and `connect-src` is generated from this same value, so the page can
 * reach the one origin named here and no other.
 *
 * @type {string}
 */
export const API_ORIGIN = ''
