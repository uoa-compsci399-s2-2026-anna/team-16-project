---
title: "Kai Commitment Impact Calculator — Interface and Data Contract"
subtitle: "Single source of truth for five-way parallel development"
date: "2026-08-16 (v1.37 draft)"
---

# 0. How to Use This Document

This document defines **what every person's code receives and what it returns.** Any cross-module call is governed by this document.

> **Contract change process (mandatory):** update this document, then notify the whole team, then update the mock data in `tests/fixtures/*.json`. All three steps, every time. Renaming a field unilaterally is the most common source of rework on a five-person project.

**Ownership**

| Section | Owner | Content |
| --- | --- | --- |
| §2 Database | B | Schema and migrations |
| §3 Domain objects | A | Data classes for engine input and output |
| §4 Engine | A | Calculation function signatures |
| §5 Repository | B | The only functions that touch the database |
| §6 REST API | B (implements) / C, D (consume) | HTTP contract |
| §7 Front-end modules | C, D | JavaScript module signatures |
| §8 Admin panel | E | Staff-only interfaces |
| §9 Error codes | B | Global and uniform |

## 0.1 Change Log

### v1.37 — 2026-08-16 (the forwarded headers survive a proxy in front of ours; affects B and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **`docker/nginx-proxy-headers.conf` built both forwarded headers from what our own nginx saw, which is correct only while our own nginx is the outermost proxy.** With somebody else's TLS terminator in front — the ordinary shape on a public IPv4 whose 443 already belongs to another site — `X-Forwarded-Proto` became `http` over the edge's `https`, so the panel reasons about scheme from a false premise; and `X-Forwarded-For` became the edge's own address, so every visitor arrives as one caller. That second one is the state `admin/config.py` already names: §6.5's per-address limit becomes a single site-wide counter and one `ip_block` row denies everyone. DNS, certificates and hosting are out of the deliverable; **behaving correctly behind a terminator somebody else operates is not** | §7.8.1 |
| 2 | **Both headers now come from `map` blocks gated on a new `KAICALC_TRUST_FORWARDED_HEADERS`, and off — the default and the shipped arrangement — they resolve to exactly what the file said before.** An inbound copy of either header is discarded rather than believed. On, the scheme is taken if it is exactly `http` or `https` (normalised to lower case; anything else falls back to `$scheme`), and the forwarded chain is kept with this proxy's peer appended, so the left-most entry — the only one `db/detection.py::client_ip` reads — stays the visitor the edge saw | §7.8.1 |
| 3 | **"Forward what came in, fall back to what we saw" is wrong unconditionally, and the two headers are not equally dangerous.** A forged `X-Forwarded-Proto: https` marks the session cookie `Secure` and is self-inflicted; a forged `X-Forwarded-Proto: http` on a request that really is https takes `Secure` off a live staff session; a forged `X-Forwarded-For` names an address, out of the rate-limit bucket and out of the blocklist. Trust is therefore an opt-in an operator turns on only when an edge they control is the only way in | §7.8.1 |
| 4 | **It is a separate setting from `PROTECTION_TRUSTED_PROXY`, not the same one.** That variable asks whether the applications may believe the header *this proxy sends*; the new one asks whether this proxy may believe the header *it receives*. Reusing the name would mean turning on application trust also, silently, made this proxy believe headers from any direct caller — one switch meaning two things in two layers. All four combinations are real deployments; the one that looks configured and does nothing is reported by `docker/web-config.sh` at start-up, as a warning rather than a refusal because the web container's view of the applications' setting is second-hand | §7.8.1 |
| 5 | **The `envsubst` shell-format list grows from two names to three, and the criterion for being on it is written down.** `$remote_addr` and `$proxy_add_x_forwarded_for` eaten would forward an **empty** client address to both applications, which `client_ip` normalises to `None` — the blocklist and the rate limit skipped for every caller, with nothing raised. `KAICALC_TRUST_FORWARDED` occurs once in the template and can hold only `on` or `off` | §7.8 |
| 6 | **Nothing new is written down.** The `kaicalc` access-log format is unchanged and still carries no address and no header; `X-Real-IP` deliberately stays `$remote_addr` so one header always states the connection as it is. §2.3 is untouched | §2.3, §7.8.1 |
| 7 | **Proved with two nginx layers actually in front of each other.** `tests/test_web_forwarded_headers.py` chains an edge nginx in front of the real `kaicalc-web` image in front of an application stand-in that answers with the headers it received, and reads every assertion out of that reply — through both `location` blocks. Twelve mutations applied, twelve killed by their intended test; one survived first time round, because the untrusting assertions checked that the edge's *claimed* client was absent and an empty header satisfies that too | §7.8.1 |

### v1.36 — 2026-08-15 (one runtime configuration for the news origin and the API origin; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **The client's production domain is gone from `web/js/api.js` and from `docker/nginx.conf`, and appears in neither built image.** It was written out twice - as the WordPress base and as a `connect-src` entry - and the two had to agree. **They fail asymmetrically, which is why nothing would have caught the drift:** a wrong policy means the news quietly does not load, a wrong `NEWS_API` means the browser goes and asks a domain nobody chose. The API was not affected today, because `API_BASE` was relative, but the pair was a live trap for the moment that stopped being true. This project's deliverable is source and documentation with DNS, certificates and hosting out of scope, so the domain belongs to `docker/compose.yaml` - the deployment recipe - and to nothing else | §7.5, §7.6.10, §7.8 |
| 2 | **New §7.8 `config.js`: a generated same-origin ES module, written by `docker/web-config.sh` at container start.** The same run builds the `connect-src` and `img-src` of the public CSP from the same variables, so the policy and the page cannot name different hosts. No build step is involved and no request is added before first paint; the checked-in copy carries empty defaults so a plain checkout still works. A `<meta>`, a `sub_filter` and a `GET /config.json` were each weighed and each costs more - §7.8 says why | §7.8 |
| 3 | **An unset news origin renders no news section at all, and requests nothing.** `getNewsPosts` returns `null`, distinct from the `[]` that means "asked and got nothing usable", and `home.js` removes `.home-news` entirely. Leaving the heading standing over "temporarily unavailable" reports an outage for a service nobody configured, and a reader cannot tell that from a real one. Most deployments of this calculator have no WordPress: unset is supported, not degraded | §7.5, §7.8 |
| 4 | **`KAICALC_API_ORIGIN` exists, defaults to empty, and the guards on it are the contract.** Same-origin stays the designed topology and `/api/v1` stays relative. It is configurable because the alternative was the same two-copies defect one layer up - split the API onto a subdomain and both `api.js` and `connect-src` have to change, or every call is refused by our own policy. It is readable only from the operator's environment, never from a URL; the entrypoint refuses to start the container on anything that is not a bare `scheme://host[:port]`; and `connect-src` is generated from the same string. That validation is also what makes it safe to interpolate into a CSP header and a JavaScript literal | §7.8 |
| 5 | **`img-src` now follows the news origin, reversing the decision recorded on `createNewsCard`.** That note kept the directive narrow because widening it meant *guessing* the media origin, and told the next person to add the `<img>`, read the host off a test failure and edit two files in order. The origin is configuration now, so the guess is gone and the order no longer applies; the note has been rewritten to say so. `post.imageUrl` is still rendered nowhere. A WordPress library on a CDN is a different host and goes in `KAICALC_NEWS_IMAGE_ORIGINS` | §7.6.10, §7.8 |
| 6 | **`envsubst` is called with an explicit two-name list, and that is load-bearing.** With no argument it eats `$time_local`, `$uri`, `$scheme`, `$http_host` and the rest, leaving valid nginx syntax that logs blank lines and redirects to nothing. The mutation that drops the list fails on `$time_local` | §7.8 |
| 7 | **Proved in a running container with a value that is not the default, both configured and unconfigured.** `tests/test_web_runtime_config.py` reads the origin back out of the rendered header *and* the served module; `tests/web/test_csp.py` starts a second, unconfigured container and asserts in a real browser that the home page drops its news section and makes no off-origin request. A test that asserts a value exists does not assert anything reads it | §7.8 |

### v1.35 — 2026-08-15 (the gate's ground runs under the chooser, and the capsule gains a second skin; affects E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **The gate pages' Kale ground is now the page's ground and not the shell's, and v1.34 item 5's "it floats on the page's own ground" is where the defect hid.** That sentence was true and the ground was white: `brand.css` painted `.gate`, and the chooser is a **sibling** of `.gate` — it has to be, because `brand/base.html` owns it and every page's `{% block shell %}` replaces what is under it. So the capsule floated on a 72px full-bleed white strip with a hard edge under it and the deep green starting below, which is the full-bleed band v1.34 removed, reappearing as the body's own background. `body` carries the Kale now on any page wearing the gate shell, and the shell claims **what is left of the viewport** rather than a fresh `100vh` — the login page had been scrolling by exactly the height of the chooser for as long as the two were stacked | §7.7.4, §8 |
| 2 | **The ground is derived from `body:has(> .gate)`, not declared by a class on `<body>`.** The gate shell is chosen by *not* overriding a Jinja block, so a class would mean naming the ground a second time, and the six templates that do override `shell` with `<main class="page">` are six chances to forget — the failure being white text on a white page. `:has()` reads what the page actually rendered. Where it is unsupported the body stays white and the capsule keeps its light skin, which is exactly what shipped at v1.34: the fallback is the old appearance, not an unreadable one | §7.7.4 |
| 3 | **One capsule, two skins, and only colour differs.** v1.34 item 1's white surface, Kale shadow and Kale-filled button are all correct on Tabler's grey and all wrong on a Kale field: a Kale shadow cannot be seen there and a white capsule reads as a hole punched in the ground rather than an object on it. Every colour is a `--lang-*` token on `.language-bar` itself — which keeps `language.css` independent of `brand.css`, as v1.34 item 6 requires — and one block restates them for the Kale ground, selected the same derived way. **Nothing but colour is restated**: 44px, the 12px radius, the margins, `width: 20ch` and the 720px label rule are shared, so the two skins cannot come to disagree about the shape of the control. **The panel proper is unchanged** — same colours, same 416×44 box, same position | §7.7.4 |
| 4 | **On Kale the island is Kale at 12% white, lit along its top edge, and its only bright element is the button.** Elevation is inverted rather than recoloured: on a light ground an object is raised by the shade it casts, and #003223 cannot be darkened perceptibly at a 16px blur, so the raise is carried by a lit edge and a hairline. White was refused for the surface because it is the sign-in card's material and a second white object at the top of the page competes with the one the reader came for; the card stays the only white thing in the composition. The globe's disc is Pea, the same green as the card's half-circle | §7.7.4 |
| 5 | **The focus ring is two lines and ground-aware.** Blueberry measures **2.2:1 on Kale**, under the 3:1 a non-text indicator needs, and it was the ring on both grounds. It stays Blueberry on the light one — `brand.css` rings the login card's own inputs with it, so one page keeps one focus colour — and is **Banana at 10.9:1** on Kale, which is also the brand's dark-ground pairing and unmistakably not a colour a browser draws by itself. Underneath either, the capsule's own hairline goes to full contrast, so the indicator survives losing half of itself. Widened to 3px to match `brand.css`'s input ring | §7.7.4 |
| 6 | **A ground block out-specified `:focus-within` and silently disabled it, and only reading the painted layer back could see it.** `body:has(> .gate) .language-bar` is 0-2-1 and `.language-bar:focus-within` is 0-2-0, so a ground that assigned `--lang-hairline` itself won: the ring's inner line stayed at its resting 0.28 translucency on the ground where the ring matters most, and `:hover` did nothing at all. A ground names three variants (`-rest`, `-hover`, `-focus`); only the state rules assign the value the capsule paints with. The hairline is a layer inside a `box-shadow`, so nothing shorter than splitting that shadow on the focused element and reading its **alpha** would have found it | §7.7.4 |
| 7 | **No new interface string, and no markup change.** `brand/_language_chooser.html` and `brand/base.html` are untouched: the skin is derived from the shell the page already renders, so the partial both surfaces `{% include %}` is not forked and its opening tag — asserted verbatim by `tests/admin/test_i18n_pages.py` — still reads exactly as v1.34 left it. All twenty calculator catalogues and the panel's Chinese one are untouched | §7.7.1, §7.7.4 |
| 8 | **Six browser tests, and one of them is photographic on purpose.** A computed `backgroundColor` on `<body>` passes against a Kale body with a white strip still spanning the top of it, so a square beside the capsule and a square from the middle of the field are photographed and compared as bytes: one ground or two. The others measure the shell ending at the fold; the capsule's four colour pairs on Kale **and** that its surface differs from the field, which is the assertion a capsule painted the colour of the ground would otherwise pass; the ring against the ground it is offset onto; the fall back to the light skin when the one class the ground is derived from is taken out of the page, which is the condition every Tabler screen is in; and that the island **mirrors** rather than merely moves under `dir="rtl"`. Ten mutants applied, nine killed by their intended test — `align-self: flex-start` survives its deletion on the gate as it did before, because `width: fit-content` in the same rule holds the capsule at 416px alone, and the anchored pattern in `tests/admin/test_i18n_pages.py` stays the only guard | §7.7.4 |
| 9 | **`language.css` now has the physical-direction test its own header has claimed since it was written.** `styles.css` has had one since Arabic shipped; the panel's stylesheet asserted "LOGICAL PROPERTIES THROUGHOUT" in a comment and nothing enforced it, on the file that is the reason the calculator's rule exists. Both copies forbid the physical **properties** and neither can see a four-value **shorthand** — `margin: 14px 20px 14px 60px` pins the island physically left and is spelled `margin` — which is why the browser-side RTL test measures the gap from the reading edge in both directions instead. Neither is sufficient alone, and that is now written down in both | §7.7.4 |

### v1.34 — 2026-08-15 (the language chooser becomes one component on both surfaces; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **The chooser is one raised capsule on both surfaces, and the `<select>` inside it has no box of its own.** It was a bare `LANGUAGE` label beside a bordered `<select>` on the calculator, which reads as a form control somebody forgot to remove, and three unstyled platform controls on the panel. It is now a white capsule with a 1px hairline, a two-layer shadow, a 12px radius and an 18px globe at its inline-start; the border, the radius and the shadow belong to the **component**, and the select keeps its behaviour and loses its frame. **Nothing about the mechanism moves**: §7.7.4's asymmetry stands unchanged — the panel's is still a real `<form method="post">` with a visible submit button and no `onchange`, the calculator's is still built by `web/js/i18n.js`, and neither is converted to the other's shape | §7.7.4 |
| 2 | **The hairline is an inset `box-shadow`, not a `border`, and that is arithmetic rather than taste.** The `<select>` is 44px because 44px is the touch target this calculator uses and `tests/web/test_i18n_browser.py` measures it; a 1px border round it makes the capsule 46px. §7.7.4's header budget is a **measured** one — a separate strip for this control cost 57px and was removed — so the treatment is required to cost nothing, and an inset shadow draws the same line and occupies no layout. Measured after: **92px header at 1278×983 and at 938×898, 126px wrapped at 390×700** — the same three numbers as before it, and `test_a_short_step_is_not_floored_by_a_stale_min_height` still passes | §7.7.4, §7.6.3 |
| 3 | **The globe is inline SVG, drawn on both surfaces rather than fetched, and `aria-hidden`.** §7.6 rule 7 forbids a runtime asset from a third-party host and the public policy is `img-src 'self' data:`; there is no icon font and no build step, so a drawn icon is the only kind either surface can have. It is silent to a screen reader because the `<label>` beside it already names the control — announcing "globe, Language" is one thing said twice. **Below 720px, where the label is `.sr-only`, it is the only thing on screen that says what the control is for**, which is what it is there for as much as decoration | §7.6, §7.7.4 |
| 4 | **RTL comes from `dir` alone and nothing was added to make it.** The globe sits at the capsule's `padding-inline-start` and the `<select>`'s arrow is the browser's own, drawn at the select's inline-end, so the two land on opposite sides in both directions and the arrow is deliberately **not** replaced with a drawn one. Measured in Arabic and Urdu in a browser, not inferred | §7.7.4 |
| 5 | **The panel's chooser stops being a full-bleed strip and becomes a floating capsule — v1.27 item 9's "the panel keeps a row of its own" is amended.** That reading produced a white band with a bottom rule across the top of every screen, which is chrome the panel had grown to hold one small control. The reason the sentence gave still holds: the panel has no above-the-fold budget and the capsule is not squeezed into an existing row the way the calculator's is. It floats on the page's own ground at the top inline-start instead | §7.7.4 |
| 6 | **`admin/static/language.css` is new, and the defect it closes is that the chooser had rules the panel proper never got.** `brand.css` is linked from `brand/base.html`, which is the five gate pages; the panel proper is sqladmin's Tabler layout, whose `<head>` is in a template this project does not fork. So every screen a signed-in person actually uses rendered the chooser as raw platform controls while the login page they had just left was styled. The fix reaches Tabler rather than forking it — `{% block head %}` is declared in sqladmin's own base and overridden by nobody, so `sqladmin/layout.html` claims it — and **one file serves both skins**, which is what stops them drifting. Loading `brand.css` into Tabler was refused: it styles `body`, `h1`, `input`, `button` and `table` globally | §7.7.4, §8 |
| 7 | **No new interface string.** The globe is decoration with an `aria-hidden`, so all twenty calculator catalogues and the panel's Chinese one are untouched at 309 entries: zero missing, zero orphaned | §7.7.1 |
| 8 | **Two defects found by measuring rather than by reading.** `max-width: min(100%, 22ch)` never clamped the select's max-content **contribution** — a percentage inside `min()` is indefinite while a flex container is sized from its contents — so the capsule shrink-wrapped to **396px around a 183px control**, 83px of it empty. Invisible while the select carried the only border; a visible hole the moment the box moved outwards. An explicit `width` fixes it at 316px, and the three content pages' four-link header stops wrapping onto a second line as a result — their row measures 92px at 1278×983, the same as the calculator's. Separately, `.intro-header`'s rule tinting the label white for the Kale ground had to be **deleted**: the label is inside a white capsule now, so it would have painted white on white — present, still the control's accessible name, invisible to everyone who can see | §7.7.4 |

### v1.33 — 2026-08-15 (step 3 offers containers; the seed becomes New Zealand's actual bin sizes; affects B, C and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **The container input is built, and §7.2's standing requirement is met.** `taxonomy.unit_presets` was served, `units.js::toKg` was written for exactly this, the seed had the rows, and **nothing called any of it** — step 3 offered kilograms and tonnes and no way to say "two 240 L wheelie bins". That is the client's stated case: a café or a school does not know its kilograms, it knows it fills two bins a week, and making it guess a weight is how a made-up number gets into a public statistic (§7.6.1). **The containers are an `<optgroup>` on the unit `<select>` step 3 already had**, valued `preset:<code>` to keep them apart from `kilograms` and `tonnes` in one value space; choosing one switches the amount field from a mass to a count and reveals a running kilogram total | §7.2, §7.3 |
| 1a | **Why it is one control and not two, which is a measured decision rather than a taste.** The first build put a "By weight / By container" radio pair above the form. It cost **169px at 1278x983 on a step that had 50px of headroom**, pushed the document to 1102px in a 983px viewport, and broke three of `tests/web/test_step_navigation.py`'s assertions at once — §7.6.3's rule that advancing must never require scrolling, which this project measures rather than assumes. Folding the containers into the control that was already asking the question costs nothing, and "1,200 kilograms" and "2 × 240 L wheelie bin" are one question with one answer in any case. **Do not reinstate a separate mode control**; it takes back exactly the space this arrangement exists to keep | §7.6.3 |
| 2 | **`state` gains `measureMode`, `unitPreset` and `unitCount`** — the shape §7.2 named as absent. `unitCount` holds the raw string the visitor typed, for the same reason `qtyInput` does: nothing rounds until `units.js` converts it | §7.2 |
| 3 | **A container entry allocates in kilograms, and `measureMode: 'container'` therefore implies `totalUnit: 'kilograms'`.** A container estimates the *total*; the total is a mass. Step 4 splits it across destinations, §6.2's mass-conservation rule compares the two, and both have to be in one unit — "0.37 wheelie bins to landfill" is not enterable or checkable. **The consequence that matters: a container entry reaches the API as exactly the `qty_kg` a visitor who had typed the kilograms would have sent.** That is the property that makes the input safe to add at all, and it is asserted request-body against request-body in a browser | §6.2, §7.2, §7.3 |
| 4 | **The two-decimal rule follows the count, not the derived total** — §7.2 recorded this collision as the decision building this input would require, and this is the decision. Two decimals is an input rule about *typing*; `toKg` returns three because §6.2 refuses a fourth, and "139.200" is not a number anybody enters. **The count is additionally bounded at 10,000 containers**: half a wheelie bin is a reasonable thing to say and two hundred and forty thousand of them is not, and 10,000 of the largest preset is 3,190 t — inside §6.2's 10,000,000 kg per-line ceiling, so the bound a visitor meets is expressed in containers rather than in kilograms | §6.2, §7.2, §7.3 |
| 5 | **`toKg` multiplies in decimal, not in double, and this was wrong in the third decimal place.** Both operands are decimals — the count is typed and `kg_per_unit` crosses the wire as a string precisely so `Number` never sees it (§1.2) — and the old body was `Number(a) * Number(b)` then `.toFixed(3)`. A quarter of the seeded 23 L food scraps bin is `0.25 × 6.6700 = 1.6675 kg` exactly; the nearest double to `6.67` is below it, so `toFixed` reads the exact tie as under the half and answers `"1.667"`. It is now integer arithmetic rounded half up, and asserted against `decimal.Decimal` | §7.3 |
| 6 | **`toKg` refuses a negative operand.** `unit_preset` carries a `kg_per_unit >= 0` CHECK written for this multiplication; the *count* is the operand no database constraint can reach, and `<input type="number">` hands over `"-2"` quite happily. Found by the test, not by review | §2.1, §7.3 |
| 7 | **`units.js` gains `containerKg(entry, presets)` and `entryTotal(entry, presets)`.** `entryTotal` is the one place the two measurement modes reconcile. It is in `units.js` rather than in `calculator.js` because the **results export needs it too and cannot import that module** — it read `entry.totalAmount` directly, which is empty for a container entry, so a downloaded report said "0.00 kilograms" for an entry whose screen said 139.200 kg | §7.3, §7.3a |
| 8 | **`GET /api/v1/taxonomy` orders `unit_presets` by `kg_per_unit`, smallest first**, and the form renders that order as given. This is the one taxonomy table with no `sort_order`; alphabetically by `code` the 1100 L front-loader sorted above the 660 L one and the 140 L kerbside bin above the 80 L one. No schema change buys this — the column already carries the order | §6.1 |
| 9 | **A preset naming a `food_category` is offered only once that category is chosen**, which is what §2.1's nullable column is for: a bin of bread and a bin of potatoes do not weigh the same. NULL means "every category" and always shows. A selection that leaves the list when step 2 changes is **cleared from state with it** — a conversion nobody can see is a conversion nobody can check | §2.1, §6.1, §7.2 |
| 10 | **A preset's `label` is not translated.** §7.7.7's ruling of 14 August applies unchanged: it is staff-typed, so it is published exactly as written, and a Thai visitor gets a Thai form listing English container names. The `<optgroup>` labels around them — "Weight", "Containers" — are interface strings and are translated, which is the boundary drawn on one control. Eight new keys across twenty catalogues, four retired; 309 per catalogue | §7.7.7 |
| 11 | **The seeded `unit_preset` rows become New Zealand's actual containers** — ten of them, replacing five. Added: the **23 L kerbside food scraps bin** most of urban Auckland was issued from March 2023, the **80 L and 140 L** kerbside bins councils issue instead of the 120 L, and the **660 L and 1100 L front-loader** bins that are the standard commercial sizes here. **The density is sourced and is still not measured**: 0.29 kg/L from the Food Loss & Waste Protocol's *Guidance on FLW Quantification Methods*, Table 3.2, where it appears twice independently (household food waste in a small container, WRAP 2010; commerce-and-industry animal and vegetable wastes, Jacobs Engineering UK 2010). It is not a New Zealand figure, none was found, and MfE's *Solid Waste Analysis Protocol* publishes none and warns against volume-based estimation for exactly this reason. Every row's `source_note` says all of that. **O-6 stays open** — a sourced placeholder is not measured data | §2.1 |
| 12 | **Migration `0015` is data-only** and exists because `admin.seed._ensure` creates a row and never updates one, so a seed edit alone reaches a fresh database and leaves every deployed one on the old numbers. It rewrites only rows still carrying the untouched placeholder `source_note`, so a staff correction is never clobbered, and it writes no `audit_log` row — a migration has no actor to record | §2.1, §2.4 |

| 13 | **`tests/web/test_i18n_browser.py` now reads `KAICALC_WEB_URL`** like every other browser module. It alone hard-coded `:18080`, so `test_every_catalogue_is_actually_in_the_built_image` — whose whole subject is whether the *current* checkout's catalogues shipped — measured whichever stack held that port, and could not be pointed at the one under test. It reported a stale image correctly and about the wrong stack | §7.7.1 |

> **Still open after this revision.** **O-6** is not closed and this revision does not claim to close it: the container *sizes* are now real and traceable, and the *density* converting them to kilograms is a published overseas factor standing in for a New Zealand measurement nobody has taken. Read the `source_note` on any row before quoting a figure from it. **O-1** is unchanged.

### v1.32 — 2026-08-15 (the placeholder-data flag stops being a tick and starts being two actions; affects B and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **`is_mock` comes off `FactorSetAdmin`'s edit form and moves to two actions, and the two directions are not symmetric.** Adding the warning is allowed in every status, from either role, with no proof — the safe direction has to be instant, so that anybody who doubts a published set can put the disclaimer in front of the public at once. Removing it takes a **press-then-prove** confirmation, the same one creating an account, deleting one and revealing an unclaimed password already take. A checkbox on a generic edit form could carry neither: the form has nowhere to ask, and a tick is something a staff member changing a version label can take off by accident | §2.2, §8.2 |
| 2 | **Status no longer gates the flag, and that is a reversal.** The panel refused it on a published or archived set and told staff to clone first. The workflow it refused is the real one — publish the real factors, let them run publicly for a day or two to verify them, clear the flag then — and the clone it forced creates a `factor_set` row and a new version label for a change in which **not one factor value differs**, while every `submission` recorded meanwhile stamps the old id. That is a version discontinuity manufactured by the workflow rather than by the data. A published set's **other** fields are still refused in place; only this one field moved, and it moved off the form rather than into it | §2.2 |
| 3 | **A draft takes the same proof as a published set.** Proportionality argues the other way — a draft has no public consequence — but `publish` takes no proof (§8.3 keeps it open to both roles deliberately), so a gate applied only to published sets has a one-button way round it: clear it on the draft, then publish. The cost of closing that is one password on an operation each factor set sees once in its life | §2.2, §8.3 |
| 4 | **A new `audit_log.action`: `clear_placeholder`, with `flag_placeholder` for the other direction**, each carrying `before` and `after`. The change used to land as an ordinary `update`, which in the trail is indistinguishable from somebody fixing a typo in the same set's notes — on the one entry that answers "when did the public warning come off, and who took it off" | §2.2, §5.5 |
| 5 | **`is_mock` is off the *create* form too**, so a set cannot be created unflagged. "Nothing is published as real data by omission" was resting on the creator leaving a ticked box alone; it is now structural | §2.2, §8.1 |
| 6 | **§5.2's bundle cache re-reads `is_mock` on every hit, and nothing else.** Found while writing item 1: the flag rides on the cached published bundle, `invalidate_factor_bundle` is a dict in one process, and `docker/compose.yaml` runs the panel and the API as **two services** — so the flag could be changed and the API would serve the old value until it was restarted. `/factors` reads the row live and `/calculate` did not, which is the worst version: the placeholder disclaimer present on one public surface and absent on the other. The cost is one boolean `SELECT` per calculation, against a bundle of some 900 rows | §5.2, §2.2 |
| 7 | **A test that was passing on the wrong mechanism.** `test_editing_a_published_sets_is_mock_is_refused` submitted **no field change at all** — `version_label` unchanged and `notes=""` against a NULL — and passed because the *omitted* `is_mock` checkbox was itself what made the row dirty. Taking the field off the form made that POST a no-op, the audit listener returns before calling `validate_before_commit` when nothing is dirty, and the assertion silently became "nothing happened". It now edits `notes`, which is what it always meant to test | §2.2 |

> **Still open after this revision.** **O-1** remains the hard blocker: no real emissions factors have been supplied, so the published set stays flagged and the banner stays mandatory. Nothing here makes a placeholder set look real — it changes who has to prove they meant it, and what the trail says afterwards.

### v1.31 — 2026-08-15 (`factor_downstream` gains a sector, and the ReFED fixture is rebuilt into ReFED's own shape; affects A, B, C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **`factor_downstream.sector_id` — INT, FK, NULL, where NULL means "applies to every sector for this destination".** Structurally identical to `food_category_id` beside it. The table could not see which stage of the supply chain the waste arose at, and §10.3's ReFED fixture absorbed that by folding the stage into the *food category's code*: one sector row and 39 categories named `refed_farm_dry_goods`, `refed_foodservice_frozen`. v1.19 recorded that as lossless and said this note was "the first thing to check if a downstream factor ever does need to vary by sector". It did — and the cost was never in the arithmetic, it was in the product: the calculator's first step offered **one** radio button that selected itself, its second listed 39 compound entries the client read as stages rather than foods, and §5.4's `by_sector` chart was a single 100% bucket carrying no information at all | §2.2, §4.1, §10.2, §10.3 |
| 2 | **The lookup is now two-dimensional, and the precedence is a decision rather than a derivation. Exact `(sector, food_category)` → `(sector, NULL)` → `(NULL, food_category)` → `(NULL, NULL)` → zero.** Steps 2 and 3 both name exactly one dimension, so specificity cannot separate them and one had to be chosen. **The sector wins**; §2.2 carries the three reasons in full. A wrong choice here returns a plausible number, not an error, which is why all sixteen subsets of the four candidate rows are asserted **by value** in `tests/test_bundle.py` rather than sampled | §2.2, §4.1 |
| 3 | **`FactorBundle.downstream()` takes the sector: `downstream(destination, sector, food_cat, metric)`.** The new argument is positional and in the middle, deliberately — a call site left at three arguments is a `TypeError` at import-test time rather than a silent miss. `bundle.downstream_factors` is keyed `(destination, sector \| None, food_category \| None, metric)` | §4.1 |
| 4 | **`downstream[].sector` is a required key whose value may be `null`**, on exactly the terms `downstream[].food_category` and `upstream[].destination` already have. A missing key is a malformed row, because a row that had silently lost it would load as the every-sector row and price every stage of the supply chain the same — a plausible answer rather than an exception | §10.2 |
| 5 | **§6.3's factor export carries `sector` on every downstream row**, present-and-null rather than omitted, for the same reason it carries `source_note` that way: so a consumer can tell "this row applies to every sector" from "this endpoint does not report the sector" | §6.3 |
| 6 | **§5.1's coverage filter: a sector is covered if it appears in `factor_upstream.sector_id` *or* as a non-NULL `factor_downstream.sector_id`.** Not redundant with the upstream read — a set may legitimately price a stage downstream only, a per-tonne disposal charge that differs by collection contract with no upstream footprint of its own, and reading one table would drop that sector from the form while the rows pricing it sat in the database. Mirrors what v1.21 already does for destinations across the two tables | §5.1 |
| 7 | **The ReFED comparison fixture is rebuilt: 5 sectors × 9 food categories, not 1 × 39.** `refed_farm`, `refed_retail`, …; `refed_produce`, `refed_dry_goods`, …. **The numbers did not move.** `python -m pytest tests/benchmark/refed/ -q` agrees with ReFED's separately published totals to ~1e-9 before and after, and two scenarios run by hand on ReFED's live calculator are now tests: Farm / Standard Mix over six destinations, and Retail / Standard Mix over two | §10.3 |
| 8 | **Every downstream row in that fixture states both dimensions; none is left NULL**, even for the 20 of 102 groups whose value does not vary by sector. ReFED publishes only **39 of the 45** (sector, food type) pairs — Farm has Dry Goods, Produce and Standard Mix and nothing else — and a 5 × 9 taxonomy offers all 45, so a NULL-sector row would answer a Farm / Frozen lookup with Retail's number. With every row explicit those six pairs price at **zero on every metric**, which is visibly nothing rather than plausibly wrong. Asserted for all six | §10.3 |
| 9 | **The New Zealand set is unchanged and that is evidence, not an assumption.** Its downstream factors do not vary by sector, so all fifteen rows take NULL. In the golden suite the rebuild touched **only `bundle.json`** — not one `expected.json` and not one `request.json` — and all nine cases pass | §10.1, §2.2 |
| 10 | **Migration `0014`.** Nullable column, no backfill: an existing row's NULL says "every sector", which is what that row already said by having no opinion. `uq_factor_downstream` becomes five columns and `uq_factor_downstream_generic` COALESCEs **both** nullable columns — collapsing only the new one would make duplicate `food_category IS NULL` rows legal again and quietly undo what 0005 created that index for | §2.2 |
| 11 | **The documentation page's downstream table gains a `Sector` column**, reading `All sectors` where the row applies to every one, and the sentence above it states §4.1's order. Omitting the column would print the ReFED set's 1,728 rows with five of them identical in every visible field and different only in the number. **One new key (`All sectors`) and one reworded sentence**, in all twenty catalogues — 301 entries each, zero missing, zero orphaned. The admin panel's two optional downstream fields carry the same rule, and the Chinese description of the food-category field is retranslated rather than left: it stated the precedence for a lookup that no longer exists, which reads as current and is worse than an untranslated one | §7.6, §7.7, §8.1 |

### v1.30 — 2026-08-15 (the three content pages are translated, and charts get a translation path of their own; affects C and D)

| # | Change | Section |
| --- | --- | --- |
| 1 | **v1.29 item 9 is closed and O-11 with it.** The statistics, home and documentation pages are translated into all twenty languages. **100 new keys, 300 per catalogue**, against the 201 v1.29 left standing — fewer than the 120–130 that row estimated, and the difference is not work skipped: the estimate counted strings per page, and the four navigation links, the footer sentence, the logo alt text and fifteen table column headers are each **one** key shared across pages and sections. One key was retired (see item 4). Every catalogue is again exactly complete: zero missing, zero orphaned | §7.7, O-11 |
| 2 | **A chart is not reachable by `applyToDocument()`, and that is a second translation path rather than a gap in the first.** A Chart.js title (`plugins.title.text`) and a canvas `aria-label` are arguments to a constructor: once the chart exists the title is painted onto a bitmap and no DOM walk can find it or repaint it. **Changing language therefore destroys every chart on the statistics page and builds it again**, through the ordinary render path, from the response already held — `stats.js` does not re-fetch, because the figures do not depend on the language and a page that calls the service on every language change rate-limits itself. `BREAKDOWNS` holds **functions, not strings**, for the same reason: a constant is read once at module evaluation and stays in the language the page opened in. Asserted by reading the title off the live chart's laid-out title block in two languages, with the first canvases proved destroyed rather than left behind the new ones | §7.4, §7.7 |
| 3 | **The key extractor lost every marked element nested inside another marked element, and the public navigation is exactly that shape.** `html_keys()` matched `<(\w+)([^>]*\bdata-i18n\b[^>]*)>(.*?)</\1>`, which matches a `<nav data-i18n-attr="aria-label">` — `\b` is satisfied by `data-i18n-attr` — and consumes everything through `</nav>`, swallowing the four `<a data-i18n>` links inside it on all three pages. **Nothing would have failed:** a key nobody extracts is a key no coverage test can ask for, and translating it fails the stale-key test instead, so the defect defends itself. It is an `html.parser` walk now, and a **new rule** comes with it: **a `data-i18n` element may not contain element children**, because `applyToDocument` assigns `textContent` and would delete them on the first language change. The footer's transparency line is written to that rule — the sentence in its own `<span>` beside the link, never a marker on the paragraph holding both | §7.7.1 |
| 4 | **The documentation page's `<title>` is "Documentation \| …", settling the name v1.29 left open.** The navigation calls the page Documentation on all four pages, and the other two titles are the navigation label followed by the site name. `Methodology \| …` is retired from twenty catalogues; the `<h1>` still reads "Methodology and published factors", which is what the page *is* rather than what it is called | §7.6 |
| 5 | **The two footers that differed by one word are now one string**, and `stats.html` and `home.html` gain the `<noscript>` note the calculator and the documentation page already carry. A near-duplicate is worse than a duplicate: a translator meets it twice and a reworded copy orphans only one of them | §7.6, §7.7.1 |
| 6 | **A translation may be character-identical to its English source, and it must be declared.** `test_no_entry_is_blank_or_still_english` exists because a key carrying its own English value looks translated to every other test. But `Code` in French, `Name` in German and `Sector` in Dutch **are** the natural words, and forcing a synonym to satisfy a test would make the interface worse to read in exchange for a greener suite. So a small per-language allowlist carries them, and a second test fails on any entry in it that is **not** actually identical, so it can only be added deliberately and cannot outlive its reason | §7.7.1 |
| 7 | **What these pages do not translate, stated so it is not read as an omission.** Statistics bucket labels are the API's `label` — destination, sector and food-category names staff typed into the panel, published exactly as written (§7.7.7), so a chart legend is identical in every language and a test asserts that it is. News post titles, excerpts and dates are the client's published words on their own site. Every figure — axis ticks, counts, shares, masses — stays pinned to `en-NZ`; read in Arabic, where `toLocaleString` would otherwise produce Eastern Arabic numerals, and asserted there. Date and number **formats** remain O-4, which promises nothing | §7.7.7, O-4 |
| 8 | **Chinese is still the one unflagged catalogue, and it is still unread.** Nineteen languages carry the machine-translation notice and Chinese does not, because it has speakers on this project using the panel daily — that is the reason for the flag's value, not a claim that these 99 entries have been reviewed line by line. They have not been. §7.7 records it as outstanding rather than delivered | §7.7, O-8 |

### v1.29 — 2026-08-14 (D's statistics, home and documentation pages land; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **§7.4's `charts.js` is now a description rather than a specification, and it gains two options.** `renderDonut` and `renderBar` both take `formatValue`, a function applied to the y-axis ticks **and** the tooltip together. The axis read `0`, `0.05` … `0.40` immediately above a text list reading `37.9% share` — one number in two units on one card — and formatting one without the other only moves the contradiction into the hover. Both also carry a plugin that sizes the chart's container to the legend Chart.js actually laid out; see item 2 | §7.4 |
| 2 | **A chart's container must fit its legend, and this is a front-end hard constraint rather than a styling note.** With `maintainAspectRatio: false` the canvas is exactly its container, so a fixed container height is a fixed drawing surface. Measured at 390px with the canonical ten-bucket `by_destination` fixture, `legend.legendHitBoxes[9]` sat at `top: 324` on a 320px surface — the tenth entry drawn outside the canvas, invisible, with no scrollbar. **The entry that falls off is always the last one, and the last one is always `other`**, which §6.4 already says can be the largest bucket in a young data set. A per-row height constant is not sufficient: legend height depends on width and on the language, and an Arabic legend wraps differently from an English one. Read the measured `legend.height` | §7.6, §7.4 |
| 3 | **Charts take the brand palette, and it is long enough for the taxonomy.** The palette indexed `PALETTE[index % PALETTE.length]` over nine colours, so bucket ten drew bucket one's colour — measured, `Landfill` and `Other (sample too small)` were the same `#005f73` in one doughnut, which is the default state because the taxonomy defines fourteen destinations and `other` sits alongside them. None of the nine was a brand colour. **The palette is now sixteen entries, each a brand colour or a mix of one with White or Kale**, and its length is pinned against `tests/fixtures/taxonomy.json` so the wrap is unreachable rather than merely further away. Each entry carries the ink the brand's dark-ground/light-ground rule assigns it, and the ink is used: a tooltip is painted on its own segment's fill | §7.4, §7.6 |
| 4 | **§7.7.7's rule about decimals is restated as binding on `total_kg` in the statistics list, because that was the one public surface still breaking it.** `toLocaleString('en-NZ', {maximumFractionDigits: 3})` grouped (`521952.691` → `521,952.691 kg`) and round-tripped through `Number`, dropping the significant trailing zero the service published (`139132.500` → `139,132.5`). §1.2 sends decimals as strings precisely so the front end need not do either: print the string | §7.7.7, §1.2 |
| 5 | **The front end may not render a factor set's `id`, and the documentation page's field list is now exactly §6.3's four fields.** `METADATA_FIELDS` was written to print `factor_set.id`; §6.3's projection carries four keys, so `Object.hasOwn` filtered it and the page looked correct — and would have begun publishing a primary key the day B added `id` to the projection, with nothing failing. §1.1 makes `code` the cross-layer identifier. **A projection is not a permission**: rendering whatever the response happens to carry is publishing whatever the response happens to carry | §6.3, §1.1 |
| 6 | **A public-page Content-Security-Policy is added, scoped to nginx's static `location /`.** The API and the panel do not inherit it. Two consequences are recorded rather than discovered. **`img-src 'self' data:` will refuse a news card image**: `news.js` normalises `imageUrl` from `wp:featuredmedia` and nothing renders it, so the field is dead today; the directive is deliberately **not** widened, because the media origin is a guess until somebody looks and a policy widened to the wrong host is looser and still broken. And **`style-src 'self'` refuses Playwright's `add_style_tag`**, which is how this suite injects its measurement and mutation stylesheets — every browser file that measures layout sets `bypass_csp`, and one file runs with the policy enforced so a directive that breaks a real page still fails something | §7.6 |
| 7 | **The calculator page carries no four-link public navigation, and the three content pages do.** §7.7.4 admitted the language chooser to the calculator's header row as a measured decision; a third block does not fit. Measured: chooser 364px, brand 330px, navigation 355px, against a row content box of 1209px at 1278×983 and **887px at 938×898**. In the footer it does not fit either — the shortest step measures 983px in a 983px viewport, so the page's whole slack is 32px and a navigation block meeting the 44px touch target needs 44. **There is no link from the calculator to Home or Statistics**, which is a real gap and is recorded as O-10 rather than closed by shaving a touch target | §7.6, O-10 |
| 8 | **§6.4's copy constraint gets a definition it can be tested against.** "Never *Distribution of food waste destinations in New Zealand*" had been implemented as a guard on that one phrase order, which "Food waste destinations across New Zealand", "Where New Zealand's food waste goes", "New Zealand food waste by destination" and "Total food waste in New Zealand: 884,200 tonnes" all pass. The rule as it now reads: **on the statistics page, no sentence may name New Zealand within three words of a food-waste quantity or distribution term unless the sentence negates before the mention.** The page's one legitimate mention is such a negation | §6.4 |
| 9 | **Interface translation of the three new pages is explicitly deferred, and this row is the record that it is outstanding.** They ship in English. `methodology.html` keeps its `data-i18n` markers and its chooser on every string it already had, so all twenty-one catalogues remain exactly complete — zero orphaned keys and zero missing keys — but roughly 120–130 new strings across the three pages are unmarked. See O-11 | §7.7, O-11 |

### v1.28 — 2026-08-14 (two pre-existing defects closed: the panel's document language, and a page that scrolled sideways; affects D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **v1.27 item 14 is closed. The panel proper now announces the language it rendered**, and §7.7.8 states the rule instead of recording the defect. The mechanism is a Jinja extension that rewrites sqladmin's own `<html lang="en">` as its template compiles — **not a second vendored template**, which was the option this project could see and had already been burned by: the one file it does vendor went stale across 0.30.0 → 0.31.0 and suppressed the upstream markup silently. Nothing is copied, so an upgrade is picked up in full, and the rewrite carries the same drift guard with the same version-skew wording | §7.7.8 |
| 2 | **`lang` is the language RENDERED, not the language chosen — written down because the two differ and the wrong one is the tempting one.** A choice the surface has no catalogue for renders English, so `lang` says English. This is the same requirement the surface asymmetry in §7.7.5 already implies; it had never been stated as a rule about the attribute | §7.7.8 |
| 3 | **`dir` is emitted on the panel too, and it is stated in terms that it is unverified there.** It follows the catalogue rather than a constant, so it cannot be wrong for the first RTL catalogue that arrives; an absent `dir` asserts left-to-right by omission. **No claim is made that the panel's layout mirrors** — there is no RTL panel catalogue to look at one on. The calculator's RTL layout is exercised separately, in Arabic and Urdu | §7.7.8 |
| 4 | **The methodology page's sideways scroll is fixed, and the report of it is corrected.** It was recorded as "in Arabic at 390px". Measured, it overflowed by **485px in Arabic and 480px in English** — never an RTL defect. Nor were the formulas table and the definition list both at fault: the table is inside an `overflow-x: auto` container and was always clipped. The cause was one `<dd>` in the factor-set summary rendering the published set's **notes**, which cite a 124-character URL; a URL has no space, so its min-content width is its full width, and a flex item's default `min-width: auto` forbids shrinking below that. **This is data-driven, so it is not specific to the ReFED set** — any notes field with a long token reaches it. Fixed on the shared `.review-destinations` rule with `overflow-wrap: anywhere` on the value — **one property, established by rebuilding with each candidate on its own**: `anywhere` alone is sufficient because it also shrinks the min-content contribution, while `min-width: 0` alone is not. Direction-neutral. **The label deliberately does not get it:** applied to the `<dt>` as well, Arabic `ملاحظات` collapsed to a 1px column 760px tall, one letter per line, while every overflow assertion still passed | §7.6 |
| 5 | **Where a layout claim has to come from.** The overflow was recorded for a day against a reading of the stylesheet and got both the language and the culprit wrong. `scrollWidth` against `clientWidth` in a real browser at a real width is the only thing that settles one, and the regression test additionally asserts the page still carries a token long enough to reproduce the defect — otherwise publishing a factor set with short notes turns it green on a page that could never have overflowed | §7.6 |

> **On version numbers.** Two lines of this document ran in parallel from 2026-08-07 to 2026-08-09: v0.10–v0.13 on `admin_panel`, and v1.0–v1.1 on `docs/contract-v1.0`. They were merged as v1.2. Entries below appear in the order they were merged, not in numeric order, and both sequences are real — a reference to "v0.13 §8.3" and one to "v1.1 §2.2" both resolve here.

### v1.27 — 2026-08-14 (a language chooser on both surfaces, defaulting to the browser; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **v1.25 item 1 and v1.26 item 7 are amended: there is a language chooser on both surfaces, and one cookie is stored.** Negotiation was the only way to get a language, and a browser's setting is not always what a person wants an interface in — a Chinese-speaking visitor may deliberately prefer the English original and had no way to say so. **Negotiation becomes the default rather than the only behaviour.** The chooser sits at the **top inline-start** of every page; its first option is "Follow the system", which negotiates exactly as v1.26 specifies, and the rest select a language outright. **The v1.26 negotiation rule itself is untouched** — highest-priority tag only, English if it has no catalogue | §7.7, O-8 |
| 2 | **`## 7.7 Interface Translation` is new, and it is where the dangling references land.** v1.24–v1.26 cite `§7`, `§7.6` and `§2.1` for translation behaviour, and until now none of those sections said anything about it: the only normative i18n text in this document was the change log. §7.7 carries the catalogue shape, the resolution order, the storage rule, the chooser, the surface asymmetry, `Vary`, what is never translated, and one known defect | §7.7 |
| 3 | **The stored value is `kaicalc_lang`: path `/`, `SameSite=Lax`, one year, not `HttpOnly`, not `Secure`, value `auto` or a language code.** A cookie rather than `localStorage` — and the usual reason is wrong, because both surfaces are the same origin and `localStorage` would be shared too. The decisive reason is that **the panel renders server-side and must know the language before it emits HTML.** Not `HttpOnly`, because the calculator's JavaScript reads and writes the same cookie; that one cookie is what keeps this a single choice instead of two that drift | §7.7.3, O-8 |
| 4 | **Why §2.3 permits it, written as the reason rather than as reassurance, because it has already been misread once in the other direction.** Two properties, and **both are needed**: it records something the visitor **deliberately declared** rather than something inferred from their browser; and its **value space is closed, tiny and free of entropy** — twenty-two values in total, being twenty-one languages and `auto`, shared identically by everyone who picks the same one. The second is load-bearing. "The person declared it" alone would equally justify storing a name typed into a form, which would be a fingerprint by any measure — **it is the absence of entropy, not the presence of consent, that makes this incapable of identifying anyone.** Written down so the next reader neither deletes the cookie as a violation nor adds a genuinely identifying field believing consent licenses it | §2.3, §7.7.3 |
| 5 | **"Follow the system" is a stored value — the literal `auto` — not an absent cookie.** Otherwise "chose to follow" and "never chose" are indistinguishable and the chooser cannot show what is in effect. The mechanical reason is stronger: reverting becomes an ordinary write instead of a deletion, and deleting a cookie reliably needs `Max-Age=0` with an exactly matching path and domain — get it wrong and the old value survives, so the chooser appears to revert and snaps back on the next page | §7.7.3 |
| 6 | **Not entangled with `submission.token`.** Different name, lifetime and purpose; it neither extends nor refreshes the token and never reaches `submission`, `audit_log` or the access log. Because path `/` cannot be scoped away when both surfaces need it, the browser also attaches it to `POST /api/v1/calculate` — **the API receives it and ignores it, and a test asserts that** rather than leaving it obvious, the access log having been found writing four §2.3-forbidden fields on 2026-08-12 | §2.3, §7.7.3 |
| 7 | **`?lang=` is unchanged and must not be confused with the chooser.** Still a one-request override that persists nothing, still emitted by no control, still ignored when unrecognised. A support link must not silently re-language its recipient for good. The two share no mechanism: the chooser is a form post on the panel and a `<select>` writing a cookie on the calculator, and neither touches the URL. Precedence is `?lang=` → cookie → negotiation → English | §7.7.2 |
| 8 | **"It must work without JavaScript" means different things on the two surfaces, and the calculator's version of the rule is not the obvious one.** The panel renders through FastAPI and genuinely works with scripting off, so its chooser is a real `<form method="post">` with a submit button and no `onchange`. **The calculator is ES modules end to end and renders nothing without scripting** — no steps, no taxonomy, no results — so a chooser needing JavaScript adds no degradation it did not already have, and building it in `web/js/i18n.js` means it can never exist as a control that is present and does nothing. A `<noscript>` note names the page, not the language control | §7.7.4 |
| 9 | **"Top left" is written as top inline-start**, so it is top-left in English and top-right in Arabic and Urdu — a control pinned physically left in a mirrored page lands at the reading-*end* of the header. It sits **below** the machine-translation notice, which stays `document.body.firstElementChild`. **On the calculator it is the first item in the header's existing row rather than a strip of its own, and that was forced by measurement:** a separate strip cost **57px on every page**, and this calculator had already deleted an 87px step-indicator band to stop short steps scrolling. `test_a_short_step_is_not_floored_by_a_stale_min_height` failed with the strip and passed with it hidden via `KAICALC_MUTATION_CSS`. The header is 93px around a 67px logo, so a 44px control fits without the row growing; the lockup is untouched, and below 720px the row wraps so the wordmark is never clipped. **The panel keeps a row of its own** — it has no above-the-fold budget to protect. Measured in a browser at 390px and 1280px in both directions, not inferred from `dir` | §7.7.4 |
| 10 | **The machine-translation warning now also marks its own option**, so it can be read before somebody picks rather than only after. The non-dismissible notice is unchanged, and is now **removed when the language changes away** and **re-worded when it changes between two machine-translated languages** — a notice that outlived the language it warned about would be a false statement about a reviewed page. English and Chinese carry neither. `web/locales/index.json` gains `endonym` and `machine_translated` per entry so the chooser can label twenty-one options from the fetch it already makes | §7.7.1, §7.7.4 |
| 11 | **The two surfaces do not ship the same languages, and the panel says so instead of pretending.** The calculator has twenty-one, the panel has two. The panel's chooser **lists only what the panel has** — no dead entries — and when the stored choice names a language it lacks it renders **English** (not the browser's language, which the visitor had already overridden) and **names the missing language in that language's own script.** **The cookie is left untouched**, which is the assertion worth the most: the tempting implementation rewrites it to `auto` and destroys the calculator's language from an unrelated screen | §7.7.5 |
| 12 | **`Vary: Accept-Language, Cookie` on the panel**, still appended rather than assigned. `Cookie` is added because the response now depends on the stored choice, which **overrides** the header and is therefore the more dangerous half to omit. Cost is nil — the panel is authenticated and uncacheable. **The static origin still sets no `Vary`, and that is the payoff of the client-side chooser**: every visitor gets a byte-identical `index.html` and the cookie is read after the response arrives. Server-side negotiation there would have meant a cached copy of the HTML, the stylesheet and both font faces per visitor | §7.7.6 |
| 13 | **Three tests were rewritten rather than deleted**, having been written to make this reversal deliberate: `test_nothing_about_the_negotiation_is_persisted`, `test_no_language_picker_is_rendered_anywhere` and `test_nothing_is_stored_about_the_language`. Each now asserts the **bounded** behaviour — one cookie, one closed value space, a control that is a form rather than a bare `<select>`, and nothing stored until somebody chooses — because "nothing is stored" and "only this is stored" are different claims and the second is the one now being made | §7.7.3 |
| 14 | **(Closed at v1.28 — see above.)** A defect recorded rather than fixed: the panel proper renders `<html lang="en">` whatever language it is in. sqladmin's own layout hardcodes it and the element sits outside every block that layout defines, so closing it means forking a template this panel deliberately does not fork. It pre-dates the chooser and is not caused by it; a Chinese page announced as English is read aloud by a screen reader in English phonetics. `brand/base.html` and the calculator both get it right. A test asserted the defect so it would fail the day it became fixable; it did, and it was replaced by one asserting the fix in two languages | §7.7.8 |

### v1.26 — 2026-08-13 (only the first language is consulted; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **v1.25 item 3's last sentence is withdrawn: the visitor's language list is no longer walked. Only the highest-priority tag is consulted, and if it has no catalogue the answer is English.** v1.25 shipped the opposite — a tag nobody claims resolved to nothing so that the next preference got a turn, and `fr-CA, zh, en` reached Chinese. The repository owner ruled against it, and the reasoning is the part that has to survive, because walking the list is the more obvious behaviour and someone will try to restore it: **a browser's language list does not reliably describe what a person can read.** The first entry is usually deliberate; the second and third are frequently residue — a preinstalled system locale, an input method added once, a setting changed years ago and forgotten. Treating those as a genuine second language means using an unreliable signal to override a reliable fallback. **English is a safe floor for this audience and an unfamiliar language is not**: everyone who reaches this tool reads English, so the worst outcome under this rule is an English page, while the worst outcome under the walk is a page in a language the reader does not have — and with no picker on either surface, cannot navigate out of | O-8, §7 |
| 2 | **Both surfaces, one rule, and that is not a nicety.** `admin/i18n.py::negotiate` (from `Accept-Language`) and `web/js/i18n.js::negotiate` (from `navigator.languages`) implement the same rule, and each carries the reasoning above as a comment. Two surfaces that answered one visitor differently would be the exact defect a shared rule exists to prevent. `negotiate` on the calculator now takes `navigator.languages` alone and returns `match(preferred[0]) ?? 'en'`; a forced `?lang=` is matched by the caller **before** it rather than prepended to the list | O-8, §7 |
| 3 | **What the rule does *not* change: the lookup within that one tag.** v1.25 item 3's RFC 4647 truncation and per-catalogue `tags` claims are untouched — `en-NZ` still reaches English, `zh-CN` and `zh-Hans-CN` still reach Simplified, `zh-TW` still reaches Traditional through `zh-Hant`'s own claim, and `fil` still reaches `tl`. **The walk between tags is gone; the match inside a tag is not.** `de-AT, xx` reaching German is the case that tells the two apart, and it is asserted as such | O-8 |
| 4 | **Ordering happens before the rule, not after.** Quality values still rank the header first, so `zh;q=0.8, en;q=0.9` has `en` at its head and answers English — the single tag consulted is the highest-*priority* one, never the first one written. `q=0` is still an explicit refusal and is dropped, so `en;q=0, zh` has `zh` at its head and answers Chinese | O-8 |
| 5 | **`*` stops being dropped and is ranked like any other tag** (amending v1.25 item 2). Dropping it was right while the list was walked and is wrong now: it would promote the tag behind it into the one slot that decides, so `*, zh` — a header whose first statement is "no preference" — would have answered Chinese. Ranked, no catalogue claims `*` and it has no subtag to drop, so **`*` at the head means English**, while `zh, *;q=0.5` still means Chinese. A malformed entry is still dropped rather than defaulted, and a dropped entry holds no rank — there is no quality to rank it by, which is why it was dropped — so `en;q=high, zh` answers Chinese and a header with nothing readable in it answers English, as do an empty header and an absent one | O-8 |
| 6 | **`?lang=` stays outside the rule** (amending v1.25 item 4). It is somebody typing a language on purpose rather than a browser setting, so an unrecognised value still falls back to the negotiation instead of consuming its single slot: `?lang=qq` on a `zh-CN` browser is still Chinese, and reaches English only when the browser's own highest-priority tag has no catalogue either. A typo in a support email must not look like a broken panel | O-8 |
| 7 | **Unchanged and restated because a rule change is where they get dropped:** nothing is persisted (no cookie, no `localStorage`, no column, no picker); `Vary: Accept-Language` is still mandatory and appended wherever the server negotiates, and still absent from the static origin; and the machine-translation notice is still non-dismissible on every language except English and Chinese | O-8, §7.6 |
| 8 | **`tests/web/test_i18n_negotiation.py` is new, and the shape of its assertions is the point.** A test that `fr-CA, zh, en` yields English passes against a negotiator that always returns English, so **every assertion of that form is written beside one that a supported tag still resolves** — `zh, sv` yields Chinese, `de-AT, xx` yields German, `zh-TW` yields Traditional. The pair is what proves the rule rather than a broken matcher. It runs `web/js/i18n.js` under Node against the real `web/locales/index.json`, so the rule has a check that does not need the stack up; Node is the runner only and there is still no build step | O-8, §7 |

### v1.25 — 2026-08-13 (the language is negotiated, not chosen; the calculator gets twenty; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **v1.24 item 3 is withdrawn: the `kaicalc_lang` cookie is deleted and there is no language picker on either surface.** The header is negotiated per request and **nothing is persisted** — not a cookie, not `localStorage`, not a column. v1.24 declined `Accept-Language` on the reading that §2.3 forbids it; that reading is too strict, and §2.3's own wording is the correction: it forbids **storing** an address, a user agent or a fingerprint, and states that "user agents, headers and paths are read within a request and forgotten". Reading one to decide what to render is what that sentence describes. The repository owner ruled directly: negotiate per session, read the browser's language, store nothing, add no picker | O-8, §2.3 |
| 2 | **Two surfaces, two mechanisms, and the difference is forced rather than chosen.** The **calculator is static files served by nginx and never reaches FastAPI**, so it reads `navigator.languages` — the *ordered list*, never `navigator.language`, because a visitor whose first preference has no catalogue must get their second before English. The **panel renders through FastAPI**, so it reads `Accept-Language` and honours its quality values: `zh;q=0.8, en;q=0.9` is a request for English, and a parser that reads the header in written order gets that backwards. `q=0` is dropped rather than ranked last; `*` is dropped; a malformed entry is dropped rather than defaulted | O-8, §7 |
| 3 | **Matching is RFC 4647 lookup, not equality, and a catalogue claims its own tags.** Truncate one subtag at a time: `en-NZ`→`en`, `zh-CN`/`zh-Hans`→`zh`. Truncation alone sends `zh-TW` to Simplified Chinese, which is the **wrong script rather than a graceful degradation**, so each catalogue file carries a `tags` list and an exact claim is matched before any truncation — `zh-Hant` claims `zh-TW`, `zh-HK`, `zh-MO`; `tl` claims `fil`. **A tag nobody claims resolves to nothing rather than to English**, so the visitor's next preference gets a turn first. No two catalogues may claim one tag; a test enforces it | O-8 |
| 4 | **`?lang=` survives as a one-request override with no persistence** — for testing, screenshots and support, emitted by no control. **An unrecognised value is ignored and the request then negotiates as though it were absent**: `Accept-Language` / `navigator.languages` first, English last. Not an error, not a redirect, not remembered | O-8 |
| 5 | **`Vary: Accept-Language` is mandatory wherever the server negotiates, and is appended rather than assigned** — replacing the header would drop the `Vary: Cookie` FastAPI sets on session responses. It is set by the outermost middleware so it reaches responses the inner ones refuse; **a 403 cached without it is served to everyone.** It is deliberately **not** set on the static origin: the calculator does not negotiate, and `Vary` on a near-unique header would make a shared cache store a copy of every asset per browser and hit on none | O-8, §7.6 |
| 6 | **The calculator ships twenty languages.** Simplified Chinese, Traditional Chinese, Hindi, Tagalog, Panjabi, Korean, Afrikaans, French, German, Spanish, Dutch, Japanese, Gujarati, Arabic, Tamil, Vietnamese, Thai, Russian, Urdu — nineteen from the client's list, plus **Malayalam** as the twentieth, taken from the same census ordering, where it sits between Russian and Thai. **Samoan, Tongan and every other Pacific language are out**, for te reo Māori's reason and not for a different one: low-resource machine translation plus a cultural expectation of native review. A census-ordered list puts them near the top, which is exactly why the exclusion has to be stated rather than left to the ordering | O-8 |
| 7 | **Every language except English and Simplified Chinese ships machine translated and unreviewed, and the interface says so on the page.** The notice used to sit on the switcher's option, where somebody was choosing; with no switcher there is nothing to hang it on, so it is a **non-dismissible strip at the top of every page**, written twice — once in the language being read and once in English, because the one sentence a machine-translated page must get right went through the same machine as the rest. It is also appended to the **results export**, which leaves the browser and is read by somebody who did not choose the language. Driven by `machine_translated` in each catalogue file, so adding a language is adding a file | O-8, §7.6 |
| 8 | **Arabic and Urdu ship right-to-left**, driven by a `dir` field in their catalogues. This was conditional on the layout being able to carry it: every `margin-left`, `padding-left`, `border-left` and `text-align: left` in `styles.css` that carried meaning is now its `-inline-start` form, and the one inline style in the front end — the diverging comparison bar's offset — is `margin-inline-start`. **A half-mirrored page would have been worse than not shipping the two languages**, and a test refuses a physical direction property in the stylesheet so the trade cannot be quietly undone | §7.6 |
| 9 | **What is never translated, checked on the calculator as well as the panel.** Decimals (they cross the wire as strings; `toLocaleString('en-NZ')` stays, in every language, so `1.200,50` and `1,200.50` never become the same figure written two ways); `code` identifiers; factor set version labels; metric units and `metric.name`; the **equivalence sentences**, which §3 defines as `label_template` interpolated **by the engine** and which the export copies verbatim — they are data, not interface, and translating them here would override the client's approved wording; and **everything a staff member typed**, which on the calculator is most of the visible text: destination names, food categories and sector names all come from the database in the language they were entered in | O-8, §3, §7.6 |
| 10 | **The consequence of item 9, stated rather than discovered:** a Thai visitor gets a Thai interface listing English destination names. The chrome, the instructions, the validation messages and the results wording are translated; the taxonomy inside them is not. This is coherent — the reader knows which words are the tool's and which are the data's — and it is not complete. Closing it needs translated taxonomy columns in `§2.1`, which is a schema change, a panel change and a client decision about who writes them, and is recorded in O-8 as the next step rather than done here | §2.1, O-8 |

### v1.24 — 2026-08-13 (interface translation, and the admin panel in Chinese; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **Open item O-8 stops being unpromised and describes what is delivered.** The client removed te reo Māori from scope at the first demonstration — not deferred, removed — and asked for roughly twenty other languages, static interface strings only. The repository owner then added the admin panel, and its reason changes its rank: the panel's users are the development team, half of whom could no longer follow its English domain vocabulary at working speed. **Chinese is therefore not one of twenty. It is the only language with real users today**, and the only one that will have native speakers noticing when a translation is wrong. `architecture.md` §10 O-8 | O-8 |
| 2 | **The architecture: the English source string is the key, catalogues are JSON, one file per language per surface.** `admin/locales/<lang>.json` ships as wheel package data; `web/locales/<lang>.json` will be fetched by the browser. Two locations because package-data cannot reach outside its package and the two Dockerfiles copy disjoint trees — a top-level `i18n/` would be in neither image. The **key scheme, fallback rule, cookie and notice rule are one contract across both surfaces.** No new dependency and no build step: `sqladmin`'s own `I18nConfig` needs `babel` and ships no Chinese catalogue, but `jinja2.ext.i18n`'s `install_gettext_callables` takes ours, which translates **sqladmin's own fifty strings through our catalogue** with no fork of its templates | O-8 |
| 3 | **Language is chosen by `?lang=` and remembered in a `kaicalc_lang` cookie, path `/`.** Not the staff session: the login page and the whole enrolment flow render before a session exists, and a person who cannot read English needs those pages most. Not a column on `staff`: the preference belongs to a browser, not a person. **No `Accept-Language` sniffing on either surface** — the header is a fingerprinting signal and §2.3 forbids reading a visitor that way. Absent, empty or unrecognised ⇒ English, and an unrecognised value is ignored rather than stored. **The cookie is never read into a submission, never logged and never aggregated** | O-8, §2.3 |
| 4 | **A missing key renders its English source, silently at runtime and loudly in the suite.** A half-translated language ships as English-in-places rather than as blank labels or key names. `tests/admin/test_i18n.py` walks the live `form_args` of every registered view, every view name, and the msgid set read out of the installed `sqladmin`, and fails on anything untranslated — which is how rewording an English string, the known cost of source-text keys, becomes a failing test on the commit that reworded it rather than a paragraph in the wrong language weeks later | O-8 |
| 5 | **The machine-translation notice goes at the language switcher, on the option itself**, driven by `machine_translated` in each catalogue file so that adding a language is adding a file. **English and Chinese carry no notice** — English is hand-written and Chinese is reviewed by its users — and that Chinese is *not* flagged is asserted by a test, because if it were the switcher would make a claim about it that is false | O-8 |
| 6 | **What must never be translated, as rules rather than practice:** anything a staff member typed (factor notes, taxonomy names and descriptions, formula labels, version labels, audit log contents — this is what makes the panel WYSIWYG); `code` identifiers; decimals, which cross the wire as strings and take no locale-aware separator or grouping on either surface; metric units; and the operator messages in `admin/cli.py` and `docker/init.sh`, which are read in a terminal and pasted into search engines | O-8 |
| 7 | **Nine field descriptions are flagged as data-error risks and listed in O-8**, because a wrong reading of each produces a wrong public number rather than a confused staff member — `metric.display_unit`'s same-scale rule (a `t CO2e` label against a `kg CO2e` unit reads a thousand times too small, and was live until August 2026), the prevention destination's zero factors, the legitimacy of a negative downstream factor, and "leave blank means every category", among them. This is the concrete reason the reviewed language and the machine-translated ones are two different promises | O-8 |

### v1.23 — 2026-08-13 (Back and Next become a navigation bar; affects C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **`view.js` replaces `buttonRow(backStep, label, disabled, action)` with `stepNav({step, back, backLabel, label, disabled, action})`.** The client's first look at the calculator was that advancing requires scrolling at every step, and it was measured rather than argued: at 1278×983 the primary action sat **+2961px** past the fold on the food-type step, +796 on destinations, +630 on review and +1230 on results; at 938×898, +3046 / +899 / +715 / +1342; at 390×700 five of the seven screens failed, including the two that pass on a desktop. Every content-shrinking lever together (`h1` to 2rem, hiding the indicator, tighter `main` padding, two-column destinations) still left destinations at +238 / +395, so the row that advances the form stops being content competing with the form for height and becomes `position: sticky; bottom: 0`. Reachability is now −13px or better on every step at all three viewports. **The goal is not zero scrolling** — the food-type step is 40 categories long and always will be — it is that *advancing* never requires scrolling | §7.3 |
| 2 | **The six-step progress band is deleted from `index.html` and folded into the bar.** `#step-indicator` cost a measured 87px at the top of every step for a list of names nothing could click, and that 87px is exactly the currency the complaint was denominated in. `renderChrome()` no longer writes it; the bar carries "Step N of 6", the step's name and a progress track. `.step-mobile` and the ≥850px `<ol>` were the same information at two widths and **both** go — one element, not three. The `<ol>` is not relocated: anywhere it could go re-spends the height this change was made to free | §7.3 |
| 3 | **`sticky`, not `fixed`, and the difference is asserted.** Sticky sits in its natural place when the step is short — 235px clear of the fold on the amount step at 1278×983 — and pins only when the section would push it past the fold, so a short screen does not grow what reads as a cookie banner. At full scroll it un-pins and returns above the footer, which is why **no page-level padding is owed to it**; only `scroll-margin-bottom` is, so a control focused by Tab is not scrolled flush underneath it. It is also the half of the pair that survives a mobile soft keyboard: in flow, it moves with the layout viewport instead of being stranded behind the keyboard | §7.3 |
| 4 | **`.main-content`'s `min-height: calc(100vh - 220px)` is deleted with the band it was arithmetic over.** 220 counted a header, that step-indicator band and a footer. Left alone with the band gone it would have floored every short step 87px taller than its content — invisible on screen, and a previous pass had already found short steps at 1920 measuring exactly the floor, so shrinking their content changed nothing. `body` is a flex column and `main` takes the leftover height, so there is no constant left to drift. `methodology.html` shares the stylesheet and gets a footer pinned to the bottom of short pages out of it | §7.3 |
| 5 | **What the bar shows where there is no "Next".** The **intro has no bar**: it is a full-bleed dark hero whose own CTA already measures −447 / −362 / −273, and a second start action would both duplicate it and break the panel. The **results view does get one** — its first action was +1230 to +2075 past the fold — carrying `Edit your data` (back to review) and `Download results`. `Start a new calculation` stays in the page rather than the bar: it is a confirm-guarded reset rather than a step action, and the header's home button already offers it | §7.3 |

### v1.22 — 2026-08-13 (the prevention destination stops being a code; affects B, C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **New column `destination.is_prevention`, and `db.types.PREVENTION_CODE` is deleted.** `prevention` was the one destination code this system knew by name and five guards were stated in terms of the literal: §6.2's refusal of it in a *current* scenario, §6.1's coverage hold-out, §2.2's O-7 publish check, `admin/taxonomy_rules`'s existence rule and `web/js/calculator.js`'s current-scenario list. A taxonomy row's identity is **data** on this project — `destination_group.is_waste` is a column for exactly this reason, because MfE may revise which destinations count as waste — and the client has not settled what this destination will be called, whether it survives under that name, or how it appears. A structural *role* recognised by a magic string is the same mistake one level down. The row may now be renamed freely and every guard follows; `db/repository.prevention_destination_codes` is how a caller outside `db/` asks which codes carry it, since `api/` may not import `admin/`. Migration `0013`, which backfills the flag onto `prevention` and `refed_prevention` — **the only place in the repository that still writes either code into a guard-shaped statement**, and a one-time data statement about the rows that exist rather than a rule | §2.1, §5.1, §5.2, §6.1, §6.2, §7.2 |
| 2 | **The defect that closes with it: a second vocabulary's prevention row could be entered as current-scenario waste and reach the public statistics.** §10.3's ReFED fixture brings `refed_prevention`, whose 156 upstream and 156 downstream rows are every one of them zero — a prevention destination by every property that matters. Because §6.2's guard tested a literal, it was refused for `prevention` and accepted for `refed_prevention`, which persisted as an ordinary `submission_line` with `scenario = 'current'` and became a `by_destination` bucket: waste that by construction did not happen, counted as real waste, on the page whose whole design problem is not overclaiming. **This is the defect v1.5 closed for `prevention` itself, arriving one code along**, and §5.4's scenario predicate structurally cannot catch it for the same reason it could not catch the first — it excludes the *alternative* scenario, and the line is not in the alternative scenario. Reproducible on the deployed stack, which has the ReFED set published and offers `refed_prevention` on the form | §6.2, §5.4 |
| 3 | **"At least one active row", not "exactly one" — and this is where the `is_standard_mix` prior art is deliberately not followed.** §2.1 requires exactly one active `is_standard_mix` food category because §6.2 must resolve a null `food_category` to *one* code; nothing anywhere has to choose between prevention destinations, and v1.19 put a second vocabulary's rows in the same global tables, so `prevention` and `refed_prevention` both legitimately carry the role. "Exactly one" would refuse the state this deployment is already in. There is therefore **no UNIQUE key and no `COALESCE(...)` functional index** on the column: nothing for one to say. The lower bound is `admin/taxonomy_rules.check_prevention_destination` — renamed from `check_prevention_intact` — counted over **active** rows whose **group** is also active, and skipped on an empty destination table so the panel stays bootstrappable. **Zero is refused rather than given a meaning:** an alternative scenario that merely redirects mass between real destinations stays expressible without one, so nothing computes wrongly, but the improvement panel would render sliders that cannot express the only thing it is for. If the client removes the concept, that is a contract change — this row — and not a data edit | §2.1 |
| 4 | **The zero-factor property becomes enforced where it was assumed.** `find_missing_prevention_upstream` checked the *value* of an upstream override since v1.11, but only where a generic row existed to compare it against — a set built §10.3's way, with an explicit row per destination and no generic rows at all, could carry a prevention row at 1.9 and publish. **Nothing anywhere read `factor_downstream`.** `publish_factor_set` now refuses any upstream **or downstream** row in the set that prices a flagged destination at something other than zero. An *absent* row stays legal, because §4.1's lookup already returns zero for a missing factor and the §6.1 hold-out is built around a set that prices a prevention destination nowhere. Both factor sets in this repository pass unchanged. The completeness check is generalised by **role, not per row**: a tuple qualifies when it has a generic upstream row and **no** flagged destination carries a zero override for it — requiring one per flagged row would refuse `MOCK-v0` the moment §10.3's fixture is loaded, and one working offset is what the guard exists to guarantee | §2.2, §5.2 |
| 5 | **`destinations[].is_prevention` is a new key in the §6.1 response, and §6.2's rule moves out of Pydantic.** The front end needs the flag: `calculator.js` kept the offset off the current-waste list by comparing codes and so left every other prevention destination on it. On the server the rule is now in `entry_rule_problems`, because the set of prevention codes is a database read and a Pydantic validator has no session. `details[].field` is unchanged at `entries[i].current` — that placement was the point of the `AfterValidator` and it is written out by hand — and `details[].issue` changes from Pydantic's generic **`value_error`** to the stable slug **`prevention_in_current`**, which is what §9 asks a consumer to branch on | §6.1, §6.2, §7.2, §9 |
| 6 | **Knowingly left, and recorded rather than hidden:** under a published set of one vocabulary, the *other* vocabulary's prevention row is now also offered, because flagged rows are held out of the coverage inference by role. That crossing already happens in the other direction and v1.21 accepts it. The residual is that an alternative-scenario line to a foreign prevention destination falls back to the generic upstream row and is charged for it — which **understates** the benefit of wasting less. Conservative in the direction this project cares about, and bounded to a database holding two vocabularies at once | §6.1 |

### v1.21 — 2026-08-12 (raised by the repository owner; affects B, C, D and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **§6.1 returns the vocabulary the published factor set covers, not every active row.** §2.1's three vocabulary tables carry no `factor_set_id` — a factor set brings factors, not a vocabulary — so publishing one could not narrow the calculator's form, and a user who typed a quantity against a destination the published set has no factors for got **a silent zero, with nothing on the form to say which it was.** That was invisible while exactly one set of taxonomy rows existed and became impossible to miss when v1.19's ReFED fixture put a second, disjoint vocabulary in the same tables: 26 destinations offered, 12 priced. **It was never only ReFED's** — `MOCK-v0` prices 6 destinations of 14 and 3 sectors of 6, so most of the New Zealand form is a silent zero today and this is what stops it claiming otherwise. Covered means: a **destination** with a `factor_downstream` row (including the `food_category_id IS NULL` row, which is how the waste levy is held) or a non-NULL `factor_upstream.destination_id` (the O-7 column); a **sector** in `factor_upstream.sector_id`; a **food category** in `factor_upstream.food_category_id` or a non-NULL `factor_downstream.food_category_id`; a **destination group** with at least one visible destination; a **unit preset** whose `food_category` is null or visible. The rows come back when real factors are loaded, with no code change | §5.1, §6.1 |
| 2 | **Two rows are never filtered, and neither is an exception so much as a row the rule cannot speak about.** `prevention`'s factors are zero **by construction** — that is the whole of what makes it a 100% offset and keeps the two scenarios mass-conserving — so "has no factor row" is not evidence a set does not support it. It is held out by name (`db.types.PREVENTION_CODE`) and its group with it. It is covered anyway in every set the lifecycle will publish, because `publish_factor_set` refuses a set whose general upstream rows have no matching `prevention` row at zero — **but that is a coincidence of two other rules rather than a guarantee**, and the improvement panel is unusable the day it stops holding. A second vocabulary's own prevention row (`refed_prevention`) is covered by the ordinary rule and needs no special case. The **`is_standard_mix` food category** is kept for the structural half of the same reason: §2.1 requires exactly one active row to carry it and §6.2 resolves a null `food_category` to it, so filtering it out would leave a caller with no legal way to say "composition unknown" while the server went on resolving null to a code it was never offered. §2.1's "exactly one" invariant is still counted over the **active** rows, not the narrowed list — counting the narrowed list answers 500 on the first request the day a set the standard mix is not in gets published | §2.1, §5.1, §6.1 |
| 3 | **`metric` and `get_taxonomy_for_bundle` are deliberately not narrowed.** Metrics are the *output* vocabulary — nothing a user types is a metric, so an uncovered one cannot become a silent zero; it would be a zero column, visible on its own terms (§10.3 already rules metric rows global). `get_taxonomy_for_bundle` is a different function feeding the engine's dictionary of legal codes and §6.3's factor export, and it stays a **superset on purpose**: narrowing it would turn every code §6.1 no longer offers from a zero into an `UNKNOWN_CODE` 400, including for a browser tab holding a taxonomy fetched before the last publish. Reading a historical submission is unaffected either way — §5.4 selects `destination.code` and `.name` from the tables it joins and never consults §6.1's snapshot | §5.1, §5.4, §6.3 |
| 4 | **The filter is one repository function's and must not reach `sqladmin`.** Staff have to see and edit every taxonomy row whatever is published — a row cannot be given its first factor if the panel has stopped listing it. `get_taxonomy` has exactly one caller, `GET /api/v1/taxonomy`; the panel queries the models directly through `admin/modelviews.py`. `tests/db/test_taxonomy_coverage.py` asserts both halves: an ordinary model query still returns every row (which fails if this is ever implemented as a `with_loader_criteria` or a query event), and no file under `admin/` names `get_taxonomy` | §8.1 |

### v1.20 — 2026-08-12 (raised by the repository owner; affects C and D)

| # | Change | Section |
| --- | --- | --- |
| 1 | **§7.3a: the results export carries the results.** `downloadResults` produced `food-waste-impact-results.txt` containing the total mass, each entry, its destinations and quantities, the factor version and the placeholder notice — and **not one output figure.** No greenhouse gas, no methane, no water, no cost. The file name says "results" and the file is the one somebody attaches to an email, so this is not a missing nicety: it is a deliverable that names itself after the thing it omits. It now carries the impact summary (every metric in `totals.current.metrics` except `mass`, at that metric's own `display_precision`, labelled with the `unit` that travelled with the figure), the tangible equivalents as the engine worded them, each entry's own metric totals, each destination's `by_destination[].value`, and — when a comparison was run — `totals.net_benefit` per metric. `mass` stays out of the impact list for the reason `summaryCards` keeps it out and is the "Total food waste" line instead. Every figure is read from the response; nothing is summed, differenced or re-scaled on the way to the file (§7.6.1) | §7.3a, §7.6.2 |
| 2 | **New export `buildResultsReport(state)`, and `downloadResults` becomes the two lines that touch the browser.** The report is worth asserting on and a `Blob` is not. `tests/web/test_results_export.py` is the first test in this repository that executes a line of `web/js/`: it runs the real module under Node against `tests/fixtures/calculate_response.json` and asserts the figures — `4,449.0 kg CO2e`, `3,468.0` against landfill — appear as whole anchored lines. Node is the runner only and does not enter the stack (`architecture.md` §3): there is still no build step, no `package.json` and nothing for a browser to load | §7.3a |

### v1.19 — 2026-08-12 (adds an external benchmark; affects A, and reserves a code prefix for everyone)

| # | Change | Section |
| --- | --- | --- |
| 1 | **New §10.3: a ReFED comparison fixture, and the `refed_` taxonomy code prefix is reserved.** Every correctness check this project has descends from this document, so none of them can catch a mistake made *in* this document. ReFED's Impact Calculator is the product this one is modelled on and it publishes its conversion factors; `tests/benchmark/refed/` runs one scenario through both and agrees to the limit of our own `DECIMAL(20,10)` storage. Because taxonomy rows are global and carry no `factor_set_id` (§2.1), the fixture's sectors, food types and destinations are additional rows in the shared tables rather than a private vocabulary — so **`refed_` is reserved as a code prefix and no New Zealand taxonomy row may take it.** The fixture is loaded as a `draft` and is never published: it is United States data, `is_mock` is true on it, and the placeholder banner is correct while it is selected | §2.1, §10.3 |
| 2 | **§10.3 records that our factor tables cannot hold ReFED's shape one-for-one, and what that costs.** `factor_upstream` is keyed `(sector, food_category, destination)` and matches ReFED's key exactly; `factor_downstream` is keyed `(destination, food_category)` and has no sector column, while ReFED's downstream factors differ by sector in 82 of their 102 (food type, destination) groups. This is not a defect — no New Zealand requirement asks for a sector-varying downstream factor — but it is the reason the fixture's food category codes carry a supply chain stage, and it is the first thing to check if a downstream factor ever does need to vary by sector | §2.2, §10.3 |

### v1.18 — 2026-08-12 (closes open item O-9; affects B and E)

| # | Change | Section |
| --- | --- | --- |
| 1 | **New request header `X-Staff-Proof`, and §6.2's dry-run row now names a mechanism.** The row has required "an authenticated staff session" since v1.1 and never said how the API establishes one. Nothing did: `api.app:create_app`'s `staff_authenticator` was supplied only by `tests/api/test_api.py` and `tests/api/test_api_entries.py`, so **every dry run in every deployed system answered `UNAUTHORIZED`** and §8.2 was inert — `/admin/try` rendered the refusal inside a 200 page and looked like a working screen. The panel now mints a short-lived signed proof (`db/staff_proof.py`, in `db/` because `api/` may not import `admin/` — v1.3's ruling on `db/detection.py`) and the API verifies it as its **default** authenticator, `staff_authenticator=None` having been changed from "no authenticator" to "use the default", the same correction `blocklist_check` already carries. The rejected alternative was letting the API read the panel's session cookie; see the note under §6.2 for why sharing one origin with `/admin` makes that a wider grant than it looks | §6.2, §8.2 |
| 2 | **The panel no longer forwards the browser's cookies to the API.** `admin/calc_client.py` sent `dict(request.cookies)` on every dry run in the belief that the API authenticated with them. It does not and must not, so the forwarding proved nothing and its only effect was to hand a live staff session cookie to a second service. `CalculateClient.dry_run`'s `cookies` parameter is replaced by `actor` | §8.2 |

### v1.17 — 2026-08-12 (from merging `main` back into this line, affects nobody's code)

| # | Change | Section |
| --- | --- | --- |
| 1 | **§10's fixture note is rewritten: `tests/fixtures/` exists.** The note merged in from `main` was written at v1.2 and says in the present tense that the directory "does not exist on any branch a reader of this document is likely to be standing on", that two disagreeing sets sit on two unmerged branches, and that the canonical set will land with B's integration PR. All three were true then; none is true now. B's integration merged as PR #12, and the directory holds the seven files, `errors/`'s seven — `blocked.json` among them, the one the note called the only genuinely missing file — and `tests/golden/`'s nine cases. **A contract that describes work as outstanding when it is finished is worse than one that is silent about it**, because the next reader does the work again. The reasoning is kept in the past tense: why one canonical set rather than two, and the four content requirements that were argued for on their own merits and remain binding | §10 |

### v1.16 — 2026-08-12 (raised by the repository owner, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | **An administrator-issued password is kept and re-readable on exactly the same terms as a created one.** `issue_password` **overwrites** `staff.unclaimed_password_enc` with the password it mints, where v1.15 had it *clear* the column. v1.15's asymmetry had no defence: the loss the feature exists to prevent — a closed tab taking a password nobody wrote down — was prevented for a created password and not for an issued one, and the only recovery from the second was to issue *yet another*, which stops the one already read out to the colleague from working. That is the same exchange v1.15 removed from account creation, left in place one screen away. The overwrite is unconditional: a stale value here is a password that no longer opens the account, so "leave it alone if something is there" would have the panel offering a dead string. `secret_key` becomes a **required, keyword-only** argument of `issue_password` and of `kaicalc-admin issue-password`'s `cmd_issue_password`, for the reason it is required on `create_staff` — an optional one is a caller that silently forgot, and the state that produces is precisely the NULL column this revision abolishes | §8.3 |
| 2 | **`staff.initial_password_enc` is renamed `staff.unclaimed_password_enc`** (migration `0012`, an in-place `ALTER TABLE ... CHANGE`: same type, same nullability, same data, same key). Under v1.15 a non-NULL value meant "this account has never been used"; after item 1 it can belong to an account three years old whose owner lost their password this morning, so the old name would go on telling every future reader something false — in the model, on the accounts list ("Initial password still unclaimed"), and in `audit_log`'s `after_json`. The Python surface renames with it: `Staff.initial_password_unclaimed` → `Staff.has_unclaimed_password`, `encrypt/decrypt_initial_password` → `encrypt/decrypt_unclaimed_password`, `reveal_initial_password` → `reveal_unclaimed_password`, `INITIAL_PASSWORD_ENCRYPTION_INFO` → `UNCLAIMED_PASSWORD_ENCRYPTION_INFO`, `/admin/staff/initial-password` → `/admin/staff/unclaimed-password`, and the `show-initial-password` action → `show-unclaimed-password`. **The HKDF `info` byte string is deliberately unchanged** (`b"initial-password-encryption"`): it is key-derivation material, so editing it to match the name would derive a different key and turn every stored value into a blob nothing can open. Both `REDACTED_FIELDS` (§5.5) hold the old name alongside the new, the way `mfa_secret_enc` has been kept since v1.13 | §2.4, §5.5, §8.2, §8.3 |
| 3 | **`audit_log`'s reveal entry now reads `{"username": ..., "revealed": "unclaimed_password"}`.** The value was `"initial_password"`, which the trail — append-only, and the one table that must not say anything untrue — would have gone on asserting about passwords that were not initial | §2.3, §8.3 |
| 4 | **Wording corrected wherever it claimed a one-time reveal, including two places that were already wrong before this revision.** `brand/issued_credential.html` said "This is shown once and is not recoverable"; `brand/staff_created.html` said "This password is shown once and is not stored anywhere", which stopped being true when v1.15 landed and would send an administrator to mint a replacement for a password sitting one proof away. The bootstrap output's "These passwords are shown once and cannot be recovered" is corrected the same way: bootstrap goes through `create_staff`, so one lost line is recoverable *by the other administrator*, and only losing both is terminal — for which the output now names `kaicalc-admin issue-password` | §8.3 |
| 5 | **`staff.unclaimed_password_enc` is added to §2.4's `staff` table**, which v1.15 changed the schema without doing | §2.4 |

### v1.15 — 2026-08-12 (raised by the repository owner, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | **The audit log becomes `role = admin` only.** §8.3's role table has granted "view audit log" to both roles since v0.2, and the panel implemented that faithfully — a `staff` account reached **Audit log** in the sidebar and read every actor's entries, administrators' password issues, account creations and deletions included. **That was the specification, not a defect in the code**, and it is the specification that is being changed: the trail is an administrator's oversight tool, and a staff member reading their colleagues' account administration is not doing anything the role exists for. Enforced through `is_accessible`, which sqladmin consults for `/list`, `/details/{pk}` **and** `/export/{export_type}` separately (three handlers, not one); `AuditLogAdmin` declares no `@expose` and no `@action`, the two route kinds sqladmin registers with `login_required` alone, so those three are the whole of its surface. The rejected alternative was "their own entries only", and it was rejected on two grounds: a `ModelView` reads through four different queries (`list_query`, `get_object_for_details`, `get_model_objects`, `ajax_lookup`) so a filter on one leaves the others open to a guessed `id` or a single export request; and `actor` is a plain `VARCHAR(128)` that also holds `cli`, `bootstrap`, `deploy-seed` and `unknown`, so a per-actor view is a trail with holes and nothing on the page saying so | §8.2, §8.3 |
| 2 | **`write_audit`'s `REDACTED_FIELDS` is unchanged, and the six passages justifying it with "`audit_log` is readable by every staff member" now read as historical.** Narrowing the audience is not a reason to widen what is written. `password_hash`, `mfa_secret_enc`, `code_hash`, `token` and `ip_hmac` are credentials and identifiers no screen should render at any role, and the trail is exportable, pasted into tickets and read over shoulders. A redaction dropped because "only administrators see it now" is one that has to be found again the first time a read-only auditor role is added | §5.5 |
| 3 | **The initial password is stored, reversibly encrypted, until it is claimed.** New nullable column `staff.initial_password_enc`. **This is a deliberate weakening of credential storage and it was the repository owner's decision, taken with the cost stated.** It exists because a one-time reveal is easy to lose and this project has already lost a set of recovery codes to exactly that shape. Written only by `create_staff`; cleared unconditionally by `set_password` (self-service *and* the forced change at first login) and by `issue_password`; gone with the row on `delete_staff`. Read only through `/admin/staff/initial-password`, which is `role = admin`, takes the same re-authentication proof as account creation and deletion, and writes an `audit_log` entry per reveal. Encrypted with Fernet under a key derived from `SECRET_KEY` by HKDF with its own `info`, never the one `mfa_secret_enc` uses. **The cost, plainly:** anyone holding both a database dump and `SECRET_KEY` can log in as every account that has not yet claimed its password, and those accounts are pre-MFA — the attacker enrols the authenticator — so the password is the whole of their protection. The exposure is bounded by the column being NULL at every other moment of an account's life | §2.4, §8.3 |
| 4 | **`audit_log.action` gains `reveal`.** Written by `/admin/staff/initial-password` on each successful display. Recorded here rather than reused as `read`, because the enumeration is already the thing that has drifted twice (v1.14 added `archive` and `refuse` after both were already being written) and a reader branching on the documented set would silently drop these rows | §2.3 |
| 5 | **§8.2's audit-log path is `/admin/audit-log`, not `/admin/audit`.** sqladmin derives a `ModelView`'s identity from its model class name (`AuditLog` → `audit-log`), so the URL this document has named since v0.2 has never been the one the panel serves. Corrected in the document, not in the code: the identity is what every `request.url_for("admin:list", identity=...)` call and every menu link already resolve to | §8.2 |

### v1.14 — 2026-08-11 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | **Accounts can be deleted.** `/admin/staff/delete` and `kaicalc-admin delete-staff`, both through `admin.accounts.delete_staff`. Only `deactivate_staff` existed, so an account created by mistake became permanent furniture in the list administrators read to answer "who can get into this system". The deletion is a **hard delete and costs the audit trail nothing**: `audit_log` holds no foreign key to `staff` and cannot — `actor` is `VARCHAR(128)` and also carries `cli`, `bootstrap` and `deploy-seed` — so every entry a deleted account wrote stays complete and still names it. A tombstone would have bought a property the trail already has while leaving the row and the username in place. **No migration**; nothing in the schema changes. | §8.3 |
| 2 | **Deletion requires the account to be deactivated first, and `reactivate_staff` ships with it.** Deletion then acts on a row whose sessions are already gone rather than racing one in flight. Requiring it is only defensible if the mandatory step is reversible, and it was not: deactivating the wrong account had no undo. Also guarded by the two-administrator floor, by the refusal of self-deletion (which runs *before* the deactivation check), and by re-authentication at the point of the action. | §8.3 |
| 3 | **`last_password_change()` is scoped to entries at or after `staff.created_at`.** It reads the trail by `(table_name='staff', row_id)`, and **SQLite reuses a deleted rowid** while MySQL does not — this module is imported by both. Without the scope, an account created after a deletion could be handed the dead account's id and be shown its password history. Fixed on the read side, so `audit_log` stays append-only. | §8.3 |
| 4 | **An authenticator can be renamed at any time, and an unconfirmed enrolment is destroyed rather than left in the list.** Auto-generated names carried no information and nothing could revise them; "Set-up not finished" was not a state anybody could act on, since the secret and QR are shown once, so the row could only ever be litter that a manual Remove cleared. It now dies on a GET of `/admin/security` and on beginning a new enrolment — no timer — and cannot touch a confirmed device. Renaming does **not** bump `session_generation`: a name is a note, not a credential. | §8.3 |

### v1.13 — 2026-08-10 (raised by E, affects B)

| # | Change | Section |
| --- | --- | --- |
| 1 | **New table `staff_totp_device`; `staff.mfa_secret_enc` and `staff.mfa_last_counter` are dropped** (migration `0010`, which backfills every existing secret, confirmed or not). One secret column holds one phone, so "enrol a new authenticator" could only mean "replace the one you have" — impossible once the phone is gone. That left an administrator reset as the only way back from a lost device, and v1.12 made that administrator necessarily *somebody else*, so a lost phone became a lockout waiting on a colleague. An account may now enrol several devices and verification tries each. **`last_counter` is per device and must stay so:** two phones emit different codes for the same time step, so a shared counter would refuse the other phone's current, unused code as a replay — a lockout that appears only on two-device accounts. `staff.mfa_enrolled_at` **stays**, as documented derived state, so the onboarding gates and the two-administrator floor keep reading one indexed column. | §2.4 |
| 2 | **`REDACTED_FIELDS` gains `secret_enc`.** The old name is kept alongside it: `audit_log` rows written before `0010` still carry `mfa_secret_enc`. Anyone who copied the set by value now redacts one field too few. | §5.5 |
| 3 | **`rotate-key` re-encrypts every device row, not one secret per account.** A rotation that walked `staff` would leave every *second* phone readable only with the old key, report success, and fail silently until somebody reached for their backup device. | §8.3 |
| 4 | **New screen `/admin/security`, reachable by any signed-in account including `staff`.** It restores what v1.12 removed — changing your own password, alone — through a path that re-proves who is asking: the current password for a password change (a TOTP code is deliberately *not* accepted there), and the password or an existing code for adding or removing a device. Recovery layer **L0** is added to the ladder: a second authenticator enrolled *before* the first is lost. | §8.3 |

### v1.12 — 2026-08-10 (raised by E, affects B and D)

| # | Change | Section |
| --- | --- | --- |
| 1 | `audit_log.action`'s enumeration gained `archive` and `refuse`. **Both were already being written and neither was listed.** `archive` has been written by `factor_lifecycle.py` since E-6; `refuse` is new, and records an attempt that was rejected rather than a change that happened — a self-targeted `issue-password` or `reset-mfa`, which the service layer now refuses because they are recovery actions meant for *another* administrator to perform and become a privilege escalation applied to oneself. Anyone reading the audit trail and branching on the documented five would have silently dropped rows; anyone adding a CHECK constraint from this table would have rejected writes the application makes. | §2.3 |

### v1.11 — 2026-08-10 (the O-7 guard checks the value, and one fixture edit gets the row it should have had, **affects B and E**)

**v1.9's guard refused a *missing* `prevention` upstream row and never looked at what the row said.** A staff member satisfied it completely with a `prevention` row at 1.9 — the same value as the general row — and reopened O-7 for that tuple with no error, no warning and nothing in the log. It is a worse position than the absent row, because both callers' messages already told the staff member to add one "at 0": a set that failed the existence check got fixed, and a set that passed it looked finished. The code did not enforce its own sentence.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`find_missing_prevention_upstream` now reports a tuple whose `prevention` upstream row exists but is non-zero**, not only one where the row is absent. The query gains `value_per_kg = 0` on its covering subquery; the return shape, the sort and the two callers are unchanged, so `publish_factor_set` in both `db/repository.py` and `admin/factor_lifecycle.py` tighten together — which is the whole reason v1.9 gave the query one home. **Zero is a modelling decision, not a default**: prevented food was never produced, so there is no upstream burden to attribute, and any other value is a claim nothing in the system supports. There is no tolerance and no "small enough" — one unit in the last place `DECIMAL(20,10)` carries is refused. Both messages now say "no `prevention` upstream row at 0 — either it is missing or it carries a non-zero value" | §5.2 | **B, E** |
| 2 | **`tests/fixtures/factors.json`'s `prevention`/`co2e` downstream `source_note` was corrected and no change-log row was written for it.** The note still said `prevention` is not a whole offset and that a prevention figure must not be quoted — forty lines below the v1.8 rows that made it false, in the one file §10 calls a public export. Content only; no shape, no number and no key moved, which is exactly why it slipped through, and it is the same class as the v1.7 entry this document already calls out as "the change that was made without a row" | §10 | **B, E** |

> **This is the first entry in the sequence that changes a refusal a staff member can hit.** A draft that publishes today may stop publishing tomorrow, and that is the intent: the sets it now refuses are sets that would have shipped a partial offset. `rollback_to` stays exempt for v1.9's reason — a set archived before v1.8 will legitimately fail the check, and refusing an emergency rollback over a completeness rule is a worse failure than the one the rule prevents.
>
> **No request shape, no response shape, no engine signature, no front-end module. A, C and D have nothing to do.** The fixtures already satisfy the tightened rule: every `prevention` upstream row in `factors.json` and in `tests/support/sqlite.py`'s seed is at zero, so no fixture changes with this either.

### v1.10 — 2026-08-10 (the golden suite lands, and three descriptions catch up with the code, **affects A**)

**Nothing computes differently in this revision.** Every change below either describes what was already built or writes down a rule the code has been following with nothing behind it — which is precisely the category that goes unrecorded, because none of it breaks a test and none of it moves a fixture. The suite §10.1 has specified since v0.2 now exists: `tests/golden/test_golden.py` as the runner, and the first two cases built from the response fixtures rather than from the engine, so at least one case is a cross-check between two implementations rather than a recording of one.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **§10.1 now specifies the two files it only named.** `request.json` and `expected.json` are §3's domain objects rendered as JSON — **not** §6.2's wire shapes — because a golden case written against §6.2 would certify `api/engine_adapter.py` as well as the engine, and a hoist or an omitted key there would then read as an engine defect. Adds the four rules the suite is worth nothing without: self-contained cases, no regenerate mode, a failure that names the case and the metric and both values, and a stated purpose per case. Records where each case's numbers came from, since only the first two can avoid certifying the engine against itself | §10.1 | **A** |
| 2 | **§4.3 said the evaluator is built on `simpleeval`. It never was** — `engine/evaluator.py` is hand-written over Python's `ast`, and the entry now records that and the four reasons. The load-bearing one: `admin/expressions.py` is its static twin and the two must reach the same verdict on every expression, which is only checkable because both walk the same `ast` objects; when that agreement was first measured it found **eight** disagreements where a hand survey had listed three. Also documents six rules the four-line summary omits, all of them things the panel already enforced. `simpleeval` is dropped from `requirements.txt`, where it was an install nothing imported | §4.3 | **A**, and E should know the twin is now documented |
| 3 | **§3 rule 5 gains the row for a value that rounds to zero from below.** `Decimal("-0.4")` rounds to `Decimal("-0")` and `format(Decimal("-0"), ",")` is `"-0"`, so the rule as written produced `Equivalent to driving -0 km` on a results page. The engine has normalised it since v1.9; the rule did not exist. **No fixture changes with it** — no fixture value rounds to zero from below — which is exactly why it would otherwise never have been written down, and it is the same position the half-up rule was in | §3 | **A, C, D** |
| 4 | **§10.2 settles what `tests/fixtures/factors.json` is.** It is a `GET /factors` response, as §10's table has always said, and it is **not** a bundle: it nests `version_label` and `is_mock` inside `factor_set` and carries none of the five taxonomy sections, so `from_json()` refuses it. §10.2 now states that no fixture is a bundle and gives the composition — `taxonomy.json`'s five taxonomy sections plus `factors.json`'s five factor sections plus those two keys hoisted, which is `db/repository.build_bundle_data`'s own projection. A fourteenth fixture holding a pre-composed bundle was rejected: it would be a second copy of every factor row | §10.2 | **A, B** |

> **No request shape, no response shape, no schema and no front-end module signature moves. C and D have nothing to do**, beyond knowing that a label can now read `0` where the underlying `value` is negative — `value` is unchanged and still carries its sign at full precision.
>
> **The fixture step of §0's process is a genuine no-op this time, and that is worth stating rather than skipping.** Nothing in `tests/fixtures/` changed, because neither of the two behavioural rules written down here has a fixture that can express it: no fixture value rounds to zero from below, and no fixture value lands on a half. Both are held instead by the golden suite — `case_07_negative_total_and_zero_label` and `case_08_mixed_alternative_rollup` respectively.
>
> **Still open after this revision.** **O-1** remains the hard blocker; everything runs on mock factors and the placeholder banner stays mandatory. **O-2 is still the one to read carefully** — see v1.9. O-7 stays closed, and `case_03_prevention_whole_offset` is now the test that keeps it closed: it fails with **96.000** if `factor_upstream`'s destination dimension is removed. Carried forward unchanged: `landfill_diverted` as a real `metric` row, the container-preset input, `gwpHorizon`'s missing control, and the positive/negative colour pair.

### v1.9 — 2026-08-09 (publishing refuses to reopen O-7, **affects B and E**)

**v1.8 made `prevention`'s offset a property of the data, and data can stop being true.** A `(sector, food_category, metric)` given a general upstream row with no `prevention` counterpart reverts to pre-v1.8 behaviour **for that tuple alone** — and that is harder to catch than O-7 was, because O-7 was wrong everywhere and this is wrong for one sector while every other sector on the same results page is right. It arrives with no error, no warning and nothing in the log: a staff member adds a sector to a draft, publishes, and sees exactly what they expected.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`publish_factor_set` gains a second refusal condition and §5.2 now states both.** Publishing is refused, naming the offending tuples, when any `(sector, food_category, metric)` in the target set has a general upstream row and no `prevention` row. **Publish rather than the upstream-factor form**, for two reasons that both matter: it is the single transactional choke point, and a form-level guard cannot see a row that has not been written yet — it would refuse the general row for the sake of a `prevention` row the staff member was about to add next. **`rollback_to` is deliberately exempt**: a set archived before v1.8 will legitimately fail the check, and refusing an emergency rollback over a completeness rule is a worse failure than the one the rule prevents | §5.2 | **B, E** |
| 2 | **`find_missing_prevention_upstream(session, factor_set_id)` is named in §5.2** because it has two callers in two layers. The query lives in `db/repository.py`; `admin/factor_lifecycle.py`'s publish — **the copy the panel actually calls**, until §5.2's two implementations are unified — imports it and raises `LifecycleError` instead. Two copies of a rule drift, and the copy that stops matching is the one nobody notices; the same reasoning `db/types.PREVENTION_CODE` was given one home for | §5.2 | **B, E** |

> **No fixture, request or response shape changes.** A and C and D have nothing to do. The one thing E should know is the new refusal message, which names the tuples — "something is incomplete" would leave a staff member to find it among roughly 270 rows.
>
> **Recorded in `architecture.md` §10 rather than here: O-2 is O-7 again, in the constant dimension.** `cost`'s formula carries `const_FOOD_VALUE_PER_KG`, a constant is bound once per formula and has no destination to vary by, so a prevented line carries the full food value and `net_benefit.cost` nets it to zero. Harmless only while O-2 leaves the constant at zero — and **no test would catch it**, because the O-7 fixture check asserts `upstream` rather than the line value, deliberately (`mass`'s formula is `qty_kg`, so a prevented line must still weigh what it weighs). The fix needs no schema change: model the food's value as an upstream factor, which is what it is and which varies by `(sector, food_category)` as a single constant cannot, and v1.8's column offsets it automatically.
>
> **Still open after this revision.** **O-1** remains the hard blocker. **O-2 is now the one to read carefully before answering** — see above. O-7 stays closed. Carried forward unchanged: `landfill_diverted` as a real `metric` row, the container-preset input, `gwpHorizon`'s missing control, and the positive/negative colour pair.

### v1.8 — 2026-08-09 (O-7 closes, **affects A, B and E**)

**One change, and it is the one defect in this system that made the client's headline message wrong.** `architecture.md` §4.1 has said since the first revision that the special destination `prevention` has "factors are all zero — a 100% offset", and that this is what stops `net_benefit` being inflated by simply assuming less waste. **The data model could not express it.** `factor_upstream` was keyed on `(sector, food_category, metric)` and could not see the destination, so a line moved to `prevention` kept the entry's *full upstream factor* and only the downstream delta reached the net benefit. Measured on the canonical fixtures, 800 kg of `not_harvested` moved to `prevention` yielded `net_benefit.co2e` of **96.000** where a true offset yields **456.000** — 78.9% of the benefit missing, always in the same direction, on exactly the number the client's "wasting less" story is built from.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`factor_upstream` gains a nullable `destination_id`.** NULL means "applies to every destination for this `(sector, food_category, metric)`" — the same pattern `factor_downstream.food_category_id` already uses — so the lookup order becomes **exact destination, then the NULL row, then zero**, structurally identical to the one §4.1's `downstream()` already implements. Every upstream row written before this revision keeps meaning exactly what it meant. **The same NULL trap applies and is closed the same way**: MySQL compares NULLs as distinct inside a UNIQUE key, so `uq_factor_upstream` alone permits unlimited duplicate generic rows; a unique functional index over `COALESCE(destination_id, 0)` is what actually enforces it. Third appearance of the defect B first found on `factor_downstream` | §2.1, §2.2 | **A, B, E** |
| 2 | **`FactorBundle.upstream()` gains a `destination` parameter** — `upstream(sector, food_cat, destination, metric)` — with the three-step fallback documented on it. §10.2's `bundle.json` gains a `destination` key on every `upstream[]` row, `null` for the generic case, and `validate()`'s upstream check gains a destination resolution. §6.3's export carries the key for the same reason: both are built from `build_bundle_data` | §4.1, §6.3, §10.2 | **A, B** |
| 3 | **The rejected alternative, recorded because it was the one this document's O-7 section previously recommended.** A `prevented` line variable derived from `destination_group.is_waste`, with the default formula becoming `qty_kg * (upstream * (1 - prevented) + downstream)`, would have added a fifth line variable to §4.3 and required `admin/expressions.py`'s `BASE_VARIABLES` to gain a member in lockstep with the engine's evaluator — **two whitelists, written by two people, that must agree or the panel accepts formulas the engine rejects.** The schema option leaves `line_value = f(qty_kg, upstream, downstream, const_*)` with its exact shape: only the value bound to `upstream` changes, because the destination is resolved in the *lookup*. §4.3, the formula language and `BASE_VARIABLES` are **untouched by this revision** | §4.3 (unchanged, deliberately) | **A, E** |

> **Both fixtures that encoded the old answer have been corrected**, per §0's process. `factors.json` gains a `destination` on every upstream row plus a `prevention` row at zero for each `(sector, food_category, metric)` that has a general row; `calculate_response.json` was **regenerated from the published factors** rather than hand-edited, and the only values that moved are the ones this change causes — entry 2's alternative scenario and the totals and net benefits above it. `net_benefit.co2e` for that entry is now **456.000**. `calculate_request.json`, `taxonomy.json`, `stats.json` and the seven error bodies are untouched.
>
> **What this does not change.** No request shape, no response shape, no front-end module signature. C and D have nothing to do. The one thing worth their attention is that the canonical response fixture's numbers moved, so a hard-coded expectation taken from it will need refreshing.
>
> **Still open after this revision.** **O-1** (real emissions factors) remains the hard blocker and everything still runs on mock factors, so the placeholder banner stays mandatory. **O-7 is closed** — see `architecture.md` §10, which records what was done and what was rejected. Carried forward unchanged: `landfill_diverted` as a real `metric` row, the container-preset input, `gwpHorizon`'s missing control, and the positive/negative colour pair. New and small: nothing refuses a general upstream row created without a matching `prevention` row, which is how this item could come back one tuple at a time; the fixtures are held to it by a test, the panel is not.

### v1.7 — 2026-08-09 (the change that was made without a row, **affects B, C, D and E**)

**Change 1 is a §2.1 and §6.1 ruling that has been in the code and the fixtures since 2026-08-09 and in this change log nowhere.** §0's process is document, notify, fixture — and the change log *is* the notify step. A rule that only exists in a commit message has been applied to `tests/fixtures/taxonomy.json` and `admin/seed.py` without being announced to the two people who consume them, and v1.6's own closing note then asserted the opposite, that §1–§6 were untouched and the fixtures were clean. **A false "nothing changed" is worse than no note**: it tells a reader not to look.

The rest are the four defects and three omissions the whole-branch review turned up in `web/`. None changes a wire shape.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`display_unit` is a presentation variant of `unit` at the same scale, never a different scale, and nothing anywhere converts between the two.** `tests/fixtures/taxonomy.json` and `admin/seed.py` both carried `t CO2e` against a `unit` of `kg CO2e`, `kL` against `L`, and `t` against `kg`. **§7.6.1 is what makes that unfixable rather than merely wrong**: the front end performs no arithmetic on an API figure, so there is no layer that could divide a `kg CO2e` total by 1,000 on its way to a `t CO2e` label — the number is relabelled and every greenhouse-gas figure on the page reads a thousand times too small. It was live: C's results table rendered "3,993 t CO2e" and "1,530,000 kL" one section below the same two figures labelled correctly. The column is for a typographic variant — `kg CO₂e` against `kg CO2e` — which is how §6.1's example is written, with the two identical. **If a metric should be reported in tonnes, that is the metric's `unit` and the formula produces tonnes**: scale is a property of a formula, which is data (§2.1), not of a label. Corrected in **both** the fixture and the seed, and the seed is the half that matters, because it is what ships to the database. Ruled in §2.1 and §6.1 on 2026-08-09; **recorded here, which is the step that was missed** | §2.1, §6.1, §10 | **B, D, E** |
| 2 | **§7.2 gains `entryResultsFrom(entries, response)`, the one export §7 still did not name.** It is imported by `calculator.js` and its output is read by `results.js`, so it is already a cross-module call — which is the definition §0 gives of what this document governs. Documented with the distinction a consumer has to get right: cross-entry figures come from `result.totals`, per-entry figures from `result.entry_results`, and never a sum over the latter (§7.6.1) | §7.2 | **C, D, E** |
| 3 | **`improvement.js` interpolated `destination.code` into `for="…"` and `id="…"` through neither `escapeHtml` nor `slug`** — the only unescaped interpolation left on the branch. `destination.code` is `VARCHAR(64)` with no pattern constraint in `db/`, `api/` or `admin/`, and staff edit it through §8.1's generic CRUD, so a code containing a double quote breaks the attribute on a **public** page. Fixed with `slug`, which `calculator.js` already uses for the identical case. Recorded rather than fixed quietly because it names a standing hazard: **§7.3a's escaping guarantee holds only for values that pass through `view.js`**, and a taxonomy `code` reads as safe while being staff-authored content on a public page, exactly like the `formula.expression` §7.3a already calls out | §7.3a | **C, D, E** |
| 4 | **§7.6 rule 3's 375px baseline was being met at 375px and broken between 481px and 849px.** `.results-page`'s −80px bleed sat outside every media query while its `.wide` counterpart existed only at ≥850px and its reset only at ≤480px, so in the band between them the results page was pulled 80px past a container with 20px of padding: 60px off the left edge, unreachable in LTR, plus horizontal body scroll. **A tablet is the likeliest non-desktop demo device.** Written into the section because "mobile-first, baseline 375px" reads as a floor and is not one — a layout can pass at the baseline and at desktop and fail in between, and only a rule that says so will get the middle checked | §7.6 | **C, D** |
| 5 | **The stylesheet asked for seven weights the project does not have, and now asks for two.** Self-hosting (v1.6 change 10) replaced a five-weight CDN request with the two static faces the brand authorises — Geologica Bold and Kumbh Sans Regular — leaving 500, 600, 750, 800 and 900 with no face behind them. `font-synthesis: weight` kept them looking bold by synthesising; collapsing every declaration to 400 or 700 makes them bold **and** removes the synthesis, and `font-synthesis: none` is back. The brand-correct answer and the technically clean one were the same answer. **A weight that is not 400 or 700 now requires a font file to go with it** | §7.6 | **C, D, E** |
| 6 | **`RATE_LIMITED`'s 60-second timer cleared the deadline and not the banner**, so Calculate re-enabled underneath a paragraph still telling the user to wait 60 seconds — the button and the copy saying opposite things, with the copy the more believable of the two. The banner is now cleared with the deadline, but only if it is still the rate-limit banner: another failure may have replaced it inside the minute, and that message is about something the wait does not fix. §9.2's `BLOCKED` rule is the same shape and was already right; this was the one code with a timed recovery and no matching copy reset | §9, §7.3a | **B, C** |
| 7 | **`web/README.md` now says not to run a client demo on `?mock=1`.** §7.1 documents that mock mode re-derives only the mass figures and serves every impact figure from the fixture, cycled by entry index — so **a user who enters 5 kg is shown 4,449 kg CO2e**. That is correct behaviour for a fixture server and a catastrophic thing to put in front of the client, and the file the team actually opens said nothing about it. The instruction is to demo against the real API with the mock **factor set**: the figures are still placeholders, but they are placeholders the engine computed from what was entered, and the mandatory banner (§7.6.2) says so on screen | §7.1 | **all** |

> **Change 1 is the only one that touches a fixture, and the fixture was already changed** — this row is the announcement, not a new edit. Nothing in this revision alters a request or response shape.
>
> **Still open after this revision.** Unchanged: **O-1** (real emissions factors, the hard blocker), **O-7** (on A's critical path), `landfill_diverted` as a real `metric` row, the container-preset input (v1.6 change 4), and `gwpHorizon`, which still has no control. The positive/negative colour pair stays partly open on the same terms as v1.6. Recorded and **deliberately not fixed here**, because each is a refactor rather than a correction: `main.js` shows the taxonomy-failure screen's raw `error.message` because `publicError` is not exported from `calculator.js`; an all-`standard_mix` submission renders "no food category data was provided", which is untrue; and `.stage-fieldset.has-error` and `.destination-list.has-error` are dead as written, because `has-error` is only ever applied to `.form-field`.

### v1.6 — 2026-08-09 (§7 caught up with the front-end branch, **affects C, D and E**)

**Every row here is §7 describing code that no longer exists.** The `integrate/frontend` branch removed thirteen violations of §7.6 over six tasks, and §7 was written from a survey of the branch *before* them — so the section D and E are told to read before consuming C's modules has spent a week telling them that `formatNumber` has a rounding gap it does not have, that `results.js` hard-codes three metric columns it no longer names, and that `compareImprovement` takes one argument when it takes two. **A stale §7 is not a cosmetic problem: it is a second implementation.** §7.3a exists so D and E use C's one `escapeHtml` instead of each writing their own, and a reader who finds the described module and the real module disagreeing has no reason to trust either.

Two rows are not corrections. Row 9 settles an ambiguity nobody had ruled on, and row 10 adds a constraint the branch had already broken once.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **§7.1's mock-mode blockquote described the rewrite it was asking for as still owed.** It required the mock path to move to the `entries` / `totals` shape and to "serve the fixture as written"; it has moved, and it **cannot** serve the fixture as written — `mass` and the whole `totals` roll-up are functions of a request whose entry count a static file cannot know. Stated as it is, with the licence named as `mockRequest`'s alone, because the sentence a reader takes from a stale requirement is that mock mode is untrustworthy in ways it is not, and the sentence they need is which two figures are browser-derived | §7.1 | **C, D, E** |
| 2 | **§7.1's "known defect" on the fixture path was fixed and replaced by a different one that no version stated.** The absolute `fetch('/tests/fixtures/…')` is gone — the URL now resolves against the module's own URL, so it follows the page. What survives is structural and unfixable in JavaScript: a browser clamps `../` at the origin root, so **the document root must be an ancestor of both `web/` and `tests/`**, and FastAPI serving `web/` as the static root makes every mock call 404. Recorded with an owner (**B**, a dev-only mount) because all three of C, D and E develop in mock mode and would each rediscover it separately | §7.1 | **B, C, D, E** |
| 3 | **§7.2's key table is now exhaustive, and says so.** `state.alternative` and `state.compareAlternative` were on the object and in neither the table nor any reader: initialised, reset by `resetCalculator`, assigned `[]` by two functions in `calculator.js`, read by nothing. Removed. A key that is initialised and reset but never populated reads as a feature under construction, and the next person to need an alternative scenario would have wired theirs into a dead one rather than into `improvedAllocations`, which is where the live one is | §7.2 | **C** |
| 4 | **§7.2's `unitPreset` / `unitCount` requirement now states what building it costs**, because "not built" was hiding a decision. `toKg` returns kilograms at **three** decimal places and `calculator.js` validates `totalAmount` at **two**, so a preset whose `kg_per_unit` is not a whole number produces a total the form then refuses. Every `kg_per_unit` in `tests/fixtures/taxonomy.json` is integral and the column is `DECIMAL(12,4)`, so the collision is invisible on the fixture and certain on real data. **This is why `toKg` is still imported by nothing and was not wired up in this revision** — it needs a ruling on what a user may type, not a refactor | §7.2, §7.3 | **B, C** |
| 5 | **§7.2's fast-path exception claimed a drift wider than the one that exists.** The typed and re-rendered paths no longer disagree about server-supplied field errors — `updateLine` clears them, deliberately, because blanking or filling a row changes which lines the request carries and the server's line positions stop meaning what they meant. What remains is narrower and still real: on a negative amount `updateLine` marks only the row being typed in while `destinationRows` marks every negative row. An overstated warning and an understated one fail the same way — the reader stops believing the section | §7.2 | **C, D** |
| 6 | **§7.3 said `tonnes ? 1000 : 1` was duplicated at six sites. It is duplicated at none.** The last of them — `improvement.js`'s `lineKg`, the denominator of every allocation percentage — now calls `massToKg`, and the three sites that rounded a conversion to the API's three decimal places call `kgString`, which the section described as imported by nothing. §7.3's rule is that the front end's arithmetic can be audited in one file, and a rule observed at five of six sites is worth less than none, because a reader who checks one site concludes it holds. The section now states the auditable form of the rule: **a `*`, `/` or `.toFixed()` on a mass anywhere else in `web/` is a defect on sight** | §7.3 | **C, D** |
| 7 | **§7.3a's `formatNumber` JSDoc and its "known gap" described opposite behaviours, and both were wrong.** The signature said `{maximumFractionDigits: precision}` and the note beneath said a cost of `825.00` therefore renders as "825"; the function sets **both** bounds and has since 2026-08-09, and it clamps the digits to Intl's legal 0–20 because `display_precision` arrives from the database and an out-of-range value makes `toLocaleString` throw a `RangeError` that would take out the whole render rather than one figure. Added in its place is the precondition the calling modules actually depend on: `Number('')` is `0`, so a `\|\| 0` on an API figure makes absent, malformed and zero the same figure on screen, and every caller maps absent to `NaN` so that `formatNumber` prints "Not available" | §7.3a | **C, D, E** |
| 8 | **§7.3a's `results.js` and `calculator.js` notes were both to-do lists for work that is done.** The client-side aggregation layer, the hard-coded `CO₂e / Cost / Water` columns and the three hard-coded equivalence labels are gone; `aggregateResults` and `differenceData` no longer exist. So are all three of `calculator.js`'s silent field-binding failures — the `field` format, the index mismatch, and a third the section never named: `fieldErrorMap` stored the **envelope's** message against every field, so a correctly bound row would still have read "Request validation failed" and discarded the only prose that said what was wrong with that row. Replaced by what the modules now are, plus the two things a reader will otherwise re-litigate: why `mass` may be named in both modules without breaching §7.6.5, and why a bar width is not a figure | §7.3a | **C, D** |
| 9 | **Ruled: the arrow beside a comparison figure shows the direction of the impact, not the sign of the number.** An up arrow beside "1,104.0 kg CO2e saved" reads as "better" to one person and "went up" to another, and nothing in this document had ever said which. `net_benefit` is `current − alternative` (§3), so a positive net benefit is a saving, the impact fell, and the arrow points **down**. The ambiguity had a structural cause worth naming: `.value-negative` was carrying two different statements — "this change moved the wrong way" and "this quantity is below zero, because a destination offsets more than it costs" — so no single arrow could be right for both. They are now two sets of classes, tabulated in §7.3a, and `.value-negative` deliberately carries **no** arrow because `formatNumber` already prints the minus sign and a quantity is not a movement. §7.6.6 sends D to the same four classes so the statistics page does not grow a second convention | §7.3a, §7.6 | **C, D** |
| 10 | **New rule §7.6.7: no page may request an asset from a third-party host at runtime.** `styles.css` opened with an `@import` from `fonts.googleapis.com`, so every visitor's browser announced itself to Google before the first paint — on a calculator whose privacy position is §2.3's and whose statistics page says so in its own copy — and the first paint waited on a network the project does not control. The two brand faces were already in `admin/static/fonts/` and are now in `web/assets/fonts/` as well. **The rule is written down because §7.4 is the next place it would break:** that section tells D to return a Chart.js instance and says nothing about where Chart.js comes from, and the one-line CDN `<script>` is the documented way to add it | §7.6, §7.4 | **C, D, E** |
| 11 | **§7.3a's `main.js` "known defect" was fixed in the first task of the branch, and the fix is now a requirement rather than an implementation detail.** `main.focus()` after every `setState` ejected a keyboard user from the sector radio group, so step 1 could not be passed without a mouse. Both halves of the replacement are stated, because the obvious half is not sufficient: focus moves to `<main>` on a **step transition** and returns to the element that had it, by `id`, on a **same-step** re-render — merely scoping the focus call leaves the user on `<body>`, which is worse than where they started | §7.3a | **C** |
| 12 | **`compareImprovement(state)` takes two arguments.** The second is `calculator.js`'s `publicError` — §9's code-to-copy map — passed in rather than imported, because `calculator.js` already imports this module and the import back would be a cycle. Without it the improvement panel showed raw backend prose for the codes the main flow words carefully, and §9.1 rules that a public `FORMULA_ERROR` never echoes the expression or its location. A wrong arity in a section whose purpose is to be called from D's and E's code is the cheapest possible defect to introduce and among the more annoying to diagnose | §7.3a | **C, D, E** |
| 13 | **`improvementValidation`'s tolerance was a percentage-point tolerance where §6.2's is an absolute 0.010 kg**, and §7.3a described neither. 0.01 percentage points is 0.15 kg on a 1,500 kg entry — fifteen times the limit — so the panel enabled Compare on a submission the server then refused with a 400, **for the whole submission**, after the user had left the screen with the numbers on it. It now sums the lines that will actually be sent. Two consequences are recorded with it: the seeded allocation was itself invalid under the corrected check (52.17 + 34.78 + 13.04 = 99.99%), so the rounding remainder goes to the largest share; and `improvedLines` anchors on the entry's **allocated** current mass rather than the total typed at step 3, because step 4 deliberately permits allocating less than the total and anchoring on the typed total made every under-allocated entry send an alternative heavier than its current scenario | §7.3a | **B, C** |

> **Corrected in v1.7 — the two sentences that stood here were false.** They read "Nothing in §1–§6 or §8–§10 changed" and "`tests/fixtures/` is untouched", and both were written from this revision's own edits rather than from the branch's. The `display_unit` same-scale ruling had already changed **§2.1**, **§6.1**, `tests/fixtures/taxonomy.json` and `admin/seed.py`, with no change-log row anywhere. See **v1.7 change 1**, which is that row. What is true of *this* revision's own edits: they touch §7 only, plus §7.6's new rule 7, and moved no request or response shape.
>
> **Still open after this revision.** **O-1** (real emissions factors) remains the hard blocker and **O-7** is still on A's critical path. Carried forward and unchanged: `landfill_diverted` as a real `metric` row (the client's). The **positive/negative semantic colour pair**, carried since v1.2, is *partly* closed — change 9 fixes the four classes, their arrows and their brand colours for the calculator page, and D can adopt them as they stand — but whether the client wants Kale-and-Beetroot for better-and-worse, rather than a green-and-red pair the brand does not contain, has still not been asked. New and unclosed: **the container-preset input (change 4)**, which needs a ruling on the two-decimal rule before `toKg` can be wired to anything, and **`gwpHorizon`**, which still has no control.

### v1.5 — 2026-08-09 (from the whole-branch review, **affects B, C, D and E**)

Two of these are privacy defects, and **both are of a kind that is invisible from inside any one module.** Each is produced by two correct-looking halves meeting: a validator with no rule about `prevention` next to an aggregation that filters *for* the scenario `prevention` would land in; a suppression threshold that protects every bucket except the bucket suppression creates, in three breakdowns that are all drawn from the same entries. Neither shows up in a review of the file it lives in, which is the argument for reviewing a branch end to end before it merges rather than each task as it lands.

The third closes v1.4's one open decision. The remainder are the same class v1.4 set out to close — a convention only the implementer knew — and finding four more of them one revision later is the honest measure of how large that class was.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`prevention` is now refused in a `current` scenario, and §6.2's validation table says so.** Nothing on the server enforced it — only C's UI, which never offers it there. A hand-rolled `POST /calculate` persisted a current-scenario `prevention` line, and §5.4 selects `scenario = 'current'`, so it became a bucket in the **public** `by_destination` chart: the destination for waste that by construction did not happen, counted as real waste, on the page whose entire design problem is not overclaiming. §5.4's scenario predicate is the other half of this and structurally cannot catch it — it excludes the *alternative* scenario, and the line is not in the alternative scenario. `tests/api/test_fixture_consistency.py` asserts this of the fixture, which is what made it look covered; a fixture constrains the fixture. The rule sits on the current scenario only, so the failure locates at `entries[i].current` and the alternative keeps the destination that is its whole purpose. `PREVENTION_CODE` moved to `db/types.py` with `admin/taxonomy_rules` re-exporting it, because `api/` may not import `admin/` — v1.3's ruling on `db/detection.py`, for the same reason | §6.2, §2.1 | **B, C** |
| 2 | **`submission.token` joins §5.5's `REDACTED_FIELDS`, ahead of the screen that would leak it.** §8.2 specifies `/admin/submissions` as a record-level moderation view that sets `excluded_from_public`. The moment it is built on `AuditedModelView`, `write_audit` snapshots the whole row and copies the **live session token** into `audit_log` — readable by every staff member, never expiring, and out of reach of `expire_tokens`, which nulls the column on `submission` and knows nothing about copies. **§2.3's guarantee is that the linkage is severed after an hour**, and a copy in an audit row makes that false for every moderated submission, permanently. One line now; a privacy regression later that nobody would think to look for, in a table whose whole purpose is to be trusted | §5.5 | **B, E** |
| 3 | **v1.4's open item on `other` is decided, in favour of privacy: `other` is subject to the threshold like any other bucket.** While it is below the threshold it absorbs the **smallest visible** bucket, repeatedly; if merging everything still cannot clear it, the breakdown publishes **no buckets at all** and `total_calculations` stands alone. What made this urgent was not the single-bucket case v1.4 described but its cross-breakdown form: `by_sector`'s `other` and `by_food_category`'s `other` are formed from the same entries, so `tests/fixtures/stats.json` showed both carrying `count: 4, total_kg: "3210.750"` — identical, therefore the same four entries, therefore joinable at a glance. At `count: 1` that is one user's exact tonnage published three times with only the label hidden. **The shares property v1.4 worried about survives**: `other` stays an ordinary bucket carrying every suppressed entry, so the pre-suppression denominator is untouched and §6.4's "shares sum to 1" holds. The fixture is regenerated through `_bucketise` rather than levelled by hand, and its two `other` buckets are now 118 and 88 | §5.4, §6.4 | **B, D** |
| 4 | **§2.1's `destination` blockquote was the last site still claiming `prevention` has "all factors set to zero".** v1.4 corrected `architecture.md` §4.1 and §6.2 and left this one — in the **normative schema section**, which is where a new reader meets the word first, and which therefore outranks both of the sites that were fixed. It now states what is true (downstream zero, upstream not and structurally cannot be), points at O-7, and lists the three rules stated in terms of this code so that a future editor can see what removing it would break | §2.1 | **all** |
| 5 | **§9 defines two `details` shapes; the code emitted three, and one of them had `issue` and `message` inverted.** `api/router.py`'s inline-bundle check emitted `{field, issue}` with `FactorBundle.validate()`'s human-readable prose (§4.1) in `issue` — so a consumer told to branch on `issue` would branch on a sentence that changes whenever the engine's wording changes, and would find no `message` to display. **The code was corrected to the contract rather than the contract widened to the code**: two shapes a consumer can predict from `code` is a contract, three it must sniff at runtime is not. §9 now states the rule as a table and says plainly that no presence checks are needed | §9 | **B, C** |
| 6 | **§10's `taxonomy.json` row described a file that does not exist.** It said "nine MfE destinations across the `prevention`, `reuse`, `recycling` and `disposal` groups"; the fixture has **fourteen** destinations across **three** groups, `prevention` is a destination in `reuse` rather than a group, and `recycling` is not a group code — it is `recycle_recovery`. v1.4 change 5 claimed §10 had been rewritten from the directory, and this row had not been. Recorded rather than quietly fixed because it is the second-order failure worth naming: a section whose stated purpose is to describe what is on disk is the section a reader will not verify | §10 | **C, D** |
| 7 | **§6.2 states what `food_category: null` and `"standard_mix"` do to the duplicate check: they are distinct, both are accepted, and one supply-chain point is then counted twice.** The field table says "Null is treated as `standard_mix`" — true of the *factor lookup* and of nothing else. Keeping them distinct is deliberate and follows from §5.4, which must be able to tell "did not break it down" from "chose the mixed figure", but the asymmetry reads as a bug from §6.2 alone and a front end should send one or the other consistently | §6.2 | **B, C** |

> **Still open after this revision.** **O-1** (real emissions factors) remains the hard blocker. **O-7 is still the only item on A's critical path** and is still the client's to settle. Carried forward unchanged: `landfill_diverted` as a real `metric` row, and the positive/negative semantic colour pair that C and D both need. v1.4's `other` question (change 3) is **closed**.

### v1.4 — 2026-08-09 (the conventions nobody wrote down, **affects everybody**)

Nothing here is a new requirement. Every row is a rule the system already had — instantiated in a fixture, emitted by a handler, relied on by a test — and that this document did not state, so a second implementer had no way to arrive at it except by reading someone else's code. **That is the whole class of defect this revision closes**, and it is the one a five-way parallel split produces most reliably: the shape of a thing gets contracted, the *convention inside the shape* does not, and the convention is what the next person has to reproduce exactly.

Three of them (1, 3, 4) were already load-bearing on somebody's unwritten work. One (9) is not closed here and must not be closed here — it changes what the client is told the calculator measures.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **`GET /factors` now actually emits `source_note` and `data_quality`.** §6.3 has required them since v1.2 and `db/repository.py`'s `build_bundle_data` never selected them, so for two revisions the export published every value with its provenance stripped — the exact combination v1.1 added the columns to prevent, since it removes the defence and keeps the exposure. `build_bundle_data` is the single projection that §6.3 and §10.2's `bundle.json` are both built from, which is why one six-line fix serves both and why the omission hit both. The fixture was written to the contract and its test parked on a **strict** `xfail`, so the gap was a tracked failure rather than a silent one and the marker came off with the fix — the pattern worth repeating whenever a fixture has to lead the code | §6.3 | **B, D** |
| 2 | **§6.3's equivalence rows gain `name` and `sort_order`.** Both were emitted from the first commit and shown in no version of this section. The contract moved rather than the code because §10.2 **requires** both on a bundle equivalence row and the same projection produces both surfaces: removing them here means a second projection whose only job is to hide two harmless fields, and a second projection is a second thing to keep in step. `name` is the short label (`Kilometres driven`) — `label_template` is a whole sentence, so a consumer building a heading or a CSV column has nothing else, and hard-coding it is what §7.3a already rules out | §6.3 | **B, D** |
| 3 | **§3 gains rule 5: the `EquivalenceResult.label` interpolation format.** `{value}` renders as whole units, `ROUND_HALF_UP`, comma thousands separator — `Equivalent to driving 18,597 km`. **This was a convention `tests/fixtures/calculate_response.json` invented** to match §6.2's own samples, defined in neither §2.2, §4.3 nor §6.2, and A has to reproduce it in the engine byte for byte or every equivalence on the page is wrong in a way no test in this repository could see: `_assert_shape` compares JSON types and key sets, and `Equivalent to driving 18596.8200000000 km` is a well-formed string of the right type under the right key. Recording it in a test docstring was **not** enough — §0's rule is document, notify, fixture, all three. The rounding mode is the part that had to be written out rather than left to a default: the fixture test used `quantize(Decimal("1"))`, whose context rounds half to **even**, so it would have pinned the wrong rule the first time a value landed on a half. No value in the set does, so both modes passed; the test now names `ROUND_HALF_UP` | §3, §2.2 | **A, B, C, D** |
| 4 | **§9 gains the five codes `api/errors.py` has always emitted and this section never listed** — `NOT_FOUND` (404), `METHOD_NOT_ALLOWED` (405), `INTERNAL_ERROR` (500), `ENGINE_UNAVAILABLE` (503) and the residual `HTTP_ERROR` — **and the third key on a `details` entry.** §7.1 tells C to branch on `body.error.code`; a code from a closed set is one she can handle and a code from nowhere lands in whatever her default branch does, which is the difference between "the calculator is under maintenance" and a blank panel. `details[]` carries `field`, `issue` **and** `message`, the last of which `errors/validation_error.json` has always had and the sample never showed — a front end built from the sample alone renders the envelope's one generic message against every highlighted row and discards the only text that says what is wrong with that row. Also written down rather than fixed: `HTTP_ERROR` now names two different events, §7.1's client-side "response was not JSON" and this residual server code. They stay sharing a name because the front end's response to both is identical, and a reader who finds one string in two sections should not have to guess which is the mistake | §9, §7.1 | **B, C, D** |
| 5 | **§10 rewritten from the directory that now exists**, and gains §10.0, which names what enforces it. v1.2's §10 described two divergent sets on two unmerged branches and none in the tree; there is now one canonical set of thirteen files here. The new subsection separates the two kinds of check and says why neither substitutes for the other: `test_fixture_consistency.py` holds the fixtures against each other, against the arithmetic and against `admin/seed.py` without touching HTTP, while `test_api.py` holds them against real responses from the real app. The shape check cannot prove a number — which is precisely how a `stats.json` of three empty arrays survived two revisions while giving D nothing to build a page from. Stated as a standing rule, because change 3 is an instance of it: **anything whose correctness lives inside a string is invisible to `_assert_shape`** and needs an assertion, a contract line, or both | §10 | **all** |
| 6 | **§4.1 and §4.2 name the modules: `engine/bundle.py` and `engine/calculate.py`.** No version of this document said where `FactorBundle` or `calculate()` live, so both external callers — `db/repository.py`'s bundle factory and `api/engine_adapter.py` — searched two candidates each. A search is not a contract: it lets a layout this document does not describe work in the API and fail in the golden suite, which imports both the documented way. The `from engine import calculate` half was actively harmful — if `engine/calculate.py` existed but exported the function under another name it bound the *module object*, turning a start-up `ImportError` that names the missing thing into `TypeError: 'module' object is not callable` at the first public calculation. Both fallbacks are gone | §4.1, §4.2 | **A, B** |
| 7 | **§5.2: only a `published` factor set is cached.** The per-set partitioning stands and its reasoning is unchanged, but caching the draft as well satisfied that sentence literally while defeating the path it exists to serve. §8.1's CRUD screens write factor rows directly and have no reason to call `invalidate_factor_bundle` — only `publish_factor_set` and `rollback_to` do — so the first dry run of a draft pinned its numbers for the life of the process: a staff member edits a factor, re-runs the dry run, sees the old figure, and cannot distinguish that from a formula that ignores the column they just changed. That is the confusion `factor_source` was added to prevent, arriving by another route. Not caching is one branch in one function; the alternative is an invalidation hook in all eleven §8.1 views that whoever adds the twelfth has to remember | §5.2 | **B, E** |
| 8 | **§5.4 records, as open and as the client's, whether `other` is suppressed against itself.** A single sub-threshold bucket is republished verbatim under a new name — `count: 1` with its exact `total_kg` — which is a public statement that exactly one such calculation exists, with only its destination hidden. Raised as `docs/ToB_v2.0.md` S6 and required by no version of this contract. It is recorded rather than implemented because **each available fix breaks something else this document promises**: dropping the bucket makes §6.4's "shares sum to 1" false, since the denominator is computed before suppression; re-normalising inflates every remaining share by the suppressed mass, which is the overclaiming the merge-rather-than-drop rule exists to prevent; and folding it into the largest bucket hides a small number inside a big one. The trade is a real disclosure against a real distortion of the client's own chart, so it is not B's to take in a query or D's to take in a legend | §5.4 | **B, D** |
| 9 | **`docs/architecture.md` gains O-7: `prevention` is not the 100% offset §4.1 claims, and §4.1 now says so at the point of the claim.** `upstream` is keyed on `(sector, food_category, metric)` and cannot see the destination — §4.3's line variables carry neither the destination nor its group — so a line moved to `prevention` keeps its entry's full upstream factor, while `architecture.md` §4.1 describes `prevention` as all-zero and matching ReFED. Measured on the fixture: 800 kg `not_harvested` → `prevention` yields `net_benefit.co2e` of 96.0, the downstream delta only, where ReFED would also avoid 800 × 0.45 = 360.0. **79% of the benefit is missing, and upstream is the larger term for most categories** — so the failure is one-directional and lands on exactly the number the client's "wasting less" story is built from. §6.2's anti-inflation argument survives untouched: mass conservation is a property of the request. **Not resolved here.** O-7 states three options and who decides; the cheapest changes what the client is told the calculator measures, so it is the client's, and it must be settled before A writes the engine because the golden suite bakes in whichever answer is chosen | `architecture.md` §4.1, §10 | **A, B, E** |
| 10 | `write_audit`'s serialiser now emits UTC with a `Z` designator for every `datetime`, per §1.3. It appended `Z` to a naive value and left an aware one carrying `+00:00`, so one column serialised two ways depending on whether the object had round-tripped through MySQL — and a non-UTC aware value kept its own offset while still claiming compliance. Same normalisation as `api/serialization.wire()`, reimplemented rather than imported because `db/` may not import `api/` | §5.5, §1.3 | **B, E** |

> **Still open after this revision.** **O-1 remains the hard blocker** and everything still runs on mock factors, so the placeholder banner stays mandatory. **O-7 (change 9) is new and is on A's critical path** — it is the only item here that cannot be deferred past the start of engine work. Carried forward unchanged from v1.2: `landfill_diverted` as a real `metric` row (the client's), and the positive/negative semantic colour pair, which C and D both need and neither has written down. Added by this revision: whether `other` is suppressed against itself (change 8, the client's) — **closed in v1.5 change 3**, in favour of privacy, once the whole-branch review showed the cross-breakdown join that made it more than a single-bucket curiosity.

### v1.3 — 2026-08-09 (the blocklist reaches the API, **affects B, C, D and E**)

Two of the open items v1.2 recorded are now closed, and closed the same way, by the same change: the blocklist is applied to `/api/v1/` and the detection helpers it needed moved into a layer both callers can import. Nothing about a request or response shape changed — **§9.2's `BLOCKED` envelope is unchanged and is now actually emitted**, which is the part C and D care about.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **Closed the v1.2 open item on `looks_automated`, `RequestRate` and `_client_ip`.** They are in `db/detection.py`; `admin/detection.py` is a re-export; `admin/protection.py` keeps `_client_ip` as an alias for `db.detection.client_ip`, which is the same object rather than a second copy. §8.3's recommendation was the one implemented, and its reasoning was the deciding one: two copies of a detection rule drift, and the copy that stops matching is the one nobody notices. Note that the stated rationale was slightly wrong in one particular — `db/detection.py` is not standard-library-only at runtime, because it imports `db.blocklist` for address normalisation. What actually mattered, and what holds, is that it needs nothing from `admin/` | §8.3 | **B, E** |
| 2 | **Closed v1.2's item #15: whether an in-memory rate-limit counter may be keyed on a raw address.** It may not. Both layers now key on §2.3's HMAC fingerprint, and `api/rate_limit.py` counts with the same `db.detection.RequestRate` the panel uses rather than being a third implementation. §6.5 sets its bar at persistence and a raw address in a process-memory dict cleared that bar, but the dict outlives the request that filled it, so the address is one this system holds — and two layers applying different privacy standards to the same data was never a defensible position for a calculator whose selling point is that it stores nothing about the visitor | §6.5, §2.3 | **B, E** |
| 3 | **§9.2's "checked before anything else" is now literally true, and is implemented as middleware rather than as a router dependency.** FastAPI solves a router's dependencies only after a request has matched a route, so a dead path under `/api/v1/` answered `404` while every live path answered `403` — which hands a blocked caller a working route scanner. Recorded in §9.2 because it is a requirement on the implementation, not a free choice | §9.2 | **B** |
| 4 | **Recorded the two deployment hazards the API inherits and cannot fix in process**, both of which make §6.5 and §2.3 silently do nothing: every caller arriving as a reverse proxy's address, and a deployment (`uvicorn --uds`) that gives the process no client address at all. Both are warned about at start-up or on first occurrence. The panel survives the first only because `_RATE_EXEMPT_PATHS` keeps its login handshake reachable; a public API has no login handshake, so there is no equivalent to build | §6.5 | **B, E** |
| 5 | **`SECRET_KEY` is required by the API, and must be the same value the panel uses.** Both derive the fingerprint key from it through the same `BLOCKLIST_INFO`; two different secrets mean a block made in the panel never matches at the API, with nothing raised on either side. `api/app.py` refuses to start without one rather than starting with a blocklist that does nothing | §2.3 | **B, E** |
| 6 | **Recorded that `looks_automated` is deliberately not applied to `/api/v1/`.** It is applied to `/admin` only. Scripting a public JSON API is a legitimate way to use it, and §6.3's CSV export exists to be fetched by a tool — refusing `curl` there would refuse a use this contract invites. Written down because a reader who found the shared module and not this line would reasonably assume the omission was an oversight | §8.3, §6.5 | **B, E** |

### v1.2 — 2026-08-09 (the merge of the two contract lines, **affects everybody**)

Two documents became one. Most of the work was mechanical; the twenty-three corrections below were not. **Most of them are defects that were already in the document before the merge** — the merge is what made them visible, by putting statements next to the statements they contradict. **Three (1, 2, 5) were live blockers on A's and B's integration work**: a section of this document that had gone two revisions without being updated, and that two people were about to code against.

A smaller group — 6's scenario filter, 20 and 21 — are defects the corrections themselves created, and they share one cause worth stating once, because it will recur every time this document is tightened. **Making a vague section specific makes every remaining omission in it load-bearing in a way it was not before.** Pre-merge §5.4 named no table, no column and no join, so it could not be implemented wrongly from §5.4 alone; the rewrite named all three and left out a scenario predicate, a nullable column's fallback, and one join. Each omission then read as a followable instruction for the wrong query. When you make a section precise, re-read it against the cases it now appears to answer.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **The two forks are merged.** v0.10–v0.13 (`admin_panel`) and v1.0–v1.1 (`docs/contract-v1.0`) both revised the same document in parallel for two days, and both were live and unmarked. Nothing was dropped from either side except §6.2's placeholder note, which said only "this file has not been reconciled with the other fork yet" and which this commit falsifies. The cost of the fork is concrete rather than theoretical: `admin/calc_client.py` and `admin/dryrun_views.py` were written on the `admin_panel` branch **against the other fork's §6.2**, because that was the only place the shape they needed existed. Code was being written against a contract that was not in the tree it was being written in | all | **all** |
| 2 | **§3, §4.2 and §5.3 now describe a multi-entry calculation, which is what §6.2 has described since v1.0.** They did not. `CalculationRequest` was `current`/`alternative` with `sector_code` on `ScenarioInput`; `calculate()` returned one `ScenarioResult` pair; `upsert_submission` took that request. Meanwhile §2.3 had grown `submission_entry` and §6.2 sent `entries[]` and returned `totals`. v1.0's own change-log entry is marked "Affects **A, B and C**" and only two of the three sections were ever changed. **This is what "A and B are the most tightly coupled" costs when it goes wrong:** A builds an engine that structurally cannot produce `totals`, B has to invent the cross-entry aggregation signature with no contract to code against, and the two inventions meet for the first time at integration — where the golden suite cannot adjudicate, because the golden suite tests the engine A built | §3, §4.2, §5.3 | **A, B** |
| 3 | **`totals` is computed by the engine, not summed in the API adapter, and §4.2 now says so with the reasoning.** An adapter in `api/` that adds per-entry metric totals together is a *second* impact-calculation site — the same defect as the browser doing it, differing only in which process runs the arithmetic. It would put the page's headline figure beyond the reach of the golden suite, which exercises `calculate()` and nothing above it, and it would make a non-additive roll-up a code change in `api/` — "metrics are data, not code" broken in the layer least likely to be reviewed for it. The rules for what the roll-up does with an entry that has no alternative (its current figures count on both sides, so its net benefit is zero and mass is conserved) are written out, because that is the one behaviour §6.2 states in prose and nowhere in a signature. §4.2 also names the one §6.2 response field that has **no** §3 counterpart and cannot have one — `factor_source`, which the engine has no way to know because it is pure over a bundle and cannot see where that bundle came from. The API layer supplies it from the branch it took when resolving the bundle. Without that sentence the "no arithmetic" rule reads as forbidding B from populating a field §6.2 requires | §3, §4.2 | **A, B** |
| 4 | **§6.2 now requires an entry's two scenarios to describe the same mass, to within 0.010 kg. Nothing enforced it before, in any version.** The dual-scenario design rests on the rule: `architecture.md` §4.1 states that the `prevention` destination — all factors zero — exists so that "wasting less" is expressed by *moving* mass to it rather than by sending less of it, precisely so `net_benefit` cannot be inflated by assuming away the waste. **An implementer building from §6.2 alone permitted exactly what `prevention` was designed to prevent**, and nothing downstream would have surfaced it: an alternative that simply drops a 1,200 kg landfill line yields a large fictitious `net_benefit`, `totals.total_kg` reports the current scenario's mass only so the two figures are never both on the page, and the golden suite cannot catch it because it tests the engine against a fixed request and this is a property of the *request*. The tolerance is **absolute and derived, not chosen**: the front end rounds each alternative line independently to 3 dp (≤ 0.0005 kg each) and §6.2 already caps a scenario at 20 lines, bounding drift at 0.010 kg at any tonnage. A relative tolerance is looser than the defect at 5,000 t and tighter than the unavoidable rounding at 2 kg | §3, §6.2 | **B, C** |
| 5 | **§6.2's dry-run row named two of the three submission tables.** It said no `submission` or `submission_line` row is written; `submission_entry` was added between that sentence and now. An implementer following it literally writes **orphan `submission_entry` rows on every staff dry run** — and §5.4 aggregates `by_sector` and `by_food_category` over exactly that table, so the pollution lands in the public statistics `X-Dry-Run` exists to protect, while `total_calculations` stays flat and conceals it. Staff run dozens of calculations while tuning one formula | §6.2 | **B, E** |
| 6 | **§5.4 now carries the entry-aggregation rule that §2.3 attributes to it, and the scenario filter that nothing has ever stated.** §2.3's `submission_line` note asserts "statistics aggregate over entries, not submissions (§5.4)" — and §5.4's `get_public_stats` and `StatsBucket` said nothing about entries. B implements §5.4 from §5.4. Joining `by_sector` to `submission` instead counts a multi-stage food business once, as whichever stage it entered first: the query returns a plausible number, nothing fails, and the population the calculator is most useful to is the one it silently mis-describes. **The second half is worse and no version of this document has ever said it:** `submission_line.scenario` is `ENUM('current','alternative')` and both scenarios live in one table, so a `by_destination` group-by with no scenario predicate counts hypothetical lines as real waste — **`prevention`, the destination for waste that did not happen, becomes a bucket in the public chart**, every `total_kg` roughly doubles, and §6.4's "the cumulative total entered into this tool" is false on its face. All three breakdowns and `total_kg` read `scenario = 'current'` only. Also states the consequence D has to write copy around — `total_calculations` counts submissions while every bucket `count` counts entries, so the two figures on the statistics page differ by design and must not be presented as a breakdown of one another | §5.4, §6.4 | **B, D** |
| 7 | **§7 replaced with the eleven modules it now names — the nine of C's that are built, transcribed from her branch, plus D's two, which are still specifications: `charts.js` and `news.js` do not exist and Chart.js appears nowhere in the tree.** §7 named five, described two of those inaccurately, and omitted six — including `view.js`, which holds the escaping and formatting primitives D and E would each otherwise reimplement, and whose single `escapeHtml` is the reason that branch is XSS-clean. Two shapes changed **to match her code rather than the reverse**: a line is `{id, destination, qtyInput}`, where `id` survives a full re-render (`render()` replaces `main.innerHTML`) and `qtyInput` keeps the raw string so nothing rounds until it is sent. `unitPreset` and `unitCount` stay in this document as unmet requirements — the container-preset input was never built, `toKg` is imported by nothing — as does the `gwp_horizon` control | §7 | **C, D, E** |
| 8 | **Settled: the cross-entry destination breakdown is rendered per entry, from `entries[]`.** C's results page builds one combined destination tab by adding `by_destination[].value` across entries in JavaScript — the last §7.6 violation that could not be removed by reading a different field. The ruling costs no new field, no engine change and nothing on A's critical path, and it is the more truthful rendering: the same destination under two entries draws two different upstream factors and is genuinely two rows. Two front-end figures are **removed** rather than relocated, because no field exists to move them to — the percentage-change figure (defined in no version of this contract; `net_benefit` already carries it in the user's own units) and the `landfill_diverted` card (a metric with no row in the `metric` table, with the destination code hard-coded in JavaScript) | §6.2 | **A, C** |
| 9 | **§8.3's audit item is resolved, not open.** v0.13 recorded "an API-side block cannot audit itself today" as unresolved with B named as the decider. **Her branch had already resolved it** — `write_audit` and `_json_safe` sit in `db/repository.py`, where §5.5 has placed them since v0.3. There was never a decision to take, only a duplicate to remove, and leaving it open means the person who owns it goes looking for a choice that does not exist. The integration detail matters more than the bookkeeping: B's `_json_safe` recurses into nested dicts and lists and redacts at every level, E's `_scrub` handles top-level keys only — so a `password_hash` or `mfa_secret_enc` one level down inside a payload passes straight through E's copy into a table every staff member can read. **B's survives**; E's `date` handling folds in | §8.3 | **B, E** |
| 10 | **The submissions migration is `0008`, `down_revision = "0007"`.** `docs/ToB_v3.0.md` §1.3 says `0006` / `"0005"`, which was true when written; `0006` (comparison scenarios) and `0007` (`ip_block`) have landed since. Following it literally creates a **third Alembic head** — not a merge conflict but an ambiguous chain, and `upgrade heads` then fails partway through on a table that already exists. **MySQL DDL autocommits**, so there is nothing to roll back and the recovery is `DROP DATABASE`. Cheapest possible thing to get right; among the most expensive to get wrong, which is why it is in the contract rather than a task brief | §2.3, §8.3 | **B** |
| 11 | **§10 now records where the fixtures actually are.** This section calls them "the executable form of the contract", and **no branch a reader is likely to be standing on has any** — not `main`, not `admin_panel` (which owns this document), not `docs/contract-v1.0`. Two sets exist on two unmerged branches: B's twelve and C's ten, both in the pre-v1.0 flat shape, disagreeing with the contract and with each other. The `details[].field` split is the sharpest case — B's fixtures use Pydantic's dotted `current.0.qty_kg`, C's use the bracket form, v1.0 §9 ratified C's and extended it, so B's is the one that changes; until it does, her handlers and C's lookup keys can never bind. The canonical set lands **once**, in the v1.2 shape, with the B integration PR, and **starts from B's twelve rather than C's ten** — hers is the superset, already carries `factor_source`, and already has `calculate_response_single.json` and `errors/unauthorized.json`, which C lacks. C's `taxonomy.json` is the better content and should fill it. **`errors/blocked.json` is the only file genuinely absent everywhere**, and it is the one whose `details` is `null` rather than `[]` — every existing error fixture on both branches uses `[]`, which is how that §9.2 requirement gets implemented away | §10 | **B, C, D** |
| 12 | **`sector.details` is folded into `description`. No new column.** C's `taxonomy.json` adds a `details` string per sector and her UI renders it in an expander that falls back to `description` — so against a real API every sector shows its description twice. `description` is already `TEXT` and already carries user-facing prose; a second column means a migration, a schema change, a §6.1 field and one more thing for staff to keep in step, for a field the client has not asked for | §2.1 | **B, C** |
| 13 | **§6.3 carries `source_note` and `data_quality`; §10.2 states they are optional in `bundle.json` and must not be rejected.** v1.1 added the columns to both factor tables and `source_note` to `equivalence` on the stated ground that a calculator which cannot say which numbers are measured and which are borrowed cannot be defended in public — and then the only public surface that could say so, `GET /factors`, was left without them. Publishing the values and dropping the provenance removes the defence and keeps the exposure. On the bundle side the fields are optional and ignored: the engine computes nothing from provenance, but §8.2's **Save as regression case** writes a `bundle.json` straight out of a dry run, so `from_json()` must accept and ignore the keys rather than raise `BundleFormatError` on a bundle that is otherwise entirely valid | §6.3, §10.2 | **A, B** |
| 14 | **§8.1's list of CRUD models is no longer "eleven".** `comparison_scenario` and `comparison_scenario_line` (v0.10) and `ip_block` (v0.12) were each specified in §2 and §8.2/§8.3 without being added here — so the section that states "every write must produce an `audit_log` entry" named three fewer tables than the panel writes to, and an audit of that requirement against this list would have come back clean. `ip_block` is listed with the qualification that it is **not** generic CRUD: list-only, `role = admin`, `ip_hmac` never in `column_list`, created through a custom form and removed through an audited `unblock` action | §8.1 | **E** |
| 15 | **§6.5's "IP addresses are never persisted" contradicted §2.3.** v0.12 deliberately softened that absolute with the blocklist exception — an HMAC, never an address, only for a blocked caller — and §6.5 kept asserting the unqualified form, so the document stated a rule and its exception in two places at once. The absolute is the one that was wrong: a reader implementing §6.5 literally had grounds to call §2.3's table a contract violation. Also records as **open** the question underneath it, which is genuinely unsettled and B's: whether an *in-memory* rate-limit counter may be keyed on a raw address. `api/rate_limit.py` does; `admin/protection.py` uses the §2.3 fingerprint. Two layers currently applying different privacy standards to the same data is not a defensible position for a calculator whose selling point is that it stores nothing about the visitor | §6.5 | **B, E** |
| 16 | **`ip_block`'s "see the note above" now names its target.** The merge reordered §2.3 to put the three submission tables in dependency order, which moved `submission_entry` and `submission_line` in between — so a reference written when the two were adjacent pointed at whatever happened to precede it. It now names the E-8 privacy blockquote explicitly, which is the note that licenses the table's existence against the no-address rule and is the one thing a reader must not fail to find from here | §2.3 | **B, E** |
| 17 | **v0.11's standing instruction is discharged, and this entry closes it.** It asked for §8.2's corrected pre-publish-comparison wording to be applied to the unmerged `docs/contract-v1.0` branch as well. That branch did still carry the stale "old value, new value and change" / "differenced client-side" text, and the merge takes the corrected version because `contract-v1.0` never touched those lines. §8.2 now carries the correction. Recorded because an open instruction in a change log stays open until something says otherwise, and the next reader would spend their time confirming a done action | §0.1, §8.2 | **E** |
| 18 | **Stale cross-references and internal inconsistencies corrected.** Four sit on the path A follows between `FactorBundle.from_json()` and the file format it parses: three sites pointed at §10.1 for the `bundle.json` shape (it is §10.2) and one pointed at §10.2 for the golden suite (it is §10.1). Four more were found by reading the merged document end to end rather than as a diff — §2.3's `submission.token` expiry job pointed at §2.3 itself rather than at §5.3's `expire_tokens`; §7.3 cited a "§7.6.1" that has never existed in any version (it is §7.6 rule 1); §7.3a still called `improvement.js`'s per-line rounding drift "not resolved here" after change 4 resolved it, when §6.2's 0.010 kg tolerance is derived from that exact behaviour and accepts it — **on the boundary, and only while the 20-line cap holds**; and §6.3 said v1.1 added `data_quality` to `equivalence`, which it did not (§2.2 gives `equivalence` a `source_note` and no `data_quality`) | §2.3, §4.1, §6.2, §6.2.1, §6.3, §7.3, §7.3a | **A, B, C** |
| 19 | **`ip_hmac` is added to §5.5's `REDACTED_FIELDS`, which said three fields for four revisions after it stopped being three.** §8.3 has asserted since v0.12 that `write_audit` redacts `ip_hmac`; §5.5's literal — the v0.3 definition, written before the field existed — did not carry it, so the document stated a protection and its absence in two places at once. **The definition was the stale half, not the claim.** E-8's final review added it to `admin/audit.py`'s copy precisely because flipping `can_delete = True` on the blocklist screen would otherwise serialise a whole `IpBlock` row through `row_to_dict` and land the fingerprint in `audit_log`, which every staff member can read. It matters more after B's integration than before it: `db/repository.py`'s `write_audit` becomes the canonical one and `admin/audit.py` becomes a re-export (change 9), so **the copy that already redacts `ip_hmac` stops being the code that runs**, and an API-side automatic block writes its entry through this one. The reason is written in rather than left implicit — `ip_hmac` is not a credential like the other three; it is derived from a visitor's address, and §2.3 permits storing such a derivation in `ip_block` alone, not in a table with a wider audience | §5.5 | **B, E** |
| 20 | **§5.4 now says what happens to a NULL `food_category_id`: it groups into an explicit `unspecified` bucket, suppressed on the same threshold as any other, and never dropped.** This is change 6's lesson repeating one field over. Pre-merge §5.4 named no grouping column, so it could not be implemented wrongly from §5.4 alone; naming `submission_entry.food_category_id` made the nullability load-bearing, and §2.3 has always said NULL means the user did not break their waste down by type — a real answer, likely a common one, and one `StatsBucket.code` had no legal value for. Dropping the bucket does not remove a number from the page, it **inflates every other share on it**, in the direction of overclaiming. Also stated: do **not** resolve NULL to `standard_mix` here. The engine does that (§3, §6.2) because it needs a factor; the statistics must not, because it would report a composition the user never claimed and make `standard_mix`'s share indistinguishable from the users who chose it | §5.4, §6.4 | **B, D** |
| 21 | **§5.4's `excluded_from_public` exclusion named a join path one table short of the column it filters on.** Third instance of the same class, found by re-reading §5.4 against changes 6 and 20. `excluded_from_public` is on `submission` (§2.3); the breakdowns group over `submission_entry` and `submission_line`, and `by_destination`'s stated path stopped at "joined to its entry". Followed literally, **staff moderation applies to nothing** — the excluded submission's entries and lines are counted anyway, silently, which is the entire purpose of the flag defeated by a missing join. Every breakdown now joins up to `submission`, and the docstring says it goes one table further than its own grouping needs so nobody trims it back | §5.4 | **B** |
| 22 | Four smaller inconsistencies the end-to-end read turned up, all fixed: §4.2 said "one" §6.2 response field has no §3 counterpart when there are two (`token` is the other, from §5.3); the dry-run row said "no token is returned" while §6.2's prose said `token` is `null` — **settled as `null`, present as a key**, per §6.3's own rule for the same choice; §6.4's worked example had bucket counts summing to exactly `total_calculations`, demonstrating the equality on the page whose new paragraph explains why the two differ, and now carries 1,247 submissions across 1,600 entries plus an `unspecified` bucket so D can see one; and §2.3 still sent the reader to §8.3 for a reconciliation item change 9 had closed | §2.3, §4.2, §6.2, §6.4 | **A, B, C, D, E** |
| 23 | `docs/architecture.md` §9.1.1, cited by §8.3 for the protection design's operational detail, **is not on `main`** — it lands with PR #9, and until then resolves only on `admin_panel`. Said out loud at the citation. A cross-reference that dangles for a stated reason is a known state; one that dangles silently reads as an error in this document | §8.3 | **B, E** |

> **Still open after this revision.** None of these is a defect in the document; all of them are decisions nobody has taken. **O-1 remains the hard blocker** — the client has not supplied real emissions factors, so everything runs on mock data and the banner stays mandatory. Beyond it: whether an in-memory rate-limit counter may hold a raw address (#15, B's); whether `admin.detection.looks_automated`, `RequestRate` and `_client_ip` move into a shared layer or are duplicated in `api/` (§8.3, B's, and the four blocklist items in `api/` are all downstream of it); whether `landfill_diverted` becomes a real `metric` row plus a formula (#8, the client's); and the positive/negative semantic colour pair, which C and D both need and neither has written down.
>
> **Two of those four closed in v1.3** (above): the detection helpers moved to `db/detection.py`, and the rate-limit counter now keys on the §2.3 fingerprint in both layers. `landfill_diverted` and the semantic colour pair are still open. This paragraph is left as it was written rather than edited, because it is a record of what was true at v1.2.

### v1.1 — 2026-08-07 (raised by E, **affects B**)

Made while planning the factor screens, on the principle that the last thing to arrive should not be the thing that forces a migration.

| # | Change | Section |
| --- | --- | --- |
| 1 | `factor_upstream` and `factor_downstream` each gain `source_note` and `data_quality`, both nullable. The client has not supplied real factors, and the Otago 2025 baseline says data quality varies by an order of magnitude across the supply chain — primary-production loss rates are largely borrowed from Australian figures. `is_mock` is all-or-nothing and cannot express "these forty rows are solid and those twelve are borrowed". The columns exist now, empty, so real data arrives as an import rather than a migration. `data_quality` is free text, not an enum, for the same reason the destination groupings are a table. | §2.2 |
| 2 | `equivalence` gains `source_note`. Open item O-3 — the New Zealand sources for km driven, meal equivalents and showers are unsettled, and an equivalence with no stated basis is the figure most likely to be challenged in public. | §2.2 |

### v1.0 — 2026-08-07 (raised by E from reviews of B's and C's branches, **affects A, B and C**)

The first revision driven by reading other people's code rather than by writing the panel. Four of the five come from a real defect found on a branch.

| # | Change | Section | Affects |
| --- | --- | --- | --- |
| 1 | **A submission now carries one or more `entries`**, each a `(sector, food_category)` pair with its own scenario lines. The response carries engine-computed `totals` alongside per-entry results. Found by reviewing C's branch against B's: C's multi-entry UI sent one `POST` per entry sharing one session token, and §5.3's token upsert overwrote each row with the next — a five-row calculation persisted one row, while the client added the per-entry results together in JavaScript. The client now computes nothing; a multi-stage business is one submission and one request. | §2.3, §6.2 | **A, B, C** |
| 2 | **`UNIQUE` containing a nullable column does not prevent duplicates in MySQL.** NULLs compare distinct, so `factor_downstream`'s generic `food_category_id IS NULL` rows could duplicate without limit and the fallback lookup would pick one nondeterministically — wrong numbers, no error, nothing in the logs. A functional index over `COALESCE(food_category_id, 0)` is now required. **Raised by B during implementation**, with an integration test proving it. | §2.2, §2.3 | **B** |
| 3 | **`details[].field` is a bracket-indexed path** (`entries[0].current[1].qty_kg`), not Pydantic's native `loc` form. The two fixture sets on the team had already chosen different formats, and a mismatch makes field-level highlighting fail silently — the user only ever sees the generic banner. | §9 | **B, C** |
| 4 | `qty_kg` is limited to 3 decimal places, and the rule is now written down. B enforced it; it was in no version of this document, and `unit_preset.kg_per_unit` is `DECIMAL(12,4)`, so a container preset times a non-integer count lands on 4 places and returns a 400 the user cannot act on. | §6.2 | **B, C** |
| 5 | A `token` that does not resolve to a live submission is treated as absent and a new one is minted, rather than returning `VALIDATION_ERROR`. A stale `sessionStorage` value from an earlier deployment must not break the calculator. | §6.2 | **B** |

> **Still open after this revision:** §7's module list does not match what C actually built — `view.js`, `calculator.js`, `results.js`, `improvement.js`, `main.js` and `methodology.js` are not named there, and `view.js` in particular holds the shared escaping and formatting primitives that D and E will otherwise reimplement. C to supply the JSDoc; E to fold it in.

### v0.13 — 2026-08-09 (raised by E, **affects B, C and D**)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added the `ip_block` schema table — columns, types and constraints — which §8.3 previously named without defining. **B needs this for the migration**, and the `CHAR(64)` / UNIQUE choices are load-bearing rather than incidental. | §2.3 |
| 2 | Added the `BLOCKED` (403) error code and its envelope, with `details` fixed at `null` and a `message` that never varies. Previously undefined, so B would have had to invent a code and C and D would each have handled it differently — and would likely have retried it as though it were `RATE_LIMITED`, which never succeeds. | §9, §9.2 |
| 3 | Stated that addresses are normalised inside `ip_fingerprint` (`ipaddress.ip_address(x).compressed`) and that unparseable input raises `InvalidAddressError` rather than being hashed. **B's middleware must not let that exception escape** on the request path. Before this, `" 203.0.113.9"`, `203.0.113.09` and the several spellings of one IPv6 address each fingerprinted differently from what the middleware computes, so a block appeared on the screen and stopped nobody, with nothing failing anywhere. | §2.3 |
| 4 | Replaced §8.3's "auditing is the caller's job" instruction — which, as written, told the API layer to do something the layering rule forbids — with an honest reconciliation item: `write_audit` lives in `admin/`, `api/` may not import it, so an API-side block cannot audit itself today. Named the resolution (move `write_audit`/`row_to_dict` to `db/`, where §5.5 already says they belong) and named B as the owner of the decision. Same for `looks_automated`, `RequestRate` and `_client_ip`, which are in `admin/` and which B's middleware needs: recommendation recorded, decision hers, and stated plainly as unresolved rather than pretending otherwise. | §8.3 |
| 5 | Stated that `python -m admin.cli rotate-key` now clears `ip_block`. An HMAC cannot be re-keyed, so every block previously survived a documented, supported `SECRET_KEY` rotation as an unreachable row that `is_blocked` never matched and `unblock` could never remove. | §2.3, §8.3 |
| 6 | Stated that `/admin/login` and `/admin/verify` are exempt from the rate limit (and from that check only). Behind a proxy with `PROTECTION_TRUSTED_PROXY` false — the shipped arrangement — every caller shared one bucket and refused requests were counted, so 1 request/second from any unauthenticated caller locked every administrator out remotely. | §8.3 |
| 7 | Recorded that nothing in this system ever shows staff a caller's address, so the manual-block form cannot supply its own input — it has to come from a proxy or platform access log. Worth confirming such a log exists before an incident. | §8.3 |

### v0.12 — 2026-08-08 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Documented E-8's blocklist as an explicit, named exception to "no IP address, no user agent, no fingerprint of any kind is stored" — an HMAC of an address, keyed on `SECRET_KEY` with a named `info` string (`kaicalc-blocklist-v1`), stored only for an address a staff member or the automatic protection has blocked. | §2.3 |
| 2 | Corrected §8.3's "Blocklist" paragraph, which predated E-8 and described a table (`ip_blocklist`, CIDR-keyed, storing nothing) that does not match what was actually built (`ip_block`, HMAC-keyed). Left uncorrected, it directly contradicted the new §2.3 text added by this same version. | §8.3 |
| 3 | Added the blocklist screen (`/admin/ip-block/list`, `/admin/ip-block/block`, the `unblock` action) and the `python -m admin.cli unblock <address>` operational command — the server-side way back in for an administrator who has blocked the address they are sitting behind, since the blocklist check exempts no one, not even an authenticated staff session. | §8.3 |

### v0.11 — 2026-08-08 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Corrected §8.2's description of the pre-publish comparison view to match what was actually built: the published and draft values are shown side by side, per metric, with no difference computed — not "old value, new value and change" and not "differenced client-side" (both stale, from before Decision 6 was applied to this view). The stale wording claimed a client-side subtraction that would have put a number in front of staff no server-side calculation produced. **The same sentence exists on the unmerged `docs/contract-v1.0` branch (PR #10, v1.1) and needs the same correction there** — this change only touches the copy in this tree. | §8.2 |

### v0.10 — 2026-08-08 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added `comparison_scenario` and `comparison_scenario_line`. §8.2 named the pre-publish comparison view's standard scenarios as staff-editable but never defined where they live; these two tables are it, with their own CRUD screens under a new "Comparison" category. | §2.2a, §8.2 |

### v0.9 — 2026-08-07 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added `issue_password()`. §8.3 named "issues a random password" as half of an eviction; no function performed it. | §8.3 |

### v0.8 — 2026-08-06 (raised by E, **affects B**)

| # | Change | Section |
| --- | --- | --- |
| 1 | Added `staff.session_generation`. Removes the v0.7 known limitation: a credential change now ends the sessions that predate it. | §2.4, §8.3, §8.4 |

### v0.7 — 2026-08-05 (raised by E, **affects B**)

| # | Change | Section |
| --- | --- | --- |
| 1 | Recorded a known limitation: **changing a compromised account's password does not terminate its sessions, at any point.** Sessions carry no generation marker. Found while building the panel and reproduced end to end. **`require_staff()` returning a username does not mean that session has not been evicted** — B should not assume otherwise. | §8.3 |
| 2 | Recorded the gap underneath it: §8.3 names "issues a random password" as half of the eviction, but no service function performs it. `set_password` clears `must_change_password`, so an issued password leaves the account looking fully onboarded. | §8.3 |

### v0.6 — 2026-08-04 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | The administrator floor now guards on two counts, not one: active administrators, and *usable* administrators (active, MFA-enrolled, past the forced password change). Found in the final security review — bootstrap's two accounts are active but cannot log in, so the single count reported two usable administrators when a client who onboarded only one had exactly one. Deactivating the real administrator would have been permitted, leaving the panel owned by accounts nobody can access and no email path back. | §8.3 |

### v0.5 — 2026-08-04 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | The operational commands are subcommands of one module (`python -m admin.cli reset-mfa`) rather than three separate module entry points (`python -m admin.reset_mfa`). `--help` then lists every command in one place, and settings loading and session construction are written once. Caught during implementation review: `admin/security.py` was raising an error that told the operator to run `python -m admin.rotate_key`, a command that does not exist — and it fires precisely in the scenario where `SECRET_KEY` has been rotated and no authenticator works, which is the worst moment to hand someone an invalid instruction. | §8.3 |

### v0.4 — 2026-08-04 (raised by E, affects E only)

| # | Change | Section |
| --- | --- | --- |
| 1 | The application creates two administrator accounts on first start, with per-deployment random passwords printed once to standard output. A deployment now satisfies the two-administrator rule from the moment it comes up instead of depending on the installer running the CLI twice, and a system that starts with one administrator can be locked out by a single lost phone. **No default password exists anywhere in the source** — a fixed one on a public panel is exactly how community-sector accounts get taken over. | §8.3 |

### v0.3 — 2026-08-04 (raised by E, affects B)

Specifies staff authentication, which v0.1 named but did not define, and makes the audit-log requirement executable.

| # | Change | Section |
| --- | --- | --- |
| 1 | v0.1 §8.3 said "sqladmin built-in authentication". **There is no such thing** — `sqladmin` supplies an `AuthenticationBackend` abstract class and nothing else: no user store, no password hashing, no login page. §2.4 and §8.3 now specify all of it. | §2.4, §8.3 |
| 2 | Added the `staff` and `staff_recovery_code` tables. Owned by E, but carried in this document so that Alembic has a single migration chain — two chains will collide. | §2.4 |
| 3 | **MFA (TOTP) is mandatory for every account**, enrolled on first login and enforced before any other admin route is reachable. New Zealand community organisations have had credential-stuffing incidents; a public admin panel with password-only authentication is not defensible. | §8.3 |
| 4 | Two roles, `admin` and `staff`. Publishing and rollback are available to both — `audit_log` plus one-click rollback already provide accountability and recovery, and gating them behind an administrator would stall routine work in a three-to-five person team. Account management and MFA resets are administrator-only. | §8.3 |
| 5 | **No email system.** Account recovery is therefore three layers: recovery codes, another administrator, and a server-side CLI. At least two active administrator accounts must exist at all times, enforced in the service layer. | §8.3 |
| 6 | v0.1 §8.1 required all writes to go through "the repository functions in §5", but §5 contains **no write function for any of the eleven taxonomy and factor tables**, so the requirement was literally unexecutable. Replaced with a requirement on the outcome — every write produces an `audit_log` entry — plus a single insertion point, `write_audit()`. | §5.5, §8.1 |
| 7 | `write_audit()` carries a field blocklist. `audit_log` is readable by every staff member, so serialising a `staff` row into `before_json` would expose password hashes and TOTP secrets to anyone with an account — a real privilege-escalation path. | §5.5 |

**Unchanged:** nothing in §3, §4, §6 or §7. The public API is untouched.

### v0.2 — 2026-08-04 (raised by E, affects A and B)

Adds the dry-run capability the admin panel needs, and closes two holes in v0.1.

| # | Change | Section |
| --- | --- | --- |
| 1 | Resolved a contradiction: §4.2 said the admin dry-run page calls the engine directly, §8.2 said it calls the HTTP API. **The HTTP path wins**; the §4.2 docstring is corrected. Two paths would drift, and the point of a dry run is that it exercises what production exercises. | §4.2, §8.2 |
| 2 | `X-Dry-Run: true` now means exactly one thing — **do not persist** — and is enforced by staff authentication. v0.1 declared it "admin panel only" but specified no mechanism, so anyone could send it and compute without leaving a record. | §6.2 |
| 3 | `POST /calculate` gains an optional `dry_run` object carrying either a persisted `factor_set_version` or a complete inline `bundle`. Without it a dry run could only ever exercise the published factors, which defeats its purpose. | §6.2 |
| 4 | Defined the shape of `bundle.json`, which §10.1 named but never specified. It is now the single shape used by golden tests, `FactorBundle.from_json()` and dry-run requests. **It includes the taxonomy**, because §4.1's `has_destination()` / `has_sector()` / `standard_mix_code()` cannot be implemented without it, and because staff must be able to trial a new destination or food category before committing it. | §10.2 |
| 5 | Added `FactorBundle.from_json()` and `FactorBundle.validate()`. | §4.1 |
| 6 | Added error code `UNAUTHORIZED` (401); v0.1 had no authentication failure code at all. `FORMULA_ERROR` now has two presentations — opaque for the public, fully located for authenticated dry runs, because staff tuning a formula must be told where it broke. | §9 |
| 7 | Added `require_staff()` as the sole interface between the admin authentication system (E) and the API layer (B). | §8.4 |
| 8 | Added the pre-publish comparison view. | §8.2 |
| 9 | `load_factor_bundle` caching must be slotted by `factor_set_id` so that loading a draft cannot contaminate the published slot. | §5.2 |

**Unchanged:** the public request path (no header, no `dry_run`) behaves exactly as in v0.1 — C and D require no changes. No database table or column is added or altered. No §3 domain object changes.

---

# 1. Global Conventions

## 1.1 Naming

| Object | Convention | Example |
| --- | --- | --- |
| Table name | Singular, snake_case | `food_category` |
| Business code (`code` column) | snake_case, lowercase, globally stable | `anaerobic_digestion` |
| JSON field | snake_case | `qty_kg` |
| Python function or variable | snake_case | `load_factor_bundle` |
| JavaScript function or variable | camelCase | `renderDonut` |
| Constant code | UPPER_SNAKE | `GWP_CH4_100` |

**The `code` column is the cross-layer identifier.** API requests and responses use `code`, never the database `id`. The front end must never learn an auto-increment primary key.

## 1.2 Numbers

| Context | Type |
| --- | --- |
| Database | `DECIMAL`; **`FLOAT` and `DOUBLE` are prohibited** |
| Python | `decimal.Decimal`; **`float` is prohibited** |
| JSON | Decimals are transmitted **as strings** to avoid JavaScript double-precision loss (e.g. `"qty_kg": "1200.500"`) |
| JavaScript | Convert with `Number()` only for display; never used in a calculation |

> Sending decimals as JSON strings is deliberate. JavaScript's `Number` is a double, and `0.1 + 0.2 !== 0.3`. The front end only displays values, so converting to `Number` is safe for formatting and charting.

## 1.3 Time

Always UTC, ISO 8601 with a timezone designator: `2026-07-31T09:15:00Z`. The front end converts to local time for display.

## 1.4 Scenario Key

`scenario` has exactly two legal values: `"current"` and `"alternative"`.

---

# 2. Database Schema (owner: B)

## 2.1 Taxonomy

### `destination_group`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `reuse` / `recycle_recovery` / `disposal` |
| `name` | VARCHAR(128) | NOT NULL | Display name |
| `is_waste` | BOOLEAN | NOT NULL | Per MfE: reuse is false, the rest are true |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> Groupings are a table rather than a hard-coded enum because the MfE definition may be revised. The 2025 Otago baseline has already recommended moving bioprocessing from waste to reuse.

### `destination`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `group_id` | INT | FK → `destination_group.id`, NOT NULL | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `landfill`, `compost`, `animal_feed`, `anaerobic_digestion`, `prevention`, … |
| `name` | VARCHAR(128) | NOT NULL | |
| `description` | TEXT | NULL | User-facing explanation |
| `is_prevention` | BOOLEAN | NOT NULL, DEFAULT FALSE | The prevention offset. **At least one active row must be TRUE; more than one is allowed** |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **This system knows no destination code by name. It knows one destination *role*, and `is_prevention` is it (v1.22).** A prevention destination expresses "waste avoided" and keeps the two scenarios mass-conserving. Four rules are stated in terms of the flag and none of them is optional: `admin/taxonomy_rules.check_prevention_destination` refuses any edit that would leave no usable flagged row; §6.2 refuses **any** flagged destination in a **current** scenario, because it is by construction where waste that did not happen goes; §6.1 holds flagged rows out of its coverage inference; and §5.4 reads the current scenario only, so none can become a public statistic.
>
> **It was the literal `"prevention"`, in `db/types.PREVENTION_CODE`, until v1.22, and that constant is now deleted.** Two things forced it. A taxonomy row's identity is data on this project — `destination_group.is_waste` above is a column for precisely this reason — and the client has not settled what this destination will be called or whether it survives under that name; the row can now be renamed freely. And §10.3's `refed_prevention` is a prevention destination by every property that matters and was covered by none of the four rules, so it could be entered as current-scenario waste and reach the public statistics. See v1.22 for the full account.
>
> **"At least one", not "exactly one", which is where this departs from `is_standard_mix` below.** Two vocabularies share these global tables (v1.19) and each brings its own prevention row, so an upper bound would refuse the state the deployment is in; and nothing has to *choose* between prevention destinations, which is the whole reason the standard mix needs one. There is consequently no UNIQUE key and no `COALESCE(...)` functional index on this column. The lower bound is counted over **active** rows whose **group** is also active — every active-destination listing joins through `destination_group` — and is skipped entirely on an empty `destination` table, so an unseeded database can still be bootstrapped by hand.
>
> **Zero flagged rows is refused rather than given a meaning.** An alternative scenario that redirects mass between real destinations stays expressible without one, so nothing computes a wrong number; but §6.2 still requires the two scenarios to conserve mass, so the improvement panel would render sliders that cannot express reduction — the only thing it is for. If the client removes the concept, that is a revision of this section, not a data edit.
>
> **The factors of a flagged destination are zero, and since v1.22 that is enforced rather than assumed.** `publish_factor_set` refuses any upstream or downstream row in the set that prices a flagged destination at anything else; an absent row stays legal, because §4.1's lookup already returns zero for one. Both its downstream and its upstream factors are zero, and since v1.8 that is a statement about the data rather than about the prose. This blockquote read "with all factors set to zero" for five revisions while `factor_upstream` had no destination column, so a line moved to `prevention` kept its entry's full upstream factor; v1.5 corrected it to downstream-only and pointed at `architecture.md` O-7. **O-7 is closed** — `factor_upstream.destination_id` is the column, and `prevention` carries a row at zero for every `(sector, food_category, metric)` that has a general row. That last clause is the load-bearing one: the offset is now data, so a general row created without a matching `prevention` row reverts to the old behaviour for that tuple alone, and nothing in the panel refuses it.

### `sector`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `primary_production`, `processing`, `wholesale_retail`, `consumer_household`, `consumer_hospitality`, `consumer_institution` |
| `name` | VARCHAR(128) | NOT NULL | |
| `description` | TEXT | NULL | The whole of the user-facing explanatory text, short or long. **There is no second `details` column** — see below |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **There is one description field, not two.** C's `tests/fixtures/taxonomy.json` added a `details` string per sector and her sector step renders it in an expandable panel, falling back to `description`. No version of this contract has ever defined `details`, so against a real API the expander shows every sector's `description` twice. **Ruling: fold the longer text into `description`.** `description` is `TEXT` and already carries user-facing prose; a second column means a migration, a schema change, an extra field in §6.1 and one more thing for staff to keep in step — for a field the client has not asked for. C's fixture drops `details` and merges its text into `description`; her expander reads `description`.

### `food_category`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | The eight Otago baseline categories plus `standard_mix` |
| `name` | VARCHAR(128) | NOT NULL | |
| `is_standard_mix` | BOOLEAN | NOT NULL, DEFAULT FALSE | Fallback when the user does not know the composition. **Exactly one row must be TRUE.** |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **Where the taxonomy invariants are enforced.** "Exactly one row must be
> TRUE" and the existence of a `destination.is_prevention` row are statements
> about a table, not a column, so neither is a database constraint. Both are checked in
> `admin/taxonomy_rules.py`, called from `AuditedModelView`'s
> `validate_before_commit` hook — inside the transaction that is about to
> commit, before the audit entries are written. A refused change rolls back
> the row and its audit entry together. This placement is deliberate:
> `sqladmin`'s generic edit path never calls a service function, so a check
> that lives only in one is bypassed by the edit form.
>
> **"Exactly one row must be TRUE" means exactly one *active* row.** The
> check counts `is_standard_mix = TRUE AND active = TRUE`; a deactivated
> standard mix does not count towards the one, because it is as unusable to
> the engine as a missing one. Two rows may hold `is_standard_mix = TRUE` at
> once as long as only one of them is `active`.

### `metric`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `co2e`, `ch4`, `water`, `cost`, `mass` |
| `name` | VARCHAR(128) | NOT NULL | |
| `unit` | VARCHAR(32) | NOT NULL | Internal unit, e.g. `kg CO2e` |
| `display_unit` | VARCHAR(32) | NULL | Falls back to `unit` when null. **A presentation variant of `unit` at the same scale, never a different scale — see §6.1** |
| `display_precision` | TINYINT | NOT NULL, DEFAULT 2 | Decimal places |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **Adding a metric means inserting one row here, populating the factor tables, and writing one formula. No code changes.** This table is where Decision 2 actually lands.

### `unit_preset`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | `wheelie_bin_240l` |
| `label` | VARCHAR(128) | NOT NULL | "240 L wheelie bin (full)". Staff-typed, so **published exactly as written and never translated** (§7.7.7) |
| `food_category_id` | INT | FK, NULL | Null means it applies to all categories |
| `kg_per_unit` | DECIMAL(12,4) | NOT NULL | `kg_per_unit >= 0` CHECK. Not integral in the shipped seed |
| `source_note` | TEXT | NULL | Basis for the conversion |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

> **No `sort_order`, deliberately.** §6.1 orders this table by `kg_per_unit` — smallest container first — and the step-3 `<select>` renders that order as given. Size is the only order a visitor scanning for their own bin can use, and the column already carries it, so no schema change buys it.

> **What `food_category_id` is *for*, since it is the one column here that is easy to read as decorative (v1.33).** It is per-food density. A bin of bread and a bin of potatoes do not weigh the same, so a preset naming a category is a conversion that is only true of that category and the calculator offers it **only once step 2 has chosen it**; a NULL row is "a wheelie bin is a wheelie bin" and always shows. Step 2 is optional, so a visitor who skipped it sees the generic containers alone. **A selection that stops being on the list when the category changes is cleared from the front end's state with it** — a conversion still in force behind a choice nobody can see is the shape of defect §10 keeps recording.
>
> **Nothing in the shipped seed uses it.** All ten rows are NULL, because no measured per-food density exists to put on one and inventing one would be worse than the placeholder it replaced. See O-6.

## 2.2 Factors

### `factor_set`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `version_label` | VARCHAR(128) | UNIQUE, NOT NULL | `MOCK-v0 — PLACEHOLDER` / `2026-Q3` |
| `status` | ENUM | NOT NULL | `draft` / `published` / `archived` |
| `is_mock` | BOOLEAN | NOT NULL, DEFAULT TRUE | Triggers the site-wide warning banner. **Not on any edit form** — see below |
| `effective_from` | DATETIME | NULL | |
| `published_at` | DATETIME | NULL | |
| `published_by` | VARCHAR(128) | NULL | Staff username |
| `notes` | TEXT | NULL | Provenance of the data |

**Constraint: at most one row may have `status = 'published'` at any time.** Enforced transactionally in the repository layer.

> **Where this is enforced.** In `admin/taxonomy_rules.py`'s
> `check_single_published_set`, called from `AuditedModelView`'s
> `validate_before_commit` hook — inside the transaction that is about to
> commit, after the flush so it sees the pending change, and before any audit
> row is written so a refusal leaves no record claiming it happened. The
> publish and rollback actions of §8.2 will additionally take `SELECT ... FOR
> UPDATE` over the table, because two staff members publishing different
> drafts at the same moment is a race this hook alone cannot settle.

> **`is_mock` moves through two actions, never through a form, and the two
> directions are not symmetric.** `false → true` — *adding* the mandatory,
> non-dismissible placeholder warning of §7.6.2 — is allowed in **every**
> status, from either role, with no proof: the safe direction has to be
> instant, so that anybody who doubts a published set can put the disclaimer
> in front of the public at once. `true → false` — *removing* it — is allowed
> in every status too, and takes a **press-then-prove** confirmation (the
> current password or a live TOTP code, §8.3's `reauthenticate`) plus its own
> `audit_log` action, `clear_placeholder`, carrying both values. It is the
> only switch in the system that makes a public disclaimer disappear.
>
> **Status does not gate it, and that is a reversal.** The panel used to
> refuse the flag on a published or archived set and tell staff to clone
> first. The workflow that refuses is the real one: publish the real factors,
> let them run publicly for a day or two to verify them, then clear the flag.
> Forcing a clone there creates a `factor_set` row and a version label for a
> change in which **not one factor value differs**, while every `submission`
> recorded meanwhile stamps the old id — a version discontinuity manufactured
> by the workflow rather than by the data. A published set's **other** fields
> are still immutable in place and `validate_before_commit` still refuses
> them.
>
> **A draft takes the same proof as a published set**, deliberately. A draft
> has no public consequence of its own, but `publish` takes no proof (§8.3
> keeps it open to both roles on purpose), so a gate applied only to published
> sets has a one-button way round it: clear it on the draft, then publish.
>
> The write path is `admin/factor_lifecycle.py`'s `set_placeholder_flag`,
> beside `publish` / `rollback` / `archive` and under the same
> `SELECT ... FOR UPDATE`; it refuses a no-op for the reason publishing an
> already-published set is refused. The proof is the view's, because a service
> function has neither the request's form nor the login throttle.
>
> **`is_mock` is off the *create* form as well**, so a new set always takes
> the column default and cannot be created unflagged. "Nothing is published as
> real data by omission" is structural rather than a habit.
>
> **The published set's `is_mock` is never served from a warm bundle cache.**
> §5.2's `load_factor_bundle` re-reads this one column on every hit. It is the
> only field of a published set that legitimately moves while it stays
> published, and `invalidate_factor_bundle` cannot carry the change across:
> the panel and the API are separate processes and that cache is a dict per
> process, with no expiry. Without the re-read, clearing the flag takes the
> banner off `/factors` (read live) and leaves it on every `/calculate`
> result, and *setting* it does not reach the public at all.

### `factor_upstream`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NOT NULL | |
| `destination_id` | INT | FK, **NULL** | **NULL means the row applies to every destination for that `(sector, food_category, metric)`** |
| `metric_id` | INT | FK, NOT NULL | |
| `value_per_kg` | DECIMAL(20,10) | NOT NULL | |
| `source_note` | TEXT | NULL | Where this number came from |
| `data_quality` | VARCHAR(32) | NULL | Free text, e.g. `measured` / `modelled` / `proxy-AU` |

UNIQUE(`factor_set_id`, `sector_id`, `food_category_id`, `destination_id`, `metric_id`)

> **The nullable `destination_id` exists so that `prevention` can be a real 100% offset — open item O-7, closed in v1.8.** NULL is the normal case and almost every row carries it: producing a kilogram of dairy costs what it costs whatever later becomes of it. Lookup order: exact match on `destination_id` first, then fall back to the NULL row, then treat as zero — the same three-step `factor_downstream` uses for `food_category_id`, and the same one `FactorBundle.downstream()` already implements.
>
> Before this column existed, `factor_upstream` could not see the destination, so a line moved to `prevention` kept its entry's full upstream factor and only the downstream delta reached `net_benefit`. On the canonical fixtures, 800 kg of `not_harvested` moved to `prevention` yielded 96.000 kg CO2e where a true offset yields 456.000 — 78.9% of the benefit missing, one-directionally, on the client's headline claim. The engine's `line_value = f(qty_kg, upstream, downstream, const_*)` is unchanged by the fix: the destination is resolved in the lookup, not applied in the formula.
>
> **The column is not restricted to `prevention` and must not be.** Factors are data (that is §2.1's whole premise); a future factor set may legitimately give `animal_feed` an upstream row of its own, and a CHECK naming one destination code would have to be migrated away the first time the client asked for that.

> **This UNIQUE has the same defect `factor_downstream`'s does, for the same reason, and needs the same functional index.** MySQL treats NULLs as distinct in a unique key, so the constraint above permits unlimited duplicate rows for the generic case — and here the generic rows are not the exception, they are almost the whole table. Two of them and the fallback lookup picks one nondeterministically: the same input returning a different net benefit run to run, with nothing in the logs. A unique index over `COALESCE(destination_id, 0)` is what enforces it, and it must be written by hand — autogenerate detected this one as a plain four-column index with the expression silently dropped. Test it by inserting the second generic row and asserting `IntegrityError`, against **MySQL**; on SQLite it proves nothing.
>
> Third instance of the trap, after `factor_downstream` (below) and `submission_entry` (§2.3). Raised by B on the first; found twice more by looking for it.

### `factor_downstream`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `destination_id` | INT | FK, NOT NULL | |
| `sector_id` | INT | FK, **NULL** | **NULL means the row applies to every sector for that destination.** NULL is the normal value; the New Zealand set takes it on every row |
| `food_category_id` | INT | FK, **NULL** | **NULL means the row applies to every food category for that destination** |
| `metric_id` | INT | FK, NOT NULL | |
| `value_per_kg` | DECIMAL(20,10) | NOT NULL | **May be negative** (an offset) |
| `source_note` | TEXT | NULL | Where this number came from |
| `data_quality` | VARCHAR(32) | NULL | Free text, e.g. `measured` / `modelled` / `proxy-AU` |

UNIQUE(`factor_set_id`, `destination_id`, `sector_id`, `food_category_id`, `metric_id`)

> **Why every factor row carries its own provenance.** The client has not yet supplied real factors, and when they arrive they will not arrive uniformly: the Otago 2025 baseline states plainly that data quality varies by an order of magnitude across the supply chain, and that primary-production loss rates are largely borrowed from Australian figures. A calculator that cannot say which of its numbers are measured and which are proxies cannot be defended in public — and `is_mock` on the factor set is all-or-nothing, unable to express "these forty rows are solid and those twelve are borrowed".
>
> These columns exist now, empty, so that the arrival of real data is an **import** rather than a **migration**. `data_quality` is free text rather than an enum for the same reason the destination groupings are a table and not a hard-coded set: nobody yet knows which categories the client will use, and a column that must be altered to accept a new value puts us back where we started.

> **This UNIQUE does not do what it appears to, and a functional index is required.** MySQL treats NULLs as distinct in a unique key, so the constraint above permits unlimited duplicate rows for the generic case — the very rows where `food_category_id IS NULL`, and since v1.31 the rows where `sector_id IS NULL` as well. The lookup below would then pick one of them nondeterministically, and the calculator would return different numbers for the same input with nothing in the logs to explain it. Add a unique index over `COALESCE(sector_id, 0)` **and** `COALESCE(food_category_id, 0)` alongside the declared constraint, and test it by inserting the second generic row and asserting `IntegrityError`. **Both columns, not just the newer one:** collapsing only `sector_id` leaves two `food_category_id IS NULL` rows legal again and quietly undoes what this index was created for. The same caveat applies to `submission_entry` (§2.3) and to any other UNIQUE containing a nullable column.
>
> Raised by B during implementation, before it could produce a wrong answer in the field.

> The nullable `food_category_id` exists for cost items such as the waste levy, which are charged per tonne regardless of food type, so no special case is needed. The nullable `sector_id` (v1.31) exists so that a factor set can price the same disposal route differently by stage of the supply chain — a kerbside collection contract and a commercial one at the same landfill — without every set that does not need to being forced to say so.

#### The downstream lookup order (v1.31)

Two nullable dimensions means four rows may legally exist for one `(destination, metric)`, and exactly one of them must win:

| # | Row | Meaning |
| --- | --- | --- |
| 1 | (`sector`, `food_category`) | this sector, this food category |
| 2 | (`sector`, NULL) | this sector, every food category |
| 3 | (NULL, `food_category`) | every sector, this food category |
| 4 | (NULL, NULL) | every sector, every food category — the waste levy shape |
| 5 | — | `Decimal('0')` |

Steps 1, 4 and 5 are not in question: more specific beats less, and absence is zero. **Steps 2 and 3 are the decision.** Both name exactly one dimension, so no count of stated dimensions separates them, and there is no obviously right answer. The sector wins, for three reasons:

1. **The sector is always something the caller stated; the food category may not be.** `submission_entry.sector_id` is NOT NULL and §6.2 requires a sector on every entry, while `food_category` is nullable and §6.2 *resolves* a null one to `standard_mix`. At the moment steps 2 and 3 are compared, the sector in hand is what the user chose and the category may be a substitution the server made on their behalf. Honouring the dimension that was actually stated is the safer of the two.
2. **A row naming a sector is a stronger claim than a row naming none.** This column exists because a downstream factor was found to vary by supply-chain stage. A row that names a sector is therefore a positive assertion made *after* that dimension was known to matter; `sector IS NULL` says only that no sector-specific figure was supplied. Letting the weaker claim beat the stronger one would reproduce, inside the fallback, the very defect the column removes.
3. **It is what a staff member entering the row will expect.** Someone who adds "landfill costs more for the farm sector" must see that number applied to every farm line. Under the other order it would silently not apply to the food categories that happen to carry an all-sector row of their own — a plausible wrong number on the client's headline figure, with nothing anywhere to explain it.

The waste levy is untouched by any of this: it is the (NULL, NULL) row, it is still the last resort, and it still applies wherever nothing more specific exists.

**Test every cell.** A wrong precedence returns a number rather than an error, so a test asserting "a row came back" passes against all twenty-four orderings of the four candidates. `tests/test_bundle.py` asserts all sixteen subsets of the four rows **by value**, with four distinct constants, plus the two directional cases a value-blind test would miss: a *different* sector must fall past the sector row to the food-category row, and a *different* food category must fall past the food row to the row naming neither.

### `constant`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `code` | VARCHAR(64) | NOT NULL | `GWP_CH4_20`, `GWP_CH4_100`, `MEAL_KG`, `LEVY_NZD_PER_T` |
| `value` | DECIMAL(20,10) | NOT NULL | |
| `unit` | VARCHAR(32) | NULL | |
| `note` | TEXT | NULL | |

UNIQUE(`factor_set_id`, `code`)

### `formula`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `metric_id` | INT | FK, NOT NULL | |
| `expression` | TEXT | NOT NULL | See §4.3 |
| `notes` | TEXT | NULL | |

UNIQUE(`factor_set_id`, `metric_id`)

### `equivalence`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `factor_set_id` | INT | FK, NOT NULL | |
| `code` | VARCHAR(64) | NOT NULL | `km_driven`, `meals`, `showers` |
| `name` | VARCHAR(128) | NOT NULL | |
| `source_metric_id` | INT | FK, NOT NULL | Which metric it converts from |
| `value_per_unit` | DECIMAL(20,10) | NOT NULL | Result = metric total × this factor |
| `label_template` | VARCHAR(255) | NOT NULL | `Equivalent to driving {value} km`. `{value}` is the only placeholder; everything else is copied verbatim. **The engine interpolates it, and §3's rule 5 fixes the number format** (whole units, comma thousands separator, `ROUND_HALF_UP`) |
| `source_note` | TEXT | NULL | Basis for the conversion. Open item O-3 — the New Zealand sources for km driven, meal equivalents and showers are not yet settled, and an equivalence with no stated basis is the figure most likely to be challenged |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

## 2.2a Comparison Scenarios

The standard test scenarios §8.2's pre-publish comparison view runs. A scenario is a saved `POST /calculate` request minus the factor set: a sector, a food category, a horizon and a set of destination lines. Rows, not a constant, per §8.2 — hard-coding them would reintroduce "change the code to change the configuration."

### `comparison_scenario`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `code` | VARCHAR(64) | UNIQUE, NOT NULL | |
| `name` | VARCHAR(128) | NOT NULL | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NULL | Null means the standard mix — the same reading §6.2 gives the field |
| `gwp_horizon` | SMALLINT | NOT NULL, DEFAULT 100 | `20` or `100`, per §6.2 |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | |
| `active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |

### `comparison_scenario_line`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `scenario_id` | INT | FK → `comparison_scenario.id`, NOT NULL, ON DELETE CASCADE | |
| `destination_id` | INT | FK, NOT NULL | |
| `qty_kg` | DECIMAL(16,3) | NOT NULL | |

> Unlike the taxonomy tables, a scenario may be deleted through the panel: it is a staff member's own saved test case, referenced by nothing else in the schema, so deleting one strands no historical result.

## 2.3 Submissions

Three tables, written together by one `POST /api/v1/calculate` (§5.3). They are filed as a single Alembic migration, **`0008`, `down_revision = "0007"`** — see §8.3, and do not take the revision number from `docs/ToB_v3.0.md` §1.3, which predates two migrations that have since landed.

### `submission`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `token` | CHAR(36) | UNIQUE, NULL | UUID4 session token; nulled on expiry |
| `token_expires_at` | DATETIME | NULL | One hour after creation |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | Refreshed on upsert |
| `factor_set_id` | INT | FK, NOT NULL | Version stamp; makes results reproducible |
| `gwp_horizon` | SMALLINT | NOT NULL, DEFAULT 100 | Applies to the whole submission |
| `excluded_from_public` | BOOLEAN | NOT NULL, DEFAULT FALSE | **Staff moderation, not user consent** |
| `exclusion_reason` | VARCHAR(255) | NULL | |

**No IP address, no user agent, no fingerprint of any kind is stored.**

> **One exception, added deliberately in E-8.** The `ip_block` table stores an
> HMAC of an address — never the address — under a key derived from
> `SECRET_KEY`, and only for callers a staff member or the automatic
> protection has blocked. A database taken on its own yields no list of who
> visited. Nothing else is persisted: user agents, headers and paths are read
> within a request and forgotten. **Browser fingerprinting was considered and
> rejected** — ineffective against the traffic this defends against, and the
> highest privacy risk of the options.
>
> **One blocklist, owned by `db/`.** `ip_block` and the block/unblock/query
> functions live in `db/blocklist.py`, not in `admin/`. There is one list, and
> both callers read it: the API layer applies it to public traffic, the admin
> panel applies it to itself and provides the screen that manages it. Both
> derive the HMAC key from the same `SECRET_KEY` with
> `info = b"kaicalc-blocklist-v1"` — the same address must produce the same
> fingerprint on both sides, or a block applied in the panel silently fails to
> hold at the API.
>
> **Addresses are normalised before they are fingerprinted, inside
> `ip_fingerprint`.** `db.blocklist.normalise_ip` canonicalises through
> `ipaddress.ip_address(x).compressed`, so `" 203.0.113.9"`,
> `2001:db8:0:0:0:0:0:1` and `2001:db8::1` all reach the same row. It is inside
> `ip_fingerprint` deliberately, so every caller — the API layer, the panel,
> the CLI — inherits it without asking. An unparseable value raises
> `db.blocklist.InvalidAddressError` (a `ValueError` subclass) rather than being
> hashed: a fingerprint of nonsense writes a row that matches no caller and
> that `unblock` cannot remove either. **Callers on a request path must not let
> that exception escape** — `db.detection.client_ip` normalises the connection
> address itself and treats an unusable one as "no address". Both middlewares
> now go through it (`admin/protection.py` and `api/app.py`), and the API's
> also catches `InvalidAddressError` narrowly around an *injected* check, since
> that callable is not its own code. A caller with no usable address is skipped
> by both the blocklist and the rate limit rather than being given a stand-in
> key — see §6.5 for the deployment in which that becomes every caller.
>
> **Rotating `SECRET_KEY` clears the blocklist.** An HMAC cannot be re-keyed the
> way an encrypted TOTP secret can — there is no plaintext address left to
> re-fingerprint from, which is the property this whole design wanted. So
> `python -m admin.cli rotate-key` deletes every `ip_block` row and reports how
> many, rather than leaving rows that `is_blocked` would never match and
> `unblock` could never remove. Blocks must be re-applied after a rotation.
>
> Auditing: `db/blocklist.py` writes no audit entry. See §8.3's "Blocklist"
> section for who writes one instead. The reconciliation item this used to
> create for the API layer — that `write_audit` sat in `admin/`, which
> `api/` may not import — is **closed**: it has always been in
> `db/repository.py` on B's branch (v1.2 change 9). The question that was still open there
> — whether `looks_automated`, `RequestRate` and `_client_ip` move to a shared
> layer or are duplicated in `api/` — is **closed in v1.3**: they are in
> `db/detection.py`, and `admin/detection.py` re-exports them. See §8.3.

`sector_id` and `food_category_id` live on `submission_entry`, not here: one submission carries several, each with its own factors.

### `submission_entry`

One `(sector, food_category)` pair within a submission. A food business has waste at more than one point in the supply chain, and each point draws a different upstream factor, so they cannot share one set of lines.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `submission_id` | BIGINT | FK, NOT NULL, ON DELETE CASCADE | |
| `sector_id` | INT | FK, NOT NULL | |
| `food_category_id` | INT | FK, NULL | Null when the user did not break waste down by type |
| `sort_order` | INT | NOT NULL, DEFAULT 0 | Preserves the order the user entered them, so `entries[]` in the §6.2 response can be paired with the rows on screen |

UNIQUE(`submission_id`, `sector_id`, `food_category_id`)

> The uniqueness constraint has the same MySQL NULL caveat as `factor_downstream` (§2.2): a nullable column in a UNIQUE key does not prevent duplicates, because NULLs compare distinct. Use a functional index over `COALESCE(food_category_id, 0)`.

### `submission_line`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `submission_entry_id` | BIGINT | FK, NOT NULL, ON DELETE CASCADE | |
| `scenario` | ENUM(`current`, `alternative`) | NOT NULL | |
| `destination_id` | INT | FK, NOT NULL | |
| `qty_kg` | DECIMAL(16,3) | NOT NULL, >= 0 | |

UNIQUE(`submission_entry_id`, `scenario`, `destination_id`)

> **Statistics aggregate over entries, not submissions** (§5.4). One submission with three entries is three sector observations, not one — otherwise a multi-stage business would be counted as whichever stage happened to be first. `total_calculations` still counts submissions.

### `ip_block`

Owned by B's layer (`db/`), built by E — see §8.3's "Blocklist", and the
**"One exception, added deliberately in E-8" blockquote under `submission`
earlier in this section**, which is what licenses this table's existence
against the no-address rule. No foreign keys: a block is not owned by a staff
row, and it must survive the account of whoever made it being deleted.

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | Never leaves the server; the panel selects rows by it, no API exposes it |
| `ip_hmac` | CHAR(64) | UNIQUE, NOT NULL | HMAC-SHA256 of the normalised address, hex. `CHAR`, not `VARCHAR` — always exactly 64 characters. UNIQUE is what makes `block_ip` an upsert rather than a source of duplicates |
| `reason` | TEXT | NOT NULL | Free text, shown on the screen and in `audit_log` **in place of** the address |
| `created_at` | DATETIME | NOT NULL | Naive UTC (§1.3). Not refreshed by a re-block — see below |
| `created_by` | VARCHAR(64) | NOT NULL | Staff username, or `cli`. Overwritten by a re-block |
| `expires_at` | DATETIME | NULL | NULL means it does not expire. `is_blocked` filters on this in SQL |

> A re-block of the same address updates `reason`, `created_by` and
> `expires_at` in place and leaves `created_at` alone, so the two can name
> different people at different times. The panel labels them "First blocked"
> and "Most recently blocked by" rather than "when" and "who" for that reason
> (`admin/blocklist_views.py`). A re-block with no duration also clears any
> existing `expires_at` — blank means permanent, not "leave as it was".

> Expired rows are filtered, never pruned. At this scale that is fine; a
> scheduled job can own it later, alongside the `submission.token` expiry job
> (`expire_tokens`, §5.3).

### `audit_log`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | BIGINT | PK, AI | |
| `at` | DATETIME | NOT NULL | |
| `actor` | VARCHAR(128) | NOT NULL | Staff username |
| `action` | VARCHAR(32) | NOT NULL | `create` / `update` / `delete` / `publish` / `rollback` / `archive` / `refuse` / `reveal` |
| `table_name` | VARCHAR(64) | NOT NULL | |
| `row_id` | BIGINT | NULL | |
| `before_json` | JSON | NULL | |
| `after_json` | JSON | NULL | |

> Both JSON columns pass through the field blocklist in `write_audit()` (§5.5). An unfiltered dump of a `staff` row would hand out password hashes, TOTP secrets and — since v1.15 — the reversibly-encrypted unclaimed password. This table is `role = admin` only from v1.15 (§8.2); the blocklist is unchanged and is not conditional on the audience, since the trail is exportable and append-only.

## 2.4 Staff and Access Control

Owned by E. Carried in this document so that Alembic keeps a single migration chain.

### `staff`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `username` | VARCHAR(64) | UNIQUE, NOT NULL | Written to `audit_log.actor` |
| `display_name` | VARCHAR(128) | NOT NULL | |
| `password_hash` | VARCHAR(255) | NOT NULL | bcrypt (`passlib`) |
| `role` | ENUM(`admin`, `staff`) | NOT NULL, DEFAULT `staff` | |
| `is_active` | BOOLEAN | NOT NULL, DEFAULT TRUE | |
| `must_change_password` | BOOLEAN | NOT NULL, DEFAULT TRUE | Set on creation and on an administrator reset |
| `unclaimed_password_enc` | VARBINARY(255) | NULL | **The password this system last minted for this account, until somebody claims it** (v1.15; renamed from `initial_password_enc` and widened to cover issued replacements in v1.16, migration `0012`). Fernet under an HKDF-derived key of its own, never the one `staff_totp_device.secret_enc` uses. Written by `create_staff` and `issue_password`, cleared unconditionally by `set_password`, gone with the row on `delete_staff`. **The only reversibly-stored credential in the system** — see §8.3 for the decision and its cost |
| `mfa_enrolled_at` | DATETIME | NULL | When the account's second factor came into force. **Derived state:** true exactly when `staff_totp_device` holds at least one row with `enrolled_at` set. Kept as a column so the onboarding gates, `require_staff()` and the two-administrator floor go on reading one indexed value; `admin/accounts.py` is its only writer |
| `created_at` | DATETIME | NOT NULL | |
| `created_by` | VARCHAR(64) | NULL | |
| `last_login_at` | DATETIME | NULL | |
| `session_generation` | INT | NOT NULL, DEFAULT 0 | Bumped by every credential change; the session cookie carries the value it was minted under |

### `staff_recovery_code`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `staff_id` | INT | FK → `staff.id`, NOT NULL, ON DELETE CASCADE | |
| `code_hash` | CHAR(64) | NOT NULL | SHA-256 |
| `used_at` | DATETIME | NULL | Single use |
| `created_at` | DATETIME | NOT NULL | |

### `staff_totp_device`

| Column | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `id` | INT | PK, AI | |
| `staff_id` | INT | FK → `staff.id`, NOT NULL, ON DELETE CASCADE | |
| `name` | VARCHAR(64) | NOT NULL | What the person called this device. Reaches the `otpauth://` label, so two devices on one account are distinguishable in the authenticator app |
| `secret_enc` | VARBINARY(255) | NOT NULL | TOTP secret, encrypted at rest. NOT NULL: a row exists because a secret was minted for it |
| `enrolled_at` | DATETIME | NULL | NULL means an enrolment begun and not confirmed — a QR displayed, never proved. Such a row is resumable and is **never** accepted as a second factor |
| `last_counter` | BIGINT | NULL | Last accepted TOTP time step for **this device**; blocks replay within the window |
| `created_at` | DATETIME | NOT NULL | |
| | UNIQUE (`staff_id`, `name`) | | One name per account; everybody's first phone may share a name across accounts |

> **Why an account may hold several, and why the counter is per row.** One column holds one phone, so "enrol a new authenticator" could only ever mean "replace the one you have" — impossible once the phone is gone, which left another administrator resetting your MFA as the only way back. §8.3's self-recovery rule makes that administrator necessarily somebody else, so a lost phone with one device enrolled is a lockout waiting on a colleague's availability. Enrolling a second device *before* losing the first is the only fix that does not depend on one.
>
> `last_counter` **must not** be shared across an account's devices. TOTP replay protection is a property of a secret: two phones hold two secrets and emit two *different* codes for the same time step, so a shared counter would let a login on one device push the counter past the step the other's current, entirely unused code belongs to — and that code would be refused as a replay for the rest of its life. The symptom is "the backup phone does not work", intermittently, only on accounts with two devices.

> **Recovery codes are minted for an account's first confirmed device only.** They are the account-level fallback for when *no* authenticator is available (§8.3, layer L1), not a per-device credential. Minting five more on a second phone would leave somebody holding two printed sheets with no way to tell which is current — and the older sheet just as valid as the newer one.

> **Recovery codes are hashed with SHA-256, not bcrypt, and this is deliberate.** bcrypt is slow in order to resist brute force against low-entropy human-chosen passwords. A recovery code is a high-entropy string we generate ourselves, so brute force is already infeasible and a slow hash buys nothing but latency.

> `staff_totp_device.secret_enc` is encrypted with a **sub-key derived from `SECRET_KEY` via HKDF-SHA256** (`info=b"totp-secret-encryption"`), not with `SECRET_KEY` itself — that key already signs session cookies, and reusing one key for two purposes is a defect waiting to happen. This protects the case where a database dump leaks on its own, which is the common one: a committed backup, a misconfigured export. It does not protect against losing the database and the key together.
>
> **Consequence: rotating `SECRET_KEY` invalidates every enrolled TOTP secret.** A rotation command (§8.3) must decrypt with the old key and re-encrypt with the new one. Without it, the day the client decides to rotate their key is the day nobody can log in.

---

# 3. Domain Objects (owner: A)

All are `@dataclass(frozen=True)` and live in `engine/types.py`. **The engine does not depend on SQLAlchemy**; the repository layer converts ORM objects into these types.

> **These types carry `entries`, and they did so from v1.0 onwards.** v1.0's change-log entry 1 is marked "Affects **A, B and C**", and it reshaped §2.3 and §6.2 — but §3, §4.2 and §5.3 were never brought into line and went on describing a single-entry calculation for two revisions. Corrected in v1.2. If you are holding an older copy, the tell is `sector_code` on `ScenarioInput` and a `CalculationResult` with `current` at the top level.

```python
from dataclasses import dataclass
from decimal import Decimal

# ---------- Input ----------

@dataclass(frozen=True)
class ScenarioLine:
    destination_code: str
    qty_kg: Decimal                 # >= 0

@dataclass(frozen=True)
class EntryInput:
    """One (sector, food_category) pair and both of its scenarios.

    Sector and food category sit here rather than on each scenario because
    §6.2 puts them on the entry: an entry's `current` and `alternative`
    describe the same point in the supply chain, and a wire request cannot
    express two different sectors for one entry. Putting them on the
    scenario would make an unrepresentable state representable."""
    sector_code: str
    food_category_code: str | None          # None -> use standard_mix
    current: tuple[ScenarioLine, ...]
    alternative: tuple[ScenarioLine, ...] | None

@dataclass(frozen=True)
class CalculationRequest:
    entries: tuple[EntryInput, ...]         # at least one; request order is preserved
    gwp_horizon: int = 100                  # 20 or 100; applies to the whole request

# ---------- Output ----------

@dataclass(frozen=True)
class BreakdownRow:
    destination_code: str
    qty_kg: Decimal
    upstream: Decimal               # per kg
    downstream: Decimal             # per kg, may be negative
    value: Decimal                  # this line's contribution to the metric total

@dataclass(frozen=True)
class MetricResult:
    metric_code: str
    unit: str
    display_precision: int
    total: Decimal
    by_destination: tuple[BreakdownRow, ...]    # empty at the totals level; see below

@dataclass(frozen=True)
class EquivalenceResult:
    code: str
    label: str                      # label_template already interpolated
    value: Decimal
    source_metric_code: str

@dataclass(frozen=True)
class ScenarioResult:
    total_kg: Decimal
    metrics: dict[str, MetricResult]        # key = metric_code
    equivalences: tuple[EquivalenceResult, ...]

@dataclass(frozen=True)
class EntryResult:
    sector_code: str
    food_category_code: str | None
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code

@dataclass(frozen=True)
class CalculationTotals:
    """The cross-entry roll-up. Computed by the engine, never by a caller."""
    current: ScenarioResult
    alternative: ScenarioResult | None
    net_benefit: dict[str, Decimal] | None  # key = metric_code

@dataclass(frozen=True)
class CalculationResult:
    factor_set_version: str
    is_mock: bool
    gwp_horizon: int
    totals: CalculationTotals
    entries: tuple[EntryResult, ...]        # request order, one per EntryInput
```

**Five rules govern these types. Each is forced by §6.2 and none of them is A's to choose.**

1. **`entries` preserves request order.** §6.2 states it, and `submission_entry.sort_order` (§2.3) exists to persist it. It is what lets C pair a result with the row the user typed.
2. **`by_destination` is populated per entry and empty at the totals level.** §6.2: the same destination can appear under several entries drawing different upstream factors, so a cross-entry destination breakdown has no single correct aggregation rule. `MetricResult` is one type either way; at the totals level the tuple is empty and the serialiser omits the key. See §6.2 for the ruling on how the front end renders that breakdown.
3. **An entry with no alternative contributes its `current` result to `totals.alternative`.** This is what §6.2's "entries without one contribute zero to it rather than being excluded, so the totals stay mass-conserving" means in code: the entry's own `EntryResult.alternative` and `EntryResult.net_benefit` stay `None`, but the totals roll-up counts its current figures on both sides, so its contribution to `totals.net_benefit` is exactly zero and `totals.alternative`'s mass equals `totals.current`'s. Excluding it instead would make the alternative lighter than the current scenario and inflate net benefit — the precise failure the dual-scenario design exists to prevent.
4. **When *no* entry carries an alternative, `totals.alternative` and `totals.net_benefit` are both `None`,** and so is every `EntryResult.alternative` / `EntryResult.net_benefit`.
5. **`EquivalenceResult.label` interpolates `{value}` in exactly one format**, defined below. Until v1.4 it was defined nowhere, and §6.2's samples were the only evidence of it.

> **`EquivalenceResult.label`: the interpolation rule.**
>
> `label` is `equivalence.label_template` (§2.2) with the single placeholder `{value}` replaced by the equivalence's own `value`, formatted as:
>
> | Aspect | Rule |
> | --- | --- |
> | Decimal places | **None.** Rounded to a whole number |
> | Rounding | `ROUND_HALF_UP`, on the `Decimal` — never through `float` |
> | Thousands separator | A comma every three digits: `18,597` |
> | Decimal separator | Not applicable; there is no fractional part |
> | Negative values | A leading `-`, same grouping. Possible: a metric total can be negative when a downstream offset dominates (§4.2) |
> | A value that rounds to zero | `0`, with **no sign**. The row above is for values that are actually negative; a magnitude that rounds away is not one. `Decimal("-0.4")` rounds to `Decimal("-0")` and `format(Decimal("-0"), ",")` is `"-0"`, so without this row the rule as written produces `Equivalent to driving -0 km` on a results page. `value` itself is unaffected and keeps its sign at full precision |
> | Anything else in the template | Copied **verbatim**. `{value}` is the only placeholder substituted, and any other brace sequence is literal text — `label_template` is staff-authored (§8.1) and must never behave as a format string |
>
> `Equivalent to driving {value} km` with `value = Decimal("18596.8200000000")` gives `Equivalent to driving 18,597 km`.
>
> **`value` itself is unaffected and is transmitted at full precision**, as a string, next to the label (§1.2). `label` is display text; `value` is the number. A consumer that wants a different presentation formats `value`, and no consumer re-derives `label`.
>
> **Why this is A's to produce and not C's.** §7.6 rule 1: the browser computes nothing. Rounding is arithmetic — a client that formatted the label itself would be the second place a number is turned into the figure a user reads, and the golden suite (§10.1) could not cover it. It is also the client's approved wording (§7.3a warns against C's hard-coded equivalence labels for the same reason).
>
> **Why it needs stating at all, in these words.** This is a convention that `tests/fixtures/calculate_response.json` instantiated to match §6.2's samples, which show `21,400` and `14,500`. Neither §2.2, §4.3 nor §6.2 defined it, so the fixtures C and D build against carried a format A had no way to know he had to reproduce — and the mismatch would be invisible to every test in the tree: `_assert_shape` compares types, not text, and a `label` reading `Equivalent to driving 18596.8200000000 km` is a well-formed string of the right type in the right key. The half-up rule in particular is **not** pinned by any fixture, because no fixture value lands on a half; it is stated because half-even would silently round `2.5` down and nobody would find out from a test. **The round-to-zero row is in the same position and for the same reason** — no fixture value rounds to zero from below, so no fixture changed when the row was added and nothing in `tests/fixtures/` would have forced anyone to write it down. Both rules are held instead by the golden suite (§10.1): `case_08_mixed_alternative_rollup` lands an equivalence on `7210.5`, where half-up and half-even disagree, and `case_07_negative_total_and_zero_label` lands one on `-0.4`.



> **`ScenarioInput` is gone.** It held `sector_code`, `food_category_code` and `lines`; those three now live on `EntryInput`, split across `current` and `alternative`. It is deleted rather than emptied down to a single `lines` field, because a surviving `ScenarioInput` is exactly what B's `api/engine_adapter.py` currently populates with `req.current.sector_code`, and a type that keeps its name while losing its meaning is the one an integration will keep using by accident.
>
> **`totals.total_kg` on the wire is `totals.current.total_kg`** (§6.2 hoists it one level). It is the current scenario's mass, and one number is enough because §6.2 now **requires** each entry's two scenarios to describe the same mass to within 0.010 kg. That rule and this hoist depend on each other: the hoist is only honest while the rule holds, and the rule is only checkable at request time, because a response carrying one mass figure cannot expose a discrepancy between two. The alternative's per-entry mass remains available as `entries[].alternative.total_kg`. This is a wire-format hoist, not a fifth field on `CalculationTotals`.

---

# 4. Calculation Engine (owner: A)

Lives in `engine/`. **Pure functions: no database access, no file access, no system clock.**

## 4.1 `FactorBundle`

A fully loaded snapshot of one factor set. Constructed by the repository layer (§5.3) and treated as read-only by the engine.

**It lives in `engine/bundle.py`**, imported as `from engine.bundle import FactorBundle`. The module is named here because two callers outside `engine/` import it — `db/repository.py`'s default bundle factory and `api/engine_adapter.py`'s `bundle_from_json` — and until v1.4 no version of this document said where it was, so both searched `engine.bundle` and then `engine.types`. A search is not a contract: it lets a layout this document does not describe work in the API and fail in the golden suite, which imports it the documented way. `engine/types.py` is §3's module and holds the frozen dataclasses only.

```python
class FactorBundle:
    version_label: str
    is_mock: bool
    metrics: tuple[MetricSpec, ...]         # active only, sorted by sort_order

    def upstream(self, sector: str, food_cat: str, destination: str,
                 metric: str) -> Decimal:
        """Exact match on destination first; then fall back to the generic row
        (destination NULL); then Decimal('0'). The generic row is the normal
        case — the destination-specific one exists so `prevention` can be a
        real 100% offset (§2.2, open item O-7). Same three-step shape as
        downstream() below, and (destination, None, metric)-style misses must
        be *looked up*, not assumed absent."""

    def downstream(self, destination: str, sector: str | None,
                   food_cat: str | None, metric: str) -> Decimal:
        """Four steps, in order (v1.31), then Decimal('0'):

            1. (sector, food_cat)   2. (sector, None)
            3. (None, food_cat)     4. (None, None)

        Both middle dimensions are nullable on `factor_downstream` (§2.2), so
        all four may exist at once and exactly one must win. Steps 2 and 3 name
        one dimension each: **the sector wins**, and §2.2 carries the three
        reasons. `sector` is a positional argument in the middle rather than an
        optional one at the end, so a caller left at the pre-v1.31 signature
        raises TypeError instead of silently reading the wrong row.

        When `sector` or `food_cat` is itself None, step 1 *is* step 3 or step
        2, so the later steps must be **looked up**, not assumed absent — the
        same caveat `upstream()` carries. May return a negative value."""

    def constant(self, code: str) -> Decimal:
        """Raises UnknownConstantError when not found."""

    def formula(self, metric: str) -> str:
        """Returns the default 'qty_kg * (upstream + downstream)' when not found."""

    def equivalences(self) -> tuple[EquivalenceSpec, ...]:
        """Active only, sorted by sort_order."""

    def has_destination(self, code: str) -> bool: ...
    def has_sector(self, code: str) -> bool: ...
    def has_food_category(self, code: str) -> bool: ...
    def standard_mix_code(self) -> str: ...

    @classmethod
    def from_json(cls, data: dict) -> "FactorBundle":
        """Build a bundle from the bundle.json shape defined in §10.2.
        Pure: no database, no file system, no clock. All decimals arrive as
        strings and are converted with Decimal(); float is never used as an
        intermediate. Raises BundleFormatError on malformed input."""

    def validate(self) -> list[str]:
        """Check internal consistency and return a list of human-readable
        problems; an empty list means the bundle is well-formed. Does not
        raise — the API layer decides how to present the problems.

        Checks: every upstream row's sector / food_category / metric exists
        in this bundle and its destination is null or exists; every
        downstream row's destination / metric exists
        and its sector and food_category are each null or exist; every
        destination.group exists; exactly one food_category has
        is_standard_mix; every formula.metric and every
        equivalence.source_metric exists."""
```

> `from_json()` is required by the golden test suite regardless (§10.1 loads a `bundle.json` per case). Dry-run requests are simply a second caller of it. `validate()` exists because a bundle arriving over HTTP may be internally inconsistent in ways a database-loaded one cannot be; the rules are engine domain knowledge and are therefore implemented once, here, rather than duplicated in the API layer.

## 4.2 Entry Points

**`calculate()` lives in `engine/calculate.py`**, imported as `from engine.calculate import calculate`. Named for the same reason as §4.1's module: `api/engine_adapter.py` is the only caller outside `engine/`, and with nothing written down it tried `engine.calculate` and then `from engine import calculate`. The second form is the dangerous one — if `engine/calculate.py` exists but exports the function under another name, it binds the *module object* and the failure becomes `TypeError: 'module' object is not callable` at the first public calculation, rather than an `ImportError` at start-up naming what is missing. Re-exporting from `engine/__init__.py` is fine; relying on the re-export is not.

```python
def calculate(req: CalculationRequest, bundle: FactorBundle) -> CalculationResult:
    """
    Evaluate every entry's current scenario (and its alternative, if present),
    roll the entries up into totals, and compute net benefit at both levels.

    Parameters
      req    : A validated request carrying one or more entries. Every
               destination, sector and food_category code must exist in
               bundle, otherwise UnknownCodeError is raised.
      bundle : Factor set snapshot.

    Returns
      CalculationResult, carrying `totals` and `entries` (§3). `entries` is in
      request order. When no entry carries an alternative, totals.alternative,
      totals.net_benefit and every entry's alternative / net_benefit are None.

    Raises
      UnknownCodeError     : a code in the request is absent from bundle
      UnknownConstantError : a formula references a constant that does not exist
      FormulaError         : syntax error, division by zero, illegal identifier
      ValueError           : gwp_horizon is neither 20 nor 100

    Guarantees
      Pure. The same (req, bundle) always yields the same result.
    """
```

**`totals` is computed by the engine, not summed in the API adapter. This is a ruling, and it is not open.**

Impact calculation happens server-side **in exactly one place**, and the engine is that place. An adapter in `api/` that adds per-entry `MetricResult.total` values together is a *second* calculation site — structurally the same defect as C's browser-side `aggregateResults`, differing only in which process the arithmetic runs in. It would put a headline figure in front of a user that no golden case can cover, because the golden suite (§10.1) exercises `calculate()` and nothing above it. Three consequences follow directly, and they are the reason this is worth a paragraph rather than a sentence:

- The number a user sees on the results page would have no test.
- A metric whose roll-up is not a plain sum — a maximum, a threshold, anything the client asks for later — would need a code change in `api/`, which is "metrics are data, not code" broken in the one layer that is hardest to notice it in.
- `POST /calculate` and the golden suite would disagree about what the calculator computes, and only the API path would be wrong.

B's `serialize_result` therefore **maps** `CalculationResult` onto the §6.2 JSON and performs no arithmetic beyond the `totals.total_kg` hoist described in §3. It adds nothing, and it must not.

> **Two §6.2 response fields have no §3 counterpart, by design, and only one of them is interesting: `factor_source`.** (The other is `token`, which comes from `upsert_submission`'s return value, §5.3, and is `null` on a dry run — it was never a candidate to come from the engine, which has no concept of a session.) `factor_source` cannot come from the engine — the engine is pure over a `FactorBundle` and has no way to know whether that bundle was loaded from the published set, from a named version, or handed to it inline in a request body. **The API layer supplies it**, from the branch it took when it resolved the bundle (§6.2.1's four-row table): `"published"`, `"version:<label>"` or `"inline"`. That is a fact the API layer already holds and the engine never had, so it is not a second calculation site and does not weaken the rule above. `factor_set_version` and `is_mock` do come from the bundle and therefore from `CalculationResult`.

Within the engine, the roll-up rules are:

| Field | Rule |
| --- | --- |
| `totals.current.metrics[code].total` | Σ over entries of that entry's metric total |
| `totals.current.total_kg` | Σ over entries of `current.total_kg` |
| `totals.current.equivalences` | Computed **from the rolled-up metric total**, not summed from the per-entry equivalence values. The conversion is linear so the two agree mathematically, but `Decimal` has finite precision and one computation is one rounding |
| `totals.current.metrics[code].by_destination` | Empty (§3 rule 2) |
| `totals.alternative` | Same rules, over each entry's `alternative` — **or its `current` where the entry has none** (§3 rule 3) |
| `totals.net_benefit` | `net_benefit(totals.current, totals.alternative)` — computed on the rolled-up scenarios, not summed from the per-entry `net_benefit` maps |

```python
def net_benefit(current: ScenarioResult,
                alternative: ScenarioResult) -> dict[str, Decimal]:
    """Per metric: current.total - alternative.total.
    Only metric codes present on both sides are included.
    Applied at both levels: per entry, and to the rolled-up totals."""
```

**Everything below `calculate()` is A's to shape.** §6.2 determines the request, the result and the roll-up rules above; it says nothing about how the engine is decomposed internally. The v0.13 helper `calculate_scenario(scenario, bundle, gwp_horizon)` no longer type-checks — `ScenarioInput` is gone and a scenario can no longer supply its own sector — so a per-scenario helper now needs the sector and food category passed alongside the lines, for example:

```python
def calculate_scenario(lines: tuple[ScenarioLine, ...], sector_code: str,
                       food_category_code: str | None, bundle: FactorBundle,
                       gwp_horizon: int) -> ScenarioResult:
    """Evaluate one scenario of one entry. Internal to the engine."""
```

That signature is illustrative, not contractual. No caller outside `engine/` may depend on it; B calls `calculate()` and nothing else.

## 4.3 Expression Evaluation

```python
def evaluate_expression(expression: str, variables: dict[str, Decimal]) -> Decimal:
    """
    Restricted expression evaluation over Python's own `ast`, in
    `engine/evaluator.py`. `evaluate` is the implemented name and
    `evaluate_expression` is an alias for it.

    Permitted
      Literals  : decimal numbers
      Operators : + - * / ( ) and unary minus
      Functions : min, max, abs, round
      Variables : only the keys present in `variables`

    Forbidden
      Attribute access, subscripting, function definitions, comprehensions,
      assignment, imports, and every builtin name

    Raises FormulaError (with line and column) on:
      syntax error / undefined variable / forbidden syntax /
      division by zero / non-finite result
    """
```

> **This is hand-written over `ast`, not built on `simpleeval`, and the change from v1.9 is only that this document now says so.** No library was removed; none was ever imported. Four things were wanted from the evaluator, and three of them are properties of holding the tree yourself.
>
> **`admin/expressions.py` is its static twin (§8.1) and the two must reach the same verdict.** The panel validates the text at save time and the engine runs it; an expression one accepts and the other refuses is a formula a staff member saves and the public then receives `FORMULA_ERROR` 500 from. The two are deliberately independent implementations of one language — one walks the tree checking node types, the other walks it computing — and they are only comparable because they are looking at the same `ast` objects. `tests/test_evaluator.py::test_the_panel_and_the_engine_reach_the_same_verdict` runs the panel's whole expression corpus through both. **When that agreement was first measured it found eight disagreements, not the three a hand survey had listed**, and in all eight the panel was right.
>
> **§4.4 requires `FormulaError` to carry `line` and `column`.** Every `ast` node already has `lineno` and `col_offset`, so the error is located at the offending token rather than at the whole expression, which is the whole point of §9.1's staff presentation.
>
> **Refusal is by default.** `_evaluate_node` names the node types it permits and refuses everything else by falling through, so a construct nobody anticipated is refused rather than admitted. That is what makes the security-boundary argument hold without enumerating what is dangerous.
>
> **Arithmetic never leaves `Decimal`.** Operands are `Decimal`, literals are converted without a `float` intermediate, and a non-finite result is refused (§1.2).
>
> **Six rules the four lines above do not spell out, all of them things the panel already enforced.** Unary `+` is refused, since §4.3 lists unary minus and nothing else. Keyword arguments are refused — `round(qty_kg, ndigits=2)` was silently dropping the keyword and evaluating `round(qty_kg)`, returning a plausible number from a formula nobody wrote. `round`'s second argument must be a whole-number literal, optionally negated, checked statically on the node rather than coerced from the evaluated value. Arity comes from a table (`min`/`max` at least two, `abs` exactly one, `round` one or two) so the verdict belongs to the whitelist rather than to a CPython builtin's signature. `**`, `%`, `//` and the bitwise and shift operators are refused, `**` because a large exponent can exhaust memory before any timeout notices. And `ast.Constant` covers every Python literal, so strings, bytes, `None` and `bool` are refused by type — `bool` by name, because it is an `int` subclass — and a `float` literal that parses to infinity (`1e999`) is refused before it can propagate as a valid `Decimal('Infinity')` through every subsequent operation.

**Formula scope: an expression computes the contribution of a single line. Summation is performed by the engine.**

```
line_value   = evaluate_expression(expr, vars)
metric_total = Σ line_value
```

Variables available on each line:

| Variable | Type | Meaning |
| --- | --- | --- |
| `qty_kg` | Decimal | Quantity sent to this destination, in kilograms |
| `upstream` | Decimal | Upstream factor per kilogram |
| `downstream` | Decimal | Downstream factor per kilogram; may be negative |
| `const_<CODE>` | Decimal | A constant, as a flat identifier, e.g. `const_GWP_CH4_100` |
| `const_GWP_CH4` | Decimal | **Special binding:** resolves to `GWP_CH4_20` or `GWP_CH4_100` according to the request's `gwp_horizon` |

Because no aggregation is required, the expression language has **no arrays, no loops and no `sum()`**, which keeps the evaluator's security boundary unambiguous.

> **This table did not change in v1.8, and that is the interesting part of O-7's closing.** `upstream` is now resolved with the line's destination taken into account (§2.2, §4.1) — but the resolution happens in the *lookup*, before the evaluator is called, so the variable set, the language and `admin/expressions.py`'s `BASE_VARIABLES` are all exactly as they were. The rejected alternative would have added a fifth variable here and required the panel's whitelist to gain the same member in lockstep with the engine's. A formula never names a destination and never should.

**Default formulas**

| Metric | Expression |
| --- | --- |
| `mass` | `qty_kg` |
| `co2e` | `qty_kg * (upstream + downstream)` |
| `ch4` | `qty_kg * (upstream + downstream) * const_GWP_CH4` |
| `water` | `qty_kg * upstream` |
| `cost` | `qty_kg * (upstream + downstream)` |

## 4.4 Exceptions

All defined in `engine/errors.py`, deriving from `EngineError`.

| Exception | Trigger | Mapped API error |
| --- | --- | --- |
| `UnknownCodeError` | Request contains a code absent from the bundle | `UNKNOWN_CODE` (400) |
| `UnknownConstantError` | Formula references a missing constant | `FORMULA_ERROR` (500) |
| `FormulaError` | Expression is invalid | `FORMULA_ERROR` (500) |
| `BundleFormatError` | `from_json()` received malformed input | `VALIDATION_ERROR` (400) |

`FormulaError` carries **structured attributes**, not a pre-formatted message:

```python
class FormulaError(EngineError):
    expression: str
    line: int
    column: int
    reason: str
```

The fields must be separable because §9 gives this error two presentations: opaque for public requests (the expression is never echoed), fully located for authenticated dry runs (staff tuning a formula cannot fix what they cannot see).

---

# 5. Repository Layer (owner: B)

Lives in `db/repository.py`. **The only code in the system that touches the database.** No other module may import SQLAlchemy.

## 5.1 Taxonomy

```python
def get_taxonomy(session) -> TaxonomySnapshot:
    """The active taxonomy rows the PUBLISHED factor set covers, sorted by
    sort_order. Serialised directly by GET /api/v1/taxonomy. See §6.1 for
    what "covers" means and for the two rows that are never filtered."""
```

> **This returned every active row until v1.21, and that is what made the calculator offer destinations the published set prices at nothing.** The rule and its two protected rows are stated once, under §6.1. `get_taxonomy_for_bundle` — a different function, feeding the engine and §6.3 — is **not** narrowed and must stay a superset.

```python
@dataclass(frozen=True)
class TaxonomySnapshot:
    sectors: tuple[SectorSpec, ...]
    food_categories: tuple[FoodCategorySpec, ...]
    destination_groups: tuple[DestinationGroupSpec, ...]
    destinations: tuple[DestinationSpec, ...]
    metrics: tuple[MetricSpec, ...]
    unit_presets: tuple[UnitPresetSpec, ...]
    factor_set_version: str
    factor_set_is_mock: bool
```

## 5.2 Factor Sets

```python
def get_published_factor_set_id(session) -> int:
    """Raises NoPublishedFactorSetError when no version is published."""

def load_factor_bundle(session, factor_set_id: int | None = None) -> FactorBundle:
    """Loads the currently published version when factor_set_id is None.

    The result must be cached in a slot keyed by factor_set_id, and
    invalidated on publish or rollback. A single overwrite-on-load cache is
    not acceptable: staff repeatedly dry-running a draft would otherwise
    either evict the published bundle continuously, or — far worse — serve
    draft factors to a public request. Inline bundles supplied over HTTP
    (§6.2) are never cached; they differ on every request.

    **Only a `published` set is cached (v1.4).** A draft or archived set is
    rebuilt on every load."""

def find_missing_prevention_upstream(
        session, factor_set_id: int) -> list[tuple[str, str, str]]:
    """Which (sector, food_category, metric) tuples of this set would still
    charge a prevented line an upstream factor (§2.2, open item O-7).

    A tuple qualifies when it has a general upstream row (destination NULL)
    and **no `is_prevention` destination has a row at zero for it** — whether
    every such row is absent or each carries a non-zero value. Existence is
    not the rule; the value is (v1.11). "No flagged destination", not "every
    flagged destination" (v1.22): more than one row may carry the role and a
    set built for one vocabulary will never hold rows for another, so one
    working offset per tuple is what this guarantees. Returns codes, not ids
    (§1.1), sorted, so a caller can put them straight into a message a human
    has to act on. An empty list is the healthy state. Empty also when the
    taxonomy has no prevention destination at all — that is an unseeded
    database rather than an incomplete factor set, and it is
    admin/taxonomy_rules.check_prevention_destination's to refuse."""

def prevention_destination_codes(session) -> frozenset[str]:
    """Every destination code flagged `is_prevention` (§2.1), for §6.2's
    current-scenario rule. Reads the taxonomy, not a factor set: `destination`
    carries no `factor_set_id`, so the answer is the same for a dry run as for
    a public request. Not restricted to active rows — a deactivated prevention
    row is still one, and a caller naming it in a current scenario must be
    refused rather than told the code is unknown."""

def refuse_nonzero_prevention_factors(session, factor_set_id: int) -> None:
    """Raises FactorSetStateError when any factor_upstream or
    factor_downstream row of this set prices an `is_prevention` destination at
    something other than zero (v1.22). An absent row stays legal: §4.1's
    lookup already returns zero for one, and §6.1's hold-out is built around a
    set that prices a prevention destination nowhere at all."""

def publish_factor_set(session, factor_set_id: int, actor: str) -> None:
    """Within one transaction: archive the current published set, publish the
    target, write an audit_log entry, invalidate the cache. Rolls back if the
    'at most one published' invariant would be violated.

    **Also refuses, naming the tuples, when find_missing_prevention_upstream
    is non-empty (v1.9), and refuses a non-zero prevention factor row
    (v1.22).** Publishing is where both are checked because it is the single
    transactional choke point; a form-level guard cannot see a row that has
    not been written yet."""

def rollback_to(session, factor_set_id: int, actor: str) -> None:
    """Restores an archived version to published. Same semantics as publish,
    **except the O-7 completeness check and the non-zero prevention refusal,
    which rollback deliberately does not apply** — a set archived before v1.8
    will legitimately fail the first, and refusing an emergency rollback over
    a completeness rule is a worse failure than the one the rule prevents."""

def clone_factor_set(session, source_id: int, new_label: str, actor: str) -> int:
    """Deep-copies a version into a new draft (all factors, constants,
    formulas and equivalences) and returns the new id. This is the
    recommended path for staff edits: clone, edit, publish."""
```

> **Why the draft slot exists but is not filled.** Partitioning by
> `factor_set_id` is what stops a dry run evicting the published bundle, and
> that requirement stands. Caching the draft *as well* satisfies the sentence
> above literally and defeats the thing the draft path exists to support:
> §8.1's CRUD screens write `factor_upstream`, `factor_downstream`,
> `constant`, `formula` and `equivalence` rows directly and have no reason to
> call `invalidate_factor_bundle` — only `publish_factor_set` and
> `rollback_to` do — so the first dry run of a draft pins that draft's numbers
> for the lifetime of the process. A staff member edits a factor, re-runs the
> dry run, sees the old figure, and cannot tell that from a formula that
> ignores the column they just changed. That is precisely the confusion
> `factor_source` (§6.2) was added to prevent, arriving by another route.
>
> The alternative fix — an invalidation hook on every admin write path —
> touches all eleven §8.1 views and has to be remembered by whoever adds the
> twelfth. Not caching is one branch in one function, and there is no load
> argument on the other side: a draft is dry-run by one staff member at a
> time, while the published set serves every public request.

> **Where these live today.** `admin/factor_lifecycle.py`, not
> `db/repository.py`. The admin panel needs them and the repository
> implementation is on an unmerged branch; when it lands, one implementation
> goes and the other is imported. `admin/` may import from `db/`, never the
> reverse. Cache invalidation is the repository's half and is not implemented
> in the admin copy.
>
> **This duplication is why v1.9's O-7 check is enforced twice and written
> once.** The panel calls the `admin/` copy and nothing outside its own tests
> calls the repository's, so a guard placed only in `db/repository.py` would
> leave the staff path — the only path a human takes — entirely unguarded,
> while a guard placed only in `admin/` would vanish the day the two are
> unified. `find_missing_prevention_upstream` lives in `db/repository.py` and
> the `admin/` copy imports it, which is the legal direction; each raises its
> own layer's exception type. When the implementations merge, one call site
> goes and the query does not move.

## 5.3 Submissions

```python
def upsert_submission(session, token: str | None, req: CalculationRequest,
                      factor_set_id: int) -> tuple[int, str]:
    """
    Upsert keyed by token. One call, one submission, N entries.

    token is None, unparseable, or does not resolve to a live submission
                             -> insert a new row and mint a new UUID4 token.
    token is valid           -> overwrite that row and its whole entry set.

    `req` is a §3 CalculationRequest and therefore carries `req.entries`, not
    a single `req.current`. Writing the submission means writing three tables:

      submission        one row; stamps factor_set_id and req.gwp_horizon
      submission_entry  one row per req.entries[i], with sort_order = i so
                        the response's entries[] can be paired back to the
                        rows on the user's screen (§2.3)
      submission_line   one row per line, per scenario, per entry, keyed on
                        submission_entry_id

    On overwrite the entry set is rebuilt, not patched: delete every
    submission_entry for this submission (ON DELETE CASCADE takes the lines
    with it) and insert the request's entries in order. Patching in place
    would have to reconcile an entry the user removed against one they added,
    and the entries have no client-supplied identity to reconcile on.

    Returns (submission_id, token). The token is always returned so the
    front end can store it in sessionStorage.
    """

def expire_tokens(session, now: datetime) -> int:
    """Nulls the token column for rows whose token_expires_at is in the past,
    retaining the data. Returns the number of rows affected.
    Called by a scheduled job."""
```

## 5.4 Statistics

```python
def get_public_stats(session, threshold: int = 5) -> PublicStats:
    """
    Aggregate public statistics.

    - Excludes every entry and every line belonging to a submission with
      excluded_from_public = TRUE. That column is on `submission` (§2.3),
      so each breakdown below joins one table further than its own
      grouping needs — up to `submission`, not merely to the entry.
    - Merges any bucket with count < threshold into 'other'
    - Then, while 'other' is itself below the threshold, merges the
      smallest remaining visible bucket into it as well; if even that
      cannot clear it, returns NO buckets for that breakdown (v1.5)
    - Suppression happens here and is never delegated to the front end

    EVERY BREAKDOWN READS THE CURRENT SCENARIO ONLY.
    THE UNIT OF AGGREGATION IS THE ENTRY, NOT THE SUBMISSION (§2.3).

      by_sector         group over submission_entry.sector_id,
                        restricted to entries having current-scenario lines
      by_food_category  group over submission_entry.food_category_id, same;
                        a NULL food_category_id groups into an explicit
                        'unspecified' bucket -- it is never dropped
      by_destination    group over submission_line joined to its entry and
                        on to its submission,
                        WHERE submission_line.scenario = 'current'
      total_kg          summed over the same current-scenario lines
      total_calculations  counts submission rows

    A submission with three entries is three sector observations. Joining
    by_sector to `submission` instead — which is what a schema-driven reading
    of the old single-sector shape produces — counts a multi-stage food
    business once, as whichever stage it happened to enter first. That is the
    exact population the calculator is most useful to and least able to
    describe, and the query returns a plausible number either way, so nothing
    fails and nobody notices.
    """

@dataclass(frozen=True)
class StatsBucket:
    code: str          # taxonomy code, 'unspecified', or 'other'
    label: str
    count: int         # ENTRIES in this bucket, not submissions
    share: Decimal     # 0..1, of the entry count within this breakdown
    total_kg: Decimal  # CURRENT-scenario mass only

@dataclass(frozen=True)
class PublicStats:
    generated_at: datetime
    total_calculations: int      # SUBMISSIONS, not entries
    suppression_threshold: int
    by_destination: tuple[StatsBucket, ...]
    by_sector: tuple[StatsBucket, ...]
    by_food_category: tuple[StatsBucket, ...]
```

> **`submission_line.scenario` must be filtered, and this is the single easiest way to make the public statistics false.** The column is `ENUM('current', 'alternative')` (§2.3) and both scenarios' lines sit in the same table. A `by_destination` query that groups over `submission_line` without a scenario predicate — which is what the instruction above reads like if you stop before the `WHERE` — counts every hypothetical line as real waste. **`prevention` then appears as a destination in the public chart**, and `prevention` is by construction the destination for waste that *did not happen*; every `total_kg` roughly doubles; and §6.4's "the cumulative total entered into this tool" becomes false on its face, on the page whose whole design problem is not overclaiming. The alternative scenario is a user's what-if. It is not an observation of anything and it does not belong in a statistic.
>
> **A NULL `food_category_id` is a bucket, not a gap.** `submission_entry.food_category_id` is nullable and §2.3 already says what NULL means: the user did not break their waste down by type. That is a real answer about a real submission, and it is likely to be a common one — the calculator is aimed at businesses that mostly do not weigh their waste by food type. It groups into an explicit **`unspecified`** bucket, carrying the same `count`, `share` and `total_kg` as any other, and it is **subject to the same suppression threshold** as any other. It is never silently dropped: `share` is computed within its own breakdown and the shares must sum to 1, so discarding a bucket does not remove a number from the page — it inflates every other share on it, in the direction of overclaiming. D renders it with one rule, like every other bucket; only the label is special ("Not broken down by type").
>
> Note that `unspecified` is a *storage* fact, not a calculation one. The engine resolves a null `food_category` to `standard_mix` before it computes anything (§3, §6.2), so the same entry is `standard_mix` to the engine and `unspecified` to the statistics. Both are correct: one is the factor that was applied, the other is what the user actually told us. **Do not resolve NULL to `standard_mix` here** — that would report a composition the user never claimed, and would make `standard_mix`'s share indistinguishable from the users who selected it deliberately.
>
> **`other` is subject to the threshold like every other bucket, and clears it by absorbing more (settled in v1.5).** Until then, a single sub-threshold bucket was republished verbatim under a new name — `{"code": "other", "count": 1, …}` carrying its exact `total_kg` — which hid the label and nothing else. **In combination it was worse than alone**: `by_sector`'s `other` and `by_food_category`'s `other` are formed from the same entries, so an `other` of one published that user's exact tonnage in three breakdowns at once, and the three rows join on sight because the counts and masses are identical.
>
> The rule, in order:
>
> 1. Merge every bucket below the threshold into `other`, as before.
> 2. **While `other` exists and is itself below the threshold, merge the smallest visible bucket into it too**, and repeat. Smallest first, because it raises `other`'s count fastest per bucket surrendered and costs the page its least informative row first.
> 3. **If merging every bucket still leaves `other` below the threshold, publish no buckets for that breakdown at all** — an empty array, with `total_calculations` standing alone. That is a data set so small that every row in it is identifying, and there is no arrangement of it that is both useful and safe. `total_calculations` is a count of submissions and identifies nobody, so it still reports.
>
> **This does not break the shares property.** `other` remains an ordinary bucket carrying every suppressed entry, so the denominator — computed before suppression — is untouched and §6.4's "`share` … does sum to 1" holds exactly as before. An empty breakdown has no shares to sum. The alternatives all cost something the merge-rather-than-drop rule was written to protect: *dropping* `other` removes its entries from that denominator and makes the shares false; *re-normalising* inflates every remaining share by the suppressed mass, in the direction of overclaiming; *folding `other` into the largest visible bucket* hides a small number inside a large one and misattributes its tonnage. Absorbing costs one row of resolution and nothing else.
>
> Raised as `docs/ToB_v2.0.md` S6 and recorded as open in v1.4. Decided in favour of privacy: a statistics page that cannot describe its smallest cohort is a page with one fewer row, while a page that describes it is a page that identifies it.

> **`total_calculations` and the bucket counts are deliberately counting different things, and the statistics page must not present them as if they were not.** `total_calculations` is submissions; every `StatsBucket.count` is entries. `Σ by_sector[].count` is therefore ≥ `total_calculations`, and the gap is exactly the number of multi-entry submissions. `share` is computed within its own breakdown — over entries — so shares still sum to 1 and are the safe figure to display. Copy that reads "1,247 calculations" beside a sector chart whose counts add to 1,600 invites the obvious question; the honest phrasing names the unit ("1,247 calculations, covering 1,600 points in the supply chain"). See §6.4's copy constraint, which is D's.

## 5.5 Audit

```python
REDACTED_FIELDS = {"password_hash", "mfa_secret_enc", "secret_enc", "code_hash",
                   "ip_hmac", "token", "unclaimed_password_enc",
                   "initial_password_enc"}

def write_audit(session, actor: str, action: str, table_name: str,
                row_id: int | None,
                before: dict | None, after: dict | None) -> None:
    """The only code that inserts into audit_log.

    Runs inside the caller's transaction: a change that is rolled back must
    leave no audit record claiming it happened.

    Every key in REDACTED_FIELDS is replaced with "[redacted]" before
    serialisation. Written when audit_log was readable by every staff
    member; v1.15 closed /admin/audit-log to role=admin only, and the set
    is unchanged all the same. Narrowing the audience is not a reason to
    widen what is written: these are credentials, the trail is exportable
    and append-only, and a redaction dropped because "only administrators
    see it now" is one that has to be found again the first time a
    read-only auditor role exists.

    unclaimed_password_enc (v1.15; named initial_password_enc until v1.16,
    and both names are in the set for the reason mfa_secret_enc's old name is)
    is there for a reason of its own: it is the one reversibly-encrypted
    credential in the system, and its whole design is that it stops existing
    when the password is claimed. A copy in an append-only table would outlive
    that by the life of the deployment. It is the *ciphertext* that is
    redacted, and that is not belt-and-braces: the key is derived from
    SECRET_KEY, which anything able to read audit_log already has.

    ip_hmac is in that set for a different reason again:
    it is not a credential, it is derived from a visitor's address, and
    §2.3 permits storing such a derivation in exactly one place — the
    ip_block table, which only administrators can read. audit_log has a
    wider audience, so the same value must not reach it.

    Decimal values serialise as strings, never as float (§1.2).
    """
```

> **Why `ip_hmac` is redacted, and why the list said three for four revisions.** The set above is the v0.3 definition; `ip_hmac` did not exist then. E-8's final review round added it to `admin/audit.py`'s copy, because flipping `can_delete = True` on the blocklist screen would otherwise serialise a whole `IpBlock` row through `row_to_dict` and write the fingerprint into `audit_log` — §8.3 has stated that this list carries it ever since, while this list did not. **The definition was the stale half, not the claim.**
>
> **Why `token` is redacted, and why it is here before the view that would leak it.** `submission.token` (§2.3) is a live session token. §8.2 specifies `/admin/submissions` as a record-level moderation screen that sets `excluded_from_public`; the moment it is built on `AuditedModelView`, `write_audit` snapshots the whole row and copies that token into `audit_log` — readable by every staff member, never expiring, and out of reach of `expire_tokens`, which nulls the column on `submission` and knows nothing about copies of it. **§2.3's guarantee is that the linkage is severed after an hour**, and a copy in an audit row makes that guarantee false for every moderated submission, permanently. Like `ip_hmac` it is not a credential; like `ip_hmac` it is the one field tying a stored row back to a person's browser session, which is the thing this system's stated design promises not to keep. Added in v1.5, ahead of the screen, because a redaction added after the screen ships is a redaction that arrives after the rows do.
>
> This matters more after B's integration than before it. `db/repository.py`'s `write_audit` becomes the canonical one and `admin/audit.py` becomes a re-export of it (§8.3), so E's copy — the one that already redacts `ip_hmac` — stops being the code that runs. An API-side automatic block writes its audit entry through **this** function.

Two callers, and no others:

| Caller | Writes |
| --- | --- |
| `AuditedModelView` (§8.1) | Every admin CRUD operation |
| `publish_factor_set` / `rollback_to` / `clone_factor_set` (§5.2) | Factor-set lifecycle operations |

> v0.1 required that "all writes go through the repository functions in §5", but §5 defines no write function for any of the eleven taxonomy and factor tables, so nothing could satisfy it. Adding thirty-odd boilerplate CRUD functions to the repository would not help either: they would have exactly one caller (the admin panel), and `sqladmin`'s `insert_model` / `update_model` / `delete_model` would all have to be overridden to route through them. The requirement is therefore stated on the outcome — every write produces an audit entry — with a single insertion point to keep the format uniform.

---

# 6. REST API (implemented by B; consumed by C and D)

Base path `/api/v1`. All responses are `application/json; charset=utf-8`.

## 6.1 `GET /api/v1/taxonomy`

Called once on page load to build every dropdown and input row.

**It returns the vocabulary the published factor set covers, not every active row (v1.21).**

> **Why this is not a filter on top of the taxonomy but the definition of what the taxonomy endpoint is for.** §2.1's `sector`, `food_category` and `destination` are global tables with no `factor_set_id`: a factor set brings factors, not a vocabulary. So until v1.21 publishing a set could not narrow the form, and a user who typed a quantity against a destination the published set has no factors for got **a silent zero — with nothing on the form to distinguish it from an error, or from a genuine zero.** The form is a promise that the calculator can price what it offers.
>
> **It was visible first with a second vocabulary and it was never only that vocabulary's.** §10.3's ReFED fixture puts `refed_`-prefixed rows in the same tables; with it loaded the endpoint offered 26 destinations against a set that prices 12, and every New Zealand destination in that list was dead. But `MOCK-v0` prices 6 destinations of 14 and 3 sectors of 6, so most of the New Zealand form is a silent zero **today**, and this rule is what stops the deliverable claiming otherwise. The rows return the moment the client's real factors are loaded — one import, no code change, which is Decision 2 doing its job.

| Row | Covered when |
| --- | --- |
| `destination` | it has at least one `factor_downstream` row in the published set — **including the `food_category_id IS NULL` row**, which §2.2 defines as "every food category" and which is how a per-tonne charge like the waste levy is held — **or** it appears as a non-NULL `factor_upstream.destination_id` (the O-7 column, v1.8) |
| `sector` | it appears as `factor_upstream.sector_id`, **or** as a non-NULL `factor_downstream.sector_id` (v1.31) |
| `food_category` | it appears as `factor_upstream.food_category_id`, **or** as a non-NULL `factor_downstream.food_category_id` |
| `destination_group` | at least one covered destination belongs to it. An empty group is omitted; no `destinations[].group` may ever name a group the response omits |
| `unit_preset` | its `food_category` is null (applies to every category) or names a covered food category. **Ordered by `kg_per_unit`, smallest first (v1.33)** — this is the one taxonomy table with no `sort_order`, the list is a `<select>` a visitor scans for their own bin, and alphabetically by `code` the 1100 L front-loader sorted above the 660 L one. `code` breaks the tie. **The front end renders this order as given and sorts nothing** |
| `metric` | **always** — metrics are the output vocabulary and nothing a user types is one |

> **Both halves of the destination rule are needed because both factor-set shapes exist.** A set built the New Zealand way carries one generic upstream row per `(sector, food_category, metric)` and a `prevention` override, so `factor_upstream.destination_id` is where its only per-destination information lives; a set built the ReFED way carries an explicit upstream row per destination. Reading one table loses one shape.
>
> **Both halves of the sector rule are needed for the mirror-image reason (v1.31).** `factor_downstream.sector_id` is nullable and NULL means "every sector", so a NULL row is no evidence about any particular sector and is skipped — exactly as a NULL `food_category_id` already is on the row below. The non-NULL half is not redundant with the upstream read: a set may legitimately price a stage of the supply chain **downstream only** — a per-tonne disposal charge that differs by collection contract, with no upstream footprint of its own — and reading `factor_upstream` alone would drop that sector from the form while the rows pricing it sat in the database, which is precisely the silent zero this whole rule exists to remove. The New Zealand set is unaffected: all of its downstream rows are NULL here and its sectors come from `factor_upstream` as before.

> **Every `is_prevention` destination and the `is_standard_mix` food category are never filtered out.** A prevention destination's factors are zero **by construction** — that is the whole of what makes it a 100% offset and what keeps the two scenarios mass-conserving (§6.2) — so an absence of factor rows is *not* evidence a set does not support it, which is the inference this endpoint makes for every other row. Flagged rows are held out **by the flag** (§2.1) and their groups are kept with them; this read `db.types.PREVENTION_CODE` until v1.22, which subjected every *other* vocabulary's prevention row to an inference that cannot be true of it. `prevention` happens to be covered in any set `publish_factor_set` will accept, since that refuses a set whose generic upstream rows have no matching zero override — **a coincidence of two other rules, not a guarantee**, and the improvement panel is unusable the day it stops holding.
>
> **The cost, stated rather than hidden.** Under a published set of one vocabulary the *other* vocabulary's prevention row is also offered. That crossing already ran in the other direction before v1.22 and is accepted for the same reason. §6.2 refuses every flagged destination in a *current* scenario outright, so the exposure is confined to the alternative scenario, where a line to a foreign prevention destination falls back to the generic upstream row and is charged for it — which **understates** the benefit of wasting less. It is bounded to a database holding two vocabularies at once, and it errs in the direction this project cares about.
>
> The **standard mix** is kept for the structural half of the same reason: §2.1 requires exactly one active row to carry `is_standard_mix` and §6.2 resolves a null `food_category` to it, so filtering it out would leave a consumer with no legal way to say "composition unknown" while the server went on resolving null to a code the consumer was never offered. §2.1's "exactly one active row" invariant is still counted over the **active** rows rather than the narrowed ones.

> **A consumer must not assume this list is stable across a publish.** The front end fetches it once per page load and holds it in `state.taxonomy` (§7.2) — it caches nothing across loads, and nothing here may be cached in `localStorage`, because a taxonomy fetched before a publish is a form offering codes the current set does not price, which is the defect this rule closes arriving by another door.

**200 response**

```json
{
  "factor_set": { "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true },
  "sectors": [
    { "code": "processing", "name": "Processing / Manufacturing",
      "description": "…", "sort_order": 2 }
  ],
  "food_categories": [
    { "code": "standard_mix", "name": "Standard mix (composition unknown)",
      "is_standard_mix": true, "sort_order": 0 }
  ],
  "destination_groups": [
    { "code": "disposal", "name": "Disposal", "is_waste": true, "sort_order": 3 }
  ],
  "destinations": [
    { "code": "landfill", "name": "Landfill", "group": "disposal",
      "description": "…", "is_prevention": false, "sort_order": 1 }
  ],
  "metrics": [
    { "code": "co2e", "name": "Greenhouse gas", "unit": "kg CO2e",
      "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 1 }
  ],
  "unit_presets": [
    { "code": "wheelie_bin_240l", "label": "240 L wheelie bin (full)",
      "food_category": null, "kg_per_unit": "69.6000" }
  ]
}
```

> **`display_unit` is a presentation variant of `unit` at the same scale. It is never a different scale, and nothing anywhere converts between the two.** A typographic difference — `kg CO₂e` against `kg CO2e` — is what the column is for. It is not a unit conversion, and the example above is written with the two identical for that reason.
>
> **The rule is forced by §7.6.1 rather than chosen.** Every figure the front end prints comes from the API, and the only arithmetic it may perform is unit conversion on what the *user typed*, in `units.js`. So there is no layer that could divide a `kg CO2e` total by 1,000 on its way to a `t CO2e` label: the number would simply be relabelled, and every greenhouse-gas figure on the page would read a thousand times too small. §6.2 returns each metric total in `unit`, and a consumer that has both should prefer the `unit` travelling with the figure.
>
> **This was live in the fixtures.** `tests/fixtures/taxonomy.json` and `admin/seed.py` carried `t CO2e` against `kg CO2e`, `kL` against `L` and `t` against `kg` until 2026-08-09, and C's results table rendered "3,993 t CO2e" and "1,530,000 kL" one section below the same two figures labelled correctly. Both were corrected; the seed matters more than the fixture, because it is what ships to the database. **If a metric should be reported in tonnes, that is the metric's `unit` and the formula produces tonnes** — a metric's scale is a property of its formula, which is data (§2.1), not of a label.

## 6.2 `POST /api/v1/calculate`

**Calculates and persists. One call equals one submission** (Decision 8).

**A submission carries one or more entries.** A food business has waste at more than one point in the supply chain, and each point has its own sector, its own food category and therefore its own upstream factor — so they cannot be folded into a single set of lines. Each entry is one `(sector, food_category)` pair with its own scenario lines.

> **Why the whole submission travels in one call.** The alternative — one request per entry — breaks three things at once. `token` keys an upsert (§5.3), so the second entry would overwrite the first and only the last would survive. The client would have to add the per-entry results together itself, which puts an impact number in the browser that the engine never produced and that no golden case can cover (Decision 6). And one user action would cost N requests against a 120/hour limit. **Every number the user sees is computed server-side, including the totals across entries.**

**Request**

```json
{
  "token": "3f2b… (optional; omitted on the first call)",
  "gwp_horizon": 100,
  "entries": [
    {
      "sector": "processing",
      "food_category": "dairy",
      "current": [
        { "destination": "landfill", "qty_kg": "1200.000" },
        { "destination": "animal_feed", "qty_kg": "300.000" }
      ],
      "alternative": [
        { "destination": "anaerobic_digestion", "qty_kg": "1200.000" },
        { "destination": "animal_feed", "qty_kg": "300.000" }
      ]
    },
    {
      "sector": "primary_production",
      "food_category": "vegetables",
      "current": [ { "destination": "not_harvested", "qty_kg": "800.000" } ],
      "alternative": [ { "destination": "prevention", "qty_kg": "800.000" } ]
    }
  ],
  "dry_run": null
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token` | string \| null | No | Session token; omitted on the first call. Any value that does not resolve to a live submission is treated as absent and a new one is minted — a stale `sessionStorage` value must not produce an error |
| `gwp_horizon` | int | No | 20 or 100; defaults to 100. Applies to the whole submission |
| `entries` | array | Yes | At least one entry |
| `entries[].sector` | string | Yes | Must exist in the taxonomy |
| `entries[].food_category` | string \| null | No | Null is treated as `standard_mix` |
| `entries[].current` | array | Yes | At least one line |
| `entries[].alternative` | array \| null | No | Null means no comparison is performed **for that entry** |
| `dry_run` | object \| null | No | **Staff only**; see §6.2.1. Requires `X-Dry-Run: true` |

If **any** entry carries an `alternative`, the response carries `net_benefit` at both levels; entries without one contribute zero to it rather than being excluded, so the totals stay mass-conserving.

> **The two scenarios of an entry must describe the same mass, and until v1.2 nothing enforced it.** The dual-scenario design rests on this: `architecture.md` §4.1 states that the `prevention` destination exists precisely so that `net_benefit` cannot be inflated by simply assuming less waste in the alternative — "wasting less" is expressed by *moving* mass to `prevention`, whose factors are all zero, not by sending less of it. That is a 100% offset and it conserves mass by construction.
>
> **Both halves of that sentence now hold, and the two are independent.** The mass half is a property of the *request* and has always held — this rule is what enforces it. The "100% offset" half did not hold as built, and `architecture.md` **O-7** measured the gap: only `prevention`'s *downstream* factors were zero, so a prevented line kept its entry's upstream factor. **O-7 closed in v1.8** — `factor_upstream` gained a nullable `destination_id` (§2.2) and `prevention` carries an upstream row at zero. The independence is worth keeping in mind: this rule was never weakened by O-7 and is not strengthened by its closing, so if the offset ever stops being complete again, *this* check will not be the one that notices.
>
> **Without a rule, an implementer building from §6.2 alone permits exactly what `prevention` was designed to prevent**, and nothing downstream exposes it. An alternative that simply drops a 1,200 kg landfill line produces a large, entirely fictitious `net_benefit`. The response cannot reveal it: `totals.total_kg` reports the **current** scenario's mass only (§3), so the two figures a reader would compare are never both on the page. The golden suite cannot catch it either — it exercises `calculate()` against a fixed request, and this is a property of the *request*. The only place it can be caught is here.
>
> **The tolerance is absolute — 0.010 kg — and it is derived from this contract's own limits rather than picked.** The front end builds alternative lines by allocating percentages of an entry total and rounding each line independently to 3 decimal places (§7.3a, `improvement.js`), so each line carries at most 0.0005 kg of rounding error, and §6.2 already caps a scenario at 20 lines per entry. 20 × 0.0005 = 0.010 kg, and that bound holds regardless of the tonnage involved. A **relative** tolerance would be wrong in both directions: at 5,000 tonnes even 0.01% is 500 kg, which is looser than the defect this rule exists to catch, and at 2 kg it is tighter than the rounding the front end unavoidably produces, rejecting a legitimate request the user cannot fix.
>
> `details[].field` points at `entries[i].alternative` — the whole array, not a line, because no single line is at fault.

**Validation rules (enforced server-side)**

| Rule | On violation |
| --- | --- |
| `qty_kg >= 0` | `VALIDATION_ERROR` |
| `qty_kg` has at most 3 decimal places | `VALIDATION_ERROR` |
| Per line `qty_kg <= 10,000,000` | `VALIDATION_ERROR` |
| Per scenario total, per entry `<= 50,000,000` | `VALIDATION_ERROR` |
| Per scenario line count, per entry `<= 20` | `VALIDATION_ERROR` |
| Entry count `<= 20` | `VALIDATION_ERROR` |
| **Per entry carrying an `alternative`: `\|Σ alternative.qty_kg − Σ current.qty_kg\| <= 0.010`** | `VALIDATION_ERROR`, `field` = `entries[i].alternative` |
| **No `destination.is_prevention` line in a `current` scenario** | `VALIDATION_ERROR`, `field` = `entries[i].current`, `issue` = `prevention_in_current` |
| No duplicate `destination` within one entry's scenario | `VALIDATION_ERROR` |
| No duplicate `(sector, food_category)` across entries | `VALIDATION_ERROR` |
| All codes exist | `UNKNOWN_CODE` |
| `dry_run` present without `X-Dry-Run: true` | `VALIDATION_ERROR` |
| `dry_run.factor_set_version` and `dry_run.bundle` both non-null | `VALIDATION_ERROR` |
| `dry_run.bundle` row count across all tables `<= 5000` | `VALIDATION_ERROR` |
| `dry_run.bundle` fails `FactorBundle.validate()` | `VALIDATION_ERROR`, one `details` entry per problem |

> **Why a prevention destination in a `current` scenario is a rejection and not a curiosity.** Until v1.5 nothing on the server refused it — only C's own UI, which never offers it in the current column. A hand-rolled request carrying it persists an ordinary `submission_line` with `scenario = 'current'`, and §5.4 selects exactly that, so the line becomes a bucket in the public `by_destination` chart. A prevention destination is where waste that *did not happen* goes; counting it as real waste is the failure §5.4's scenario predicate exists to prevent, arriving through the one door that predicate cannot close — the predicate excludes the alternative scenario, and this line is not in the alternative scenario. It also makes no sense as an input: the current scenario is a description of what a business is doing now, and "we sent 900 kg to not existing" is not a description of anything. `tests/api/test_fixture_consistency.py` asserted this of the *fixture*, which is what made it look covered; a fixture constrains the fixture.
>
> **The rule tested one literal until v1.22, and that is how it missed `refed_prevention`.** §10.3's fixture puts a second vocabulary's prevention row in the same global tables; it carries 156 upstream and 156 downstream rows, every one of them zero, and it was refused by nothing. The whole defect above therefore stayed live for it, on the deployment that has the ReFED set published and offers the row on the form. The rule now reads `destination.is_prevention` (§2.1) by way of `db.repository.prevention_destination_codes`, which is also why it is checked in `entry_rule_problems` rather than in Pydantic: the set of prevention codes is a database read, and a field validator has no session. `details[].field` is unchanged; `details[].issue` is now the stable slug `prevention_in_current` rather than Pydantic's generic `value_error`.

> **`food_category: null` and `"standard_mix"` are the same thing to the engine and different things to the duplicate check.** Two entries with the same sector, one carrying `null` and one carrying `"standard_mix"`, are **both accepted** — the duplicate rule compares the values as sent. They then draw identical upstream factors, appear as two entries in the response, and count as two entries in §5.4's `by_sector`, so one supply-chain point is described twice. This is deliberate and it follows from §5.4, which keeps the two distinct on purpose: `unspecified` records that the user did not break their waste down, `standard_mix` records that they chose the mixed-composition figure, and collapsing them here would make the statistics unable to tell those apart. It is written down because it is the kind of asymmetry that reads as a bug — the field table two paragraphs up says "Null is treated as `standard_mix`", and that is true of the *factor lookup* and of nothing else. A front end should send one or the other consistently and never both for one sector.

**Request headers**

| Header | Purpose |
| --- | --- |
| `X-Dry-Run: true` | **Do not persist.** No `submission`, **no `submission_entry`** and no `submission_line` row is written — the three tables of §2.3 are untouched, not two of them. No token is minted, and the response carries **`"token": null`** — the key is present and null, never omitted, per §6.3's rule for the same choice. **Requires an authenticated staff session** (§8.4), proved by `X-Staff-Proof` below; a request carrying this header without a valid proof is rejected with `UNAUTHORIZED` (401). |
| `X-Staff-Proof: <token>` | **How the API is satisfied that the caller is staff.** A value minted by the admin panel with `db.staff_proof.mint_staff_proof` and verified with `verify_staff_proof`: `itsdangerous.TimestampSigner` over `{"sub": "<username>"}`, signed under the deployment's single `SECRET_KEY` with a **pinned salt**, valid for `PROOF_TTL_SECONDS` (60). Meaningful **only** alongside `X-Dry-Run: true` — on any other request it is ignored and grants nothing. Sent server-to-server by `admin/calc_client.py`; **never set on a browser and never a cookie.** |

> **Why a proof and not the panel's session cookie (v1.18, open item O-9).** Until v1.18 this row required a staff session and named no mechanism, and `api.app:create_app`'s `staff_authenticator` was supplied by nothing but two test files — so every dry run in every real deployment answered `UNAUTHORIZED` while `/admin/try` rendered that refusal inside a 200 page. The cookie was the obvious fix and was rejected: `/admin` and `/api/v1/` are one origin behind nginx, so a session cookie the API accepted would also be sent there by the browser, making the API a second place a staff session is *established* and putting the arbitrary-`bundle` path one `fetch` away from any staff member's tab. A proof cannot be minted without `SECRET_KEY`, which no browser holds. The salt is pinned to a value distinct from the one `SessionMiddleware` signs with, so **a stolen session cookie cannot be replayed as a proof, and a proof cannot be replayed as a session cookie**, though both are signed under the one shared secret. The API performs no `staff` lookup and holds no `staff` model; the cost of that is a 60-second window in which a just-deactivated account's proof is still accepted, for a calculation that persists nothing. `docs/architecture.md` §10 carries the full ruling and the three rejected alternatives.

> `submission_entry` is named explicitly because it was added after this row was written and an implementer working from the older wording writes orphan entry rows on every staff dry run. Staff run dozens of calculations while tuning one formula, and `submission_entry` is what §5.4 aggregates `by_sector` and `by_food_category` over — so those orphans would land squarely in the public statistics this header exists to protect, while `total_calculations` stayed flat and hid it.

## 6.2.1 The `dry_run` Object

`X-Dry-Run` and `dry_run` are orthogonal: the header decides **whether the result is persisted**, the object decides **which factors are used**. All four combinations are legal.

| `X-Dry-Run` | `dry_run` | Scenario | Persisted |
| --- | --- | --- | --- |
| absent | null | Public calculation against the published set | **Yes** |
| `true` | null | Staff verifying live behaviour | No |
| `true` | `{ "factor_set_version": "…" }` | Comparing a persisted draft against published before publishing | No |
| `true` | `{ "bundle": {…} }` | Trialling changes that have not been saved anywhere | No |

```json
"dry_run": {
  "factor_set_version": "2026-Q3-draft",
  "bundle": null
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `factor_set_version` | string \| null | `version_label` of a persisted set — draft, published or archived |
| `bundle` | object \| null | A **complete** factor set snapshot in the §10.2 `bundle.json` shape |

The two are mutually exclusive. When both are null the published set is used — the request is still not persisted.

> **Why a complete bundle rather than a diff against a base version.** A merge routine is new, untested code sitting between the staff member and the engine: when a dry run produces a wrong number, there is no way to tell whether the formula was wrong or the merge was. Diffs also have unpleasant edge cases — how does a generic `food_category: null` row merge with a specific one, and how is "delete this row" expressed? A complete snapshot has none of these questions. The volume does not justify the risk: roughly 270 upstream rows, 600 downstream rows and 20 others, around 90 KB uncompressed, and it travels behind authentication.

> **The bundle carries the taxonomy, not just the factors** (§10.2). Two reasons: §4.1's `has_destination()`, `has_sector()`, `has_food_category()` and `standard_mix_code()` cannot be implemented without it; and the client has stated that food categories, destinations and groupings will change over time, so staff must be able to trial a new destination before committing it — which is impossible if the bundle cannot carry its definition.

> This does **not** change how taxonomy is normally edited. Routine create/update/delete still goes through the admin CRUD of §8.1 and writes to the database. An inline bundle is a parallel, temporary channel that exists only for the lifetime of one request.

Because every dry-run request carries its own data, concurrent staff dry runs are isolated by construction. The path is stateless: no locks, no scratch tables, no TTL sweeper, and no orphan rows when someone closes the browser mid-edit.

**200 response**

```json
{
  "factor_set": { "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true },
  "factor_source": "published",
  "gwp_horizon": 100,
  "token": "3f2b…",
  "totals": {
    "total_kg": "2300.000",
    "current": {
      "metrics": {
        "co2e": {
          "unit": "kg CO2e",
          "display_precision": 1,
          "total": "5118.0000000000"
        }
      },
      "equivalences": [
        { "code": "km_driven", "label": "Equivalent to driving 21,400 km",
          "value": "21400.0000000000", "source_metric": "co2e" }
      ]
    },
    "alternative": { "… same shape as current …" },
    "net_benefit": { "co2e": "2100.0000000000", "water": "0.0000000000" }
  },
  "entries": [
    {
      "sector": "processing",
      "food_category": "dairy",
      "current": {
        "total_kg": "1500.000",
        "metrics": {
          "co2e": {
            "unit": "kg CO2e",
            "display_precision": 1,
            "total": "3468.0000000000",
            "by_destination": [
              { "destination": "landfill", "qty_kg": "1200.000",
                "upstream": "1.9000000000", "downstream": "0.9900000000",
                "value": "3468.0000000000" }
            ]
          }
        },
        "equivalences": [
          { "code": "km_driven", "label": "Equivalent to driving 14,500 km",
            "value": "14500.0000000000", "source_metric": "co2e" }
        ]
      },
      "alternative": { "… same shape as current …" },
      "net_benefit": { "co2e": "2100.0000000000", "water": "0.0000000000" }
    }
  ]
}
```

**`totals` is what the headline figures are rendered from; `entries` is what the breakdown table is rendered from.** Both are computed by the engine. The client adds nothing together — it has no correct way to, because a decimal transmitted as a string (§1.2) cannot be summed in JavaScript without going through `Number`, and because the golden suite (§10.1) can only cover a number the engine produced.

`totals.current.metrics[code]` carries no `by_destination`: the same destination can appear under several entries with different upstream factors, so a cross-entry destination breakdown would need its own aggregation rule. If the client asks for one later, it belongs here as a new field the engine fills, not as a loop in the browser.

> **Settled: the destination breakdown is rendered per entry, from `entries[]`.** C's results page currently builds a single combined destination tab by looping over entries and adding `by_destination[].value` together in JavaScript — a §7.6 violation, and it is the last one that cannot be removed by reading a different field. The resolution is a rendering change, not a contract change: **one breakdown section per entry**, each read straight from `entries[i].current.metrics[code].by_destination`, labelled with that entry's sector and food category.
>
> This adds no field, requires no engine change and puts nothing on A's critical path. It is also the more truthful presentation: 1,200 kg to landfill from processing and 1,200 kg to landfill from primary production carry different upstream factors and are genuinely different rows, and merging them into one "landfill" bar hides the reason a multi-entry calculation was worth making. If the client later asks for a single combined view, it arrives as an engine-filled field with a stated aggregation rule — not as a loop in the browser, and not by reopening this.
>
> **Two front-end figures are removed rather than relocated, because no field exists to move them to:**
>
> - **The percentage-change figure** ("34.2% reduction", `improvement.js:153`, computed as `difference / |current| × 100`). No version of this contract has ever defined a percentage. It is a derived impact number computed in the browser, so it cannot stay; and it is not worth an engine field, because `net_benefit` already carries the same information in the unit the user entered. Removed. If it is wanted back, it is a metric-shaped request and goes through the engine.
> - **The `landfill_diverted` card** (`improvement.js:147`). It synthesises a metric that has no row in the `metric` table, and it hard-codes the destination code `'landfill'` in JavaScript — the two things "metrics are data, not code" exists to prevent. `docs/ToC_v1.0.md` §2.2 already ruled that if the client wants this figure it becomes a real `metric` row plus a formula. Removed meanwhile.

`entries[]` preserves request order, so a client can pair each result with the row the user typed. `submission_entry.sort_order` (§2.3) is what persists that order.

When `alternative` is not supplied, both `alternative` and `net_benefit` are `null`.

`factor_source` states which factors the engine actually used:

| Value | Meaning |
| --- | --- |
| `"published"` | The published set. Public responses are always this. |
| `"version:<label>"` | The named persisted set |
| `"inline"` | The bundle supplied in the request body |

Without this field a staff member who gets an unexpected number cannot tell whether their data failed to take effect or their formula is wrong. On a dry run `token` is `null` — present as a key, holding `null`, never omitted.

> **The front end must check `factor_set.is_mock`.** When true, a placeholder-data warning banner is mandatory in the results area.

## 6.3 `GET /api/v1/factors`

Factors and formulas are published openly (Decision 7).

| Query parameter | Notes |
| --- | --- |
| `version` | Version label; defaults to the currently published set |
| `format` | `json` (default) or `csv` |

**200 response (json)**

```json
{
  "factor_set": {
    "version_label": "MOCK-v0 — PLACEHOLDER", "is_mock": true,
    "published_at": "2026-08-05T02:00:00Z",
    "notes": "Placeholder data derived from ReFED (United States)"
  },
  "constants": [
    { "code": "GWP_CH4_100", "value": "28.0000000000", "unit": "", "note": "…" }
  ],
  "formulas": [
    { "metric": "co2e", "expression": "qty_kg * (upstream + downstream)", "notes": "" }
  ],
  "upstream": [
    { "sector": "processing", "food_category": "dairy", "destination": null,
      "metric": "co2e", "value_per_kg": "1.9000000000",
      "source_note": "Otago 2025 baseline, table 14",
      "data_quality": "measured" },
    { "sector": "processing", "food_category": "dairy", "destination": "prevention",
      "metric": "co2e", "value_per_kg": "0.0000000000",
      "source_note": "Prevented waste was never produced, so no upstream burden is attributable to it.",
      "data_quality": "definitional" }
  ],
  "downstream": [
    { "destination": "landfill", "sector": null, "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "0.9900000000",
      "source_note": null, "data_quality": "proxy-AU" }
  ],
  "equivalences": [
    { "code": "km_driven", "name": "Kilometres driven", "source_metric": "co2e",
      "value_per_unit": "4.1800000000",
      "label_template": "Equivalent to driving {value} km",
      "source_note": null, "sort_order": 1 }
  ]
}
```

**`source_note` and `data_quality` are part of this response, and both may be `null`.** v1.1 added `source_note` to `factor_upstream`, `factor_downstream` and `equivalence`, and `data_quality` to the two factor tables only — `equivalence` has no `data_quality` column (§2.2) — and this endpoint is the whole reason they exist: v1.1's stated rationale is that a calculator which cannot say which of its numbers are measured and which are borrowed cannot be defended in public, and §6.3 is the only public surface where a number can say so. A factor export that carries the values and drops their provenance publishes exactly the figure that is hardest to defend, with the defence removed. `null` is a legal value — most rows will carry `null` until the client supplies real data — and it must appear as `null`, not as an omitted key, so a consumer can tell "no provenance recorded" from "this endpoint does not report provenance".

> **Emitted since v1.4; the contract said so from v1.2 and the code did not.** `db/repository.py`'s `build_bundle_data` — the one projection this export and §10.2's bundle are both built from — did not select the three columns, so `GET /factors` published every value with its provenance stripped for two revisions after §6.3 required it. The fixture (`tests/fixtures/factors.json`) was written to the contract and its contract test parked on a strict `xfail`, which is what made the gap a tracked failure rather than a silent one; the marker came off with the repository fix.

**`name` and `sort_order` are part of the equivalence rows, and v1.4 added them here rather than removing them from the export.** They were emitted from the beginning and appeared in no version of this section. Three reasons the contract moved rather than the code. §10.2's `bundle.json` **requires** both on every equivalence row, and this response is produced by the same projection — dropping them here means writing a second projection whose only purpose is to hide two harmless fields, and a second projection is a second thing to keep in step. `name` is the short human label (`Kilometres driven`); `label_template` is a whole sentence, so a consumer building a heading, a legend or a CSV column has nothing else to use — D needs it and the alternative is hard-coding it, which §7.3a already rules out for the labels themselves. And `sort_order` is the display order §4.1's `equivalences()` promises; a consumer reading this endpoint directly would otherwise have to invent one.

**`downstream[].sector` is present on every row and may be `null` (v1.31),** meaning the row applies to every sector for that destination. Present-and-null rather than omitted, for the same reason the provenance columns are: a consumer must be able to tell "this row applies to every sector" from "this endpoint does not report the sector". §4.1's four-step lookup order is what a consumer re-deriving a figure from this export has to implement, and it cannot be inferred from the rows alone.

With `format=csv`, one CSV file per table is returned, bundled as a zip archive (`Content-Type: application/zip`). The two provenance columns are columns in the `upstream` and `downstream` CSVs like any other, as is `sector`.

## 6.4 `GET /api/v1/stats`

**200 response**

```json
{
  "generated_at": "2026-09-01T03:00:00Z",
  "total_calculations": 1247,
  "suppression_threshold": 5,
  "by_destination": [
    { "code": "landfill", "label": "Landfill", "count": 1268,
      "share": "0.4064", "total_kg": "884200.000" },
    { "code": "other", "label": "Other (sample too small)", "count": 9,
      "share": "0.0029", "total_kg": "3100.000" }
  ],
  "by_sector": [
    { "code": "processing", "label": "Processing / Manufacturing", "count": 604,
      "share": "0.3775", "total_kg": "521800.000" },
    { "code": "other", "label": "Other (sample too small)", "count": 7,
      "share": "0.0044", "total_kg": "2400.000" }
  ],
  "by_food_category": [
    { "code": "unspecified", "label": "Not broken down by type", "count": 742,
      "share": "0.4638", "total_kg": "612900.000" },
    { "code": "dairy", "label": "Dairy", "count": 231,
      "share": "0.1444", "total_kg": "168300.000" }
  ]
}
```

`total_calculations` counts **submissions**; every bucket `count` counts **entries** (§5.4). **The sample above is written so the difference is visible rather than hidden:** its 1,247 submissions carry 1,600 entries between them, so `by_sector` and `by_food_category` counts sum to 1,600, not to 1,247. `by_destination` sums higher again — 3,120 — because one entry lands in the bucket of every destination it used. (Each array above is abridged to two buckets for length; a real response carries every bucket that survives suppression, and it is against those full totals that the `share` values shown are computed.) `share` is computed within its own breakdown, against that breakdown's own total, and does sum to 1. **None of the three is a breakdown of `total_calculations`**, and one submission can carry up to twenty entries.

`by_food_category` shows the `unspecified` bucket (§5.4): entries whose user did not break their waste down by food type. It is an ordinary bucket — suppressed on the same threshold, counted and shared like any other — and it is expected to be one of the largest. It is not the same thing as `standard_mix`, which is what a user selects deliberately.

> **Two things D must build for, both of which the sample above does not show because a mature data set does not reach them.**
>
> 1. **`other` may be large.** It is subject to the threshold itself (§5.4), so when it would fall below it, it absorbs the smallest visible buckets until it clears. On a young data set `other` can be the biggest row in the breakdown. A legend that renders it as a footnote, or a chart that gives it a de-emphasised colour on the assumption that it is always small, will look wrong on the day the calculator opens — which is the day it is most likely to be screenshotted.
> 2. **A breakdown may be an empty array**, while `total_calculations` is non-zero. That is the case where even everything merged together stays below the threshold. It is not an error and not a loading state: render "not enough data yet to show this breakdown" and keep the page. All three breakdowns are independent, so one can be empty while another is full.

> **Copy constraint (owner: D).** The subject of the statistics page must be the calculator itself — "Across the 1,247 calculations run in this tool…" — and **never** "Distribution of food waste destinations in New Zealand". Prefer `share`; if `total_kg` is displayed it must be explicitly labelled as the cumulative total entered into this tool. Do not label a bucket `count` as a number of calculations — it is a number of supply-chain points entered, and the two figures on this page differ by design.

## 6.5 Rate Limits

| Endpoint | Limit |
| --- | --- |
| `POST /api/v1/calculate` | 120 / hour / IP |
| `GET /api/v1/*` | 600 / hour / IP |

Exceeding a limit returns `429` with `RATE_LIMITED`. Counters live in memory or Redis.

**No IP address is persisted by the rate limiter, and none is persisted anywhere in this system except the one exception §2.3 records.** That exception is `ip_block`, which stores an HMAC of an address — never the address — and only for a caller a staff member or the automatic protection has blocked. This sentence used to read "IP addresses are never persisted" without qualification; v0.12 added the blocklist exception in §2.3 and this line was not updated, so the document asserted an absolute and its exception in two places at once. The absolute is the one that was wrong: a reader implementing §6.5 literally would have had grounds to call §2.3's table a contract violation.

> **Resolved in v1.3 — an in-memory counter may not be keyed on a raw address either.** This was recorded as open and B's: §6.5 sets its bar at persistence, and `api/rate_limit.py` keyed on `"post-calculate:203.0.113.9"`, which cleared that bar, while `admin/protection.py` keyed on the §2.3 fingerprint because §2.3's prohibition has been read as covering process memory too. **Both layers now key on the fingerprint**, and both count with the same `db.detection.RequestRate`. The deciding argument was that the counter's dict outlives the request that filled it, so an address in a key is an address this system holds — and two layers applying different privacy standards to the same data was never defensible for a calculator whose stated selling point is that it stores nothing about the visitor. As the note said, both layers moved together.

**How the counters actually behave, since "in memory or Redis" above understates it.** Both are sliding windows, not fixed ones: a fixed window resets on a boundary, which lets a caller spend a full hour's allowance in its last second and another in the first second of the next — 240 calculations inside two seconds against a limit of 120 per hour. The API's limiter (`api/rate_limit.py`) does **not** count a refused request, so a caller who overshoots recovers one window after their last *allowed* request rather than never, and `Retry-After` is the wait until their oldest counted request leaves the window. `admin/protection.py` does count refusals, deliberately — see §8.3's `_RATE_EXEMPT_PATHS`, which exists to survive what that implies. Both counters are per-process: **running more than one worker multiplies the effective limit by the worker count.**

> **Two deployment facts silently disable this section, and neither has an in-process fix.** Both are warned about — the first at start-up, the second the first time it happens — and both are named in `docs/architecture.md` §9.1.1.
>
> 1. **Behind a reverse proxy with `PROTECTION_TRUSTED_PROXY` false, "per IP" above is a fiction.** That is the shipped arrangement (TLS terminates upstream) and `false` is the correct default — with no proxy that overwrites `X-Forwarded-For`, trusting it lets any caller claim any address, which is the worse failure. But every caller then arrives as the proxy's own address, so 600/hour becomes 600/hour for the whole internet, and one `ip_block` row denies every visitor at once. The panel survives this only because §8.3's `_RATE_EXEMPT_PATHS` keeps its login handshake reachable; **a public API has no login handshake to exempt, so there is no equivalent mitigation.** Set the flag true once the proxy is confirmed to overwrite the header itself.
> 2. **A deployment that gives the process no client address disables both this section and §2.3's blocklist.** `uvicorn --uds` behind nginx does exactly that (`scope["client"] is None`). There is then no address to fingerprint and none to count under, so both checks are skipped — the correct answer per request, since inventing a stand-in key collapses every such caller into one shared bucket and one shared blocklist entry, and a silent no-op in aggregate. Bind a TCP socket, or supply the address in `X-Forwarded-For` and set `PROTECTION_TRUSTED_PROXY=true`.

---

# 7. Front-End Modules (owners: C and D)

ES modules, no build step. Located in `web/js/`. `web/README.md` is the operational companion to this section — how to run the front end, and how to run it against the fixtures with no backend — and this document is the authority where the two disagree.

**Eleven modules, nine of them C's and built.** Until v1.2 this section named five and described two of those inaccurately — six real modules were absent, including `view.js`, which holds the escaping and formatting primitives D and E would otherwise each reimplement. The signatures below are transcribed from the branch, not proposed for it. Where C's code and the old contract disagreed on shape, **the contract has changed to match her code** and says so at the point of change; where a contract requirement is genuinely unmet, it is marked **Not built** and stays a requirement.

## 7.1 `api.js` (written by C, shared with D and E)

The only module that calls `fetch` — verified across the branch. **No other module calls `fetch` directly.**

```js
export class ApiError extends Error {
  constructor(code, message, details = [], status = 0);
  code;      // string — §9 error code, or 'NETWORK_ERROR' | 'HTTP_ERROR' | 'MOCK_FIXTURE_ERROR'
             // note: 'HTTP_ERROR' is now BOTH — this module's "response was not JSON"
             // and §9's residual server code. Same handling either way; see §9.
  message;   // string — display-ready
  details;   // Array  — field-level errors; [] when absent
  status;    // number — HTTP status; 0 when the request never completed
}

/** GET /api/v1/taxonomy   @returns {Promise<Taxonomy>} @throws {ApiError} */
export async function getTaxonomy();

/**
 * POST /api/v1/calculate
 * @param {CalculatePayload} payload   §6.2 body, carrying `entries`
 * @param {{dryRun?: boolean}} [opts]  dryRun === true -> X-Dry-Run: true
 * @returns {Promise<CalculationResult>} @throws {ApiError}
 */
export async function calculate(payload, opts = {});

/** GET /api/v1/stats   @returns {Promise<PublicStats>} @throws {ApiError} */
export async function getStats();

/** GET /api/v1/factors[?version=…]   @returns {Promise<Factors>} */
export async function getFactors(opts = {});
```

`api.js` owns URL construction, headers, JSON parsing, and converting any non-2xx response into a thrown `ApiError`, reading `body.error.{code,message,details}` with a fallback to a flat `body.{code,message,details}`. It distinguishes three failure modes — network unreachable, non-JSON response, structured API error — and they carry different messages.

**Mock mode is part of this contract, not a private convenience.** It is the substrate C, D and E all develop on while the backend is unmerged, so its behaviour is written down here rather than left to be rediscovered:

| Parameter | Effect |
| --- | --- |
| `?mock=1` | Every call is served from `tests/fixtures/` instead of the network. Read once at module load |
| `&mockError=<NAME>` | `POST /calculate` throws from `tests/fixtures/errors/<name>.json` (lower-cased); status 429 for `RATE_LIMITED`, else 400 |

Mapping: `/taxonomy` → `taxonomy.json`, `/factors*` → `factors.json`, `/stats` → `stats.json`, `POST /calculate` → `calculate_response.json`.

> **Current behaviour, not a requirement — do not reimplement this.** The mock path was rewritten for the `entries` / `totals` shape on 2026-08-09 and now reproduces §6.2 end to end: one request carrying `entries[]`, one response carrying `totals` beside a per-entry result in request order. It still **derives** two things in JavaScript rather than serving them verbatim — the `mass` metric, which is an identity (`value === qty_kg`, §4.3) and not a formula, and the `totals` roll-up, including the rule that an entry with no `alternative` contributes its current figures to the alternative side. Neither can come from a static file, because both are functions of a request whose entry count the fixture cannot know. Every other figure is the fixture's own. **This is `mockRequest`'s licence and nothing else's** — no module outside it may derive an impact figure (§7.6.1), and the numbers on screen in a mock demo are still partly browser-computed, which is the one property mock mode should not share with a bug.

**Mock mode constrains the document root, and this is not fixable in JavaScript.** The fixture URL is resolved against this module's own URL (`new URL('../../tests/fixtures/', import.meta.url)`), so it follows the page wherever the site is served from — that much was a real defect and is fixed. What remains is structural: a browser clamps `../` at the origin root, so the root **must be an ancestor of both `web/` and `tests/`**. `python3 -m http.server` at the repository root satisfies it; FastAPI serving `web/` as the static root does not, and every mock call 404s. C, D and E all develop in mock mode, so **B owns a dev-only static mount that exposes `tests/fixtures/`**; until it exists, mock mode runs only under the plain HTTP server.

## 7.2 `state.js` (written by C)

```js
/** Single mutable state object with a subscriber set. */
export const state;

/** Object.assign of the patch, then notify every subscriber. */
export function setState(patch);

/** @param {(s: typeof state) => void} fn @returns {() => void} unsubscribe */
export function subscribe(fn);

/** Clears the sessionStorage token and returns to the intro step. */
export function resetCalculator();

/**
 * Pairs the entries the user typed with the per-entry results §6.2 returns, which
 * preserve request order. Each paired `response` is one entry's `current` /
 * `alternative` / `net_benefit` plus the submission-level `factor_set`,
 * `factor_source` and `gwp_horizon`, which the rendering modules read
 * `is_mock` and `version_label` from.
 *
 * `state.result` carries both this and the whole response, so a consumer reads
 * cross-entry figures from `result.totals` (§7.6.1) and per-entry figures from
 * `result.entry_results` — never a sum over the latter.
 *
 * @param {Array<object>} entries   Draft entries, in the order they were sent
 * @param {object} response         The §6.2 response
 * @returns {Array<{entry: object, response: object}>}
 */
export function entryResultsFrom(entries, response);
```

Keys, grouped. **This is C's shape and the contract has adopted it**; the previous ten-key object in this section was a proposal that her code superseded.

| Group | Keys |
| --- | --- |
| Server data | `taxonomy`, `result` |
| Session | `token` — initialised from `sessionStorage.kaiCalculatorToken` at module load |
| Draft entry | `sector`, `foodCategory`, `gwpHorizon`, `measureMode` (`'mass'` \| `'container'`), `totalAmount` (raw string), `totalUnit` (`'kilograms'` \| `'tonnes'`), `unitPreset` (a `unit_preset` code, or null), `unitCount` (raw string), `current: [{id, destination, qtyInput}]` |
| Multi-entry | `entries: []` — committed entries, same shape as the draft |
| UI | `step` (−1 intro … 5 results), `expandedSectors`, `resultBreakdownTab` (`'stage'` \| `'destination'` \| `'food'`), `lastChangedDestination` |
| Status | `loading`, `error`, `errorCode`, `fieldErrors: {fieldPath: message}`, `rateLimitedUntil` (epoch ms) |
| Improvement | `improvementOpen`, `improvedAllocations: {destinationCode: percentString}`, `improvementResult`, `improvementLoading`, `improvementError` |

> **Two of her decisions are better than what this section used to require, and are now the requirement.** A line is `{id, destination, qtyInput}`, not `{destination, qtyKg, …}`: the `id` is a stable identity that survives a full re-render, which matters because `render()` replaces `main.innerHTML` wholesale; and `qtyInput` holds the **raw string the user typed**, so no rounding happens until the value is converted for the API. The old `qtyKg` shape rounds on every keystroke, which is precisely the premature-decimal hazard §1.2 exists to avoid.

> **The table above is exhaustive as of 2026-08-09.** `alternative: []` and `compareAlternative: false` were also on the object — initialised, reset by `resetCalculator`, assigned `[]` by two functions in `calculator.js`, and **read by nothing.** The alternative scenario is built from `improvedAllocations` by `improvement.js`, which never looks at either. Both are removed. A key that is initialised and reset but never populated reads as a feature under construction, and the next person to need an alternative scenario would have wired theirs into a dead one.

**Still requirements, and still unmet:**

- ~~**`unitPreset` and `unitCount` are absent from the line shape because the container-preset input was never built.**~~ **Built at v1.33.** `measureMode`, `unitPreset` and `unitCount` are on the draft entry above, and they sit on the **entry**, not on the line: a container estimates the total that step 4 then allocates, and a destination row reading "0.37 wheelie bins" is neither enterable nor checkable against §6.2's mass-conservation rule.

  **The decision this section said would be required, made.** The two-decimal rule follows the **count**, which is what somebody types; `toKg` still returns three decimal places because §6.2 refuses a fourth, and `"139.200"` is not a number anybody enters. So `totalAmount`'s `/^\d+(\.\d{1,2})?$/` is applied to `unitCount` in container mode and to nothing derived. The collision this section predicted was real and is now moot: the seeded `kg_per_unit` values are **no longer integral** (v1.33 item 11), so a count of 1 on the 23 L bin is `6.670` kg and a total of three decimals is the normal case rather than the exotic one.

  **`measureMode: 'container'` implies `totalUnit: 'kilograms'`, and `calculator.js` maintains it.** `totalUnit` is the unit step 4 allocates in. A visitor who chooses tonnes and *then* chooses a wheelie bin would otherwise reach a step 4 whose rows say "tonnes" against a total in kilograms — a thousandfold error on a screen that looks entirely normal, refused by nothing, because both numbers are individually plausible. `tests/web/test_container_input_browser.py` walks exactly that path; no other test does, because the default is already kilograms.

  **Neither key is on the line shape, and the amount field changes its id with the mode** — `#total-waste` for a mass, `#unit-count` for a count. Two ids rather than one because they are different quantities and `state` holds them apart: switching from 1,200 kilograms to wheelie bins must not carry 1,200 over as a bin count.
- **`gwpHorizon` is set to 100 at initialisation and no control ever writes it.** §6.2 makes the horizon user-selectable between 20 and 100; a stated requirement is currently unmet and invisible on screen.

> **Documented exception to "no ad-hoc DOM manipulation".** Two modules deliberately bypass `setState` and mutate the DOM directly on keystroke (`calculator.js`, `improvement.js`). The reason is sound — `render()` replaces `main.innerHTML`, so a `setState` per keystroke destroys the focused input — but the exception must be documented rather than merely present, because the two fast paths **apply different validity rules to the same field**. As of 2026-08-09 the divergence is narrower than it was and is not zero: on a negative amount `updateLine` marks only the row being typed in, while `destinationRows` marks every negative row on the screen; and `destinationRows` additionally marks a row named by `state.fieldErrors`, which `updateLine` clears on the first keystroke because blanking or filling a row changes which lines the request would carry, so the server's line positions stop meaning what they meant. Any change to a validation rule has to be made in both.

## 7.3 `units.js` (written by C)

**All front-end mass arithmetic belongs in this module, and as of 2026-08-09 all of it is here.** That is the whole point of §7.6 rule 1: the front end's arithmetic can be audited in one file. It was not — `tonnes ? 1000 : 1` and `.toFixed(3)` were spelled out at six sites across `results.js`, `improvement.js` and `calculator.js` while `calculator.js` also called this module for the same conversion, so the front end held two copies of its only arithmetic rule and either could be changed without the other. The last of them moved here in the same revision that added `kgToTonnes`. **A `*`, `/` or `.toFixed()` on a mass anywhere else in `web/` is now a defect on sight.**

```js
/**
 * Convert a container count to kilograms.
 *
 * **The multiplication is decimal, not double (v1.33).** Both operands are decimals:
 * the count is what the visitor typed and `kg_per_unit` crosses the wire as a string
 * (§1.2) precisely so that `Number` never sees it. The previous body was
 * `Number(count) * Number(preset.kg_per_unit)` then `.toFixed(3)`, and that is wrong
 * in the **third** decimal place — a quarter of the seeded 23 L food scraps bin is
 * `0.25 × 6.6700 = 1.6675 kg` exactly, the nearest double to `6.67` is below it, and
 * `toFixed` reads the exact tie as under the half and answers `"1.667"`. Integer
 * arithmetic, rounded half up, which is what `Decimal.quantize(ROUND_HALF_UP)` does
 * on the Python side. (Python's own `round()` is banker's rounding and is not it.)
 *
 * **Neither operand may be negative.** `unit_preset` carries a `kg_per_unit >= 0`
 * CHECK (§2.1) written for this multiplication; the count is the operand no database
 * constraint can reach, and an `<input type="number">` hands over `"-2"` quite
 * happily.
 *
 * @param {number|string} count  Number of containers, as the visitor typed it
 * @param {string} presetCode    unit_preset code
 * @param {Array}  presets       taxonomy.unit_presets
 * @returns {string}             Kilograms as a string with 3 decimal places,
 *                               ready to send to the API (§6.2 refuses a fourth)
 * @throws {Error}               presetCode does not exist, or either operand is
 *                               not a plain non-negative decimal literal
 */
export function toKg(count, presetCode, presets);

/**
 * `toKg` made total: the kilograms a container entry describes, or `''`.
 *
 * Called inside a render by both consumers, so it may not throw — and `toKg` throwing
 * on an unknown preset code is reachable, because §6.1 says a consumer must not assume
 * the taxonomy is stable across a publish and the page holds a selection made before
 * one. Whether the count is *typeable* is a separate question asked in
 * `calculator.js`, beside the same two-decimal rule the mass field takes.
 *
 * @param {{unitPreset: string|null, unitCount: string}} entry
 * @param {Array} presets  taxonomy.unit_presets
 * @returns {string}  Kilograms at 3 decimal places, or '' when there is no total yet
 */
export function containerKg(entry, presets);

/**
 * The one place the two step-3 measurement modes reconcile: an entry's total as
 * `{amount, unit}`, in the unit its destination rows are entered in.
 *
 * A container entry **always** answers kilograms — see §7.2. In `units.js` rather than
 * in `calculator.js` because it has two consumers: `results.js` prints each entry's
 * waste amount in the downloaded report and cannot import `calculator.js` (that module
 * imports this one). It read `entry.totalAmount` directly, which is `''` for a
 * container entry, so the report said "0.00 kilograms" for an entry whose screen said
 * 139.200 kg.
 *
 * @param {object} entry   A draft or saved entry
 * @param {Array} presets  taxonomy.unit_presets
 * @returns {{amount: string, unit: 'kilograms'|'tonnes'}}
 */
export function entryTotal(entry, presets);

/**
 * @param {number|string} amount
 * @param {'kilograms'|'tonnes'} unit
 * @returns {number|null}  null when amount is not finite
 * Imported by calculator.js (the review step's kg figure) and improvement.js
 * (lineKg, which is every allocation percentage's denominator).
 */
export function massToKg(amount, unit);

/** massToKg(...) fixed to 3 decimal places, i.e. API-ready.
 *  @returns {string|null}  null when massToKg returns null.
 *  Imported by calculator.js (both line-normalising helpers) and
 *  improvement.js (currentLines). Note that kgString('', unit) is "0.000":
 *  a row the user has not filled is not a row holding zero, so the two
 *  callers that render blank rows keep their own '' check. */
export function kgString(amount, unit);

/**
 * Kilograms to tonnes, for display. The only arithmetic §7.6.1 permits on a
 * figure the API supplied, and therefore the only one of these functions whose
 * input is an API decimal string rather than something the user typed.
 * @param {number|string} kilograms
 * @returns {number}  NaN when the input is not finite, so an absent figure
 *                    reaches formatNumber() as absent rather than as zero
 */
export function kgToTonnes(kilograms);
```

> `kgToTonnes` was added on 2026-08-09 for `results.js`, which printed `totals.total_kg / 1000` inline at two sites — the summary card's "2.300 tonnes" note and the same line in the downloaded report. §7.6.1's exception is stated in terms of *this module*, and neither site was in it. It is a one-line function and it exists so the rule reads the same everywhere: **outside `units.js`, nothing divides, multiplies or adds a number the API supplied.** Bar and chart widths scaled against a local maximum are not figures and are not covered by this.

## 7.3a Calculator Modules (written by C)

Six modules that no version of §7 named. Transcribed from the branch.

### `view.js` — the shared primitives

**D and E consume this module rather than reimplementing it.** One `escapeHtml`, applied at every interpolation site, is the single reason C's branch is XSS-clean; a second copy in D's or E's code is a second thing to get right.

```js
/** &, <, >, " and ' -> entities. Safe for text and double-quoted attributes. */
export function escapeHtml(value = '');

/** An API figure printed at the metric's own precision. toLocaleString('en-NZ')
 *  with `precision` as BOTH minimumFractionDigits and maximumFractionDigits, so
 *  825.00 at display_precision 2 prints "825.00" rather than "825" one row above
 *  "1,204.50" in the same column of money.
 *
 *  `precision` arrives from the database (§2.1 metric.display_precision), not
 *  from this file, so it is clamped to Intl's legal 0-20: an out-of-range value
 *  makes toLocaleString throw a RangeError, which would take out the whole
 *  render rather than one figure.
 *
 *  Returns 'Not available' for a non-finite input — see the note below. */
export function formatNumber(value, precision = 2);

/** lower-case, non-alphanumerics -> '-', trimmed. For DOM ids and class names. */
export function slug(value);

/** The six screens the wizard names, in order. Lives here rather than in
 *  calculator.js because results.js needs the same vocabulary and
 *  calculator.js already imports results.js. */
export const STEPS;

/** The step navigation bar: a step's Back and primary actions, and its
 *  position in the flow, as one `position: sticky; bottom: 0` element.
 *  Emits data-action="go-step" data-step="<back>" and data-action="<action>",
 *  plus "Step N of 6" and the step's name.
 *
 *  Replaced `buttonRow(backStep, label, disabled, action)` at v1.23. */
export function stepNav({step, back, backLabel = 'Back', label = 'Continue',
                         disabled = false, action = 'continue'});
```

> **Two structural preconditions on `stepNav`, both silent when broken.** It must be the **last child of the step's `<section>`**: `position: sticky` with a `bottom` inset pins only for as long as its *containing block* extends below the fold, so anything rendered after it unpins the bar early — which is why `results.js` renders the improvement panel above it and not below. And it must come **after the content in the DOM**, so Tab reaches the form before the navigation. Neither is enforceable from inside the function; `tests/web/test_step_navigation.py` measures both.

> **Precondition, stated because D and E will now depend on it:** `escapeHtml` does not escape backticks or `/`, so it is safe only in **double-quoted** attribute contexts and in text. Every attribute in C's branch is double-quoted. An unquoted attribute breaks the guarantee silently.
>
> **Precondition on `formatNumber`, and the reason the calling modules coerce their own inputs:** `Number(null)`, `Number('')` and `Number(undefined)` are `0`, `0` and `NaN`, so a plain `Number(value) || 0` makes "the engine did not return this metric", "this value is malformed" and "this value is zero" the same figure on screen. Every caller in `results.js` and `improvement.js` therefore maps absent to `NaN` before calling in, and `formatNumber` renders that as "Not available". **An absent figure has to read as absent** — a `|| 0` on an API figure is the defect this guards.
>
> The v1.2 "known gap" — `formatNumber` setting no `minimumFractionDigits` — **is closed**; the JSDoc above is the current behaviour.

### `calculator.js` — the wizard

```js
/** Writes the current screen into `main`. Handles the loading and
 *  taxonomy-failure screens; dispatches on state.step (−1 intro, 0-4 screens,
 *  5 delegates to results.renderResults). */
export function render(main);

/** Updates the header and the "Clear all data" button. It no longer writes a
 *  progress indicator: at v1.23 the six-step band above <main> was folded into
 *  view.js's stepNav, and index.html no longer carries #step-indicator. */
export function renderChrome();

/** Installs four delegated listeners on `main` (click / change / input /
 *  keydown) and stores the taxonomy-reload callback the UNKNOWN_CODE path uses. */
export function bindCalculator(main, retryTaxonomy);
```

`data-action` vocabulary handled by the click delegate: `start`, `go-step`, `toggle-sector`, `clear-food`, `continue`, `add-entry`, `edit-entry`, `remove-entry`, `calculate`, `start-over`, `download-results`, `breakdown-tab`, `explore-improvements`, `reset-improvement`, `cancel-improvement`, `compare-improvement`, `retry`, `view-methodology`.

Module-private and worth knowing: `validateCurrentStep()` returns a display string or `''`; `buildLines(entry)` produces `[{destination, qty_kg}]` filtered to `qty_kg > 0`; `draftFieldPaths()` produces the §9 `field` path for each row of the draft entry, aligned with `state.current` and `null` for a row the request will not carry; `publicError(error)` maps a §9 code to user copy; `validationMessage(error)` and `describeDetail(detail)` build the 400 banner from the details that no row on screen can display; `fieldErrorMap(error)` turns `details[]` into `{fieldPath: message}`; `blocked()` and `clearedError()` implement §9.2's rule that `BLOCKED` is terminal; `submitCalculation()` issues the request.

> **Every `is_prevention` destination is excluded from the destination entry step and included in the improvement panel.** That modelling is correct and must survive any refactor — a prevention destination is how the alternative scenario expresses waste avoided (§2.1), and offering one as a current-scenario destination would let a user claim to be already preventing what they are about to describe wasting. §6.2 answers 400 for it, so a form that offered it would be offering a refusal.
>
> **Read `destination.is_prevention` from §6.1, never the code.** `calculator.js`'s `entryDestinations` filtered `code !== 'prevention'` until v1.22 and therefore left every *other* prevention destination — §10.3's ReFED set brings its own, and the deployed stack offers it — on the current-waste list. The function is exported so `tests/web/test_entry_destinations.py` can run it under Node against a taxonomy it builds, the same seam `buildResultsReport` was pulled out for.
>
> **The three silent failures this module used to have are fixed, and the shape of them is worth keeping.** `fieldErrorMap` keyed on the raw `details[].field` string while the render loop looked up `current[<index>].qty_kg` — §9's format is `entries[0].current[1].qty_kg`, so it never bound; the index was the position in `state.current`, which includes blank rows, while `buildLines` filters them out before sending, so the request index and the render index differed whenever any destination was left empty, which is the normal case; and `fieldErrorMap` stored the **envelope's** `message` against every field, so even a correctly bound row would have read "Request validation failed" while the server's own per-field prose was discarded. All three are silent by construction: no error, no console warning, only the generic banner. `draftFieldPaths()` exists to make the first two impossible to reintroduce independently — it derives the path from the same filter `buildLines` applies and roots it at `entries[state.entries.length]`, because the draft entry travels last.
>
> **`api/errors.py::bracket_path` is the server half of that agreement** and `tests/api/test_api_entries.py` asserts the exact string, so both ends of the `field` format are pinned by a test in one tree.

### `results.js` — the results screen

```js
/** The step-5 screen: placeholder banner (conditional on the submission-level
 *  factor_set.is_mock, §7.6.2), impact summary cards read from totals.current,
 *  tangible equivalents printed as the engine worded them, a three-tab
 *  breakdown (stage / destination / food) built per entry from entries[], a
 *  methodology-and-limitations block naming the factor version, action buttons,
 *  and the improvement panel.
 *  @returns {string} HTML */
export function renderResults(state);

/** The plain-text report, returned rather than downloaded.
 *
 *  Carries, in this order: the total mass in kg and tonnes; an impact summary
 *  holding every metric in totals.current.metrics except `mass`, each at its
 *  own display_precision and labelled with the unit that travelled with the
 *  figure; the tangible equivalents as `label` — the sentence the engine
 *  interpolated — copied verbatim; the improvement comparison read from
 *  totals.net_benefit, when one was run; then per entry, the inputs the user
 *  typed, that entry's own metric totals and each destination's
 *  by_destination[].value; then the factor version, the placeholder notice
 *  when and only when factor_set.is_mock (§7.6.2), and the percentage-waste
 *  limitation. No figure is summed, differenced or re-scaled here (§7.6.1).
 *  @returns {string} */
export function buildResultsReport(state);

/** Wraps buildResultsReport in a Blob and triggers the download as
 *  'food-waste-impact-results.txt'. */
export function downloadResults(state);
```

> **The export used to contain no results.** It printed the total mass, the entries, their destinations and quantities, the factor version and the placeholder warning, and not one output number — under a file name that says "results". `buildResultsReport` exists as a separate export because that is the half a test can assert on: `tests/web/test_results_export.py` runs this module under Node against the §10 fixtures and matches whole anchored lines, so a report that printed the label without the figure, or the figure without its unit, fails. A test that greps this file for a heading would have passed on the broken version.

> **The client-side aggregation layer is gone.** This module summed engine-computed metric totals, equivalence values and destination rows across entries; the two largest numbers on the page were numbers the engine never produced. It now reads `totals` and `net_benefit` from §6.2, and the destination tab is rendered per entry per the ruling there. `aggregateResults` and `differenceData` no longer exist.
>
> The two hard-codings went with it: the breakdown columns are collected from the response's own key order (which §4.1 already sorts by `sort_order`), and the equivalence list prints `label` — the sentence the engine interpolated from `label_template` — rather than three English labels of its own for three hard-coded codes.
>
> **`mass` is named in this module, and that is not a §7.6.5 violation.** Rule 5 exists because a view listing `['co2e','water','cost']` *omits* the metric a staff member inserted; every metric the response carries still appears here. `mass` is held out of the impact cards and the breakdown columns because §3 hoists it — its formula is `qty_kg` (§4.3), so `scenario.total_kg` and `by_destination[].qty_kg` are the same figure, and it is already on screen as the primary card and the "Waste amount" column. `improvement.js` holds it out for a different reason, stated there. Both are single-code exclusions with a stated cause, not lists.
>
> **Bar widths are not figures.** A bar is scaled against the widest bar on the tab, across every section so two per-entry sections stay comparable, and no width is printed. §6.2 defines no share for an entry or a destination, so the percentage-of-total that used to sit beside each bar was a number the engine never produced.

### `improvement.js` — the alternative scenario

```js
export function currentAllocationPercentages(state);  // {destinationCode: number}
export function openImprovement(state);               // seeds from current allocation
export function resetImprovement(state);
/** Keystroke fast path: mirrors slider and number input, updates the running
 *  total and inline error, enables/disables Compare — all without setState. */
export function updateImprovementInput(control, state);
export function allocationTotal(allocations);
export function improvementValidation(state);         // '' when valid
/** @param {object} state
 *  @param {(e: Error & {code?: string}) => string} [toPublicMessage]
 *  calculator.js's publicError — §9's code-to-copy map — PASSED IN rather than
 *  imported, because calculator.js already imports this module and the import
 *  back would be a cycle. Without it this panel showed raw backend prose for
 *  the codes the main flow words carefully, and §9.1 rules that a public
 *  FORMULA_ERROR never echoes the expression or its location. */
export async function compareImprovement(state, toPublicMessage);
export function ImprovementScenario(state);           // collapsed CTA or open panel
export function ComparisonResults(state);             // '' until a comparison exists
```

> **Charts must render negative values, and these do.** `downstream` may be negative (§2.2) and a metric total therefore may be too. `Math.abs()` stood on both comparison-bar widths, so a −500 kg CO2e offset drew a bar identical to +500 and the reuse-and-offset story — the client's headline message — was invisible. A group containing a negative value now draws against a **centred zero line**: each bar takes at most half the track and grows right from the centre when positive, left when negative, and a group with no negative value keeps the full-width left-anchored bar so the common case is unchanged.
>
> **The arrow shows the direction of the impact, not the sign of the number.** This was ambiguous — an up arrow beside "1,104.0 kg CO2e saved" reads as "better" to one person and "went up" to another — and is now ruled. `net_benefit` is `current − alternative` (§3), so a positive net benefit is a saving, the impact fell, and the arrow points **down**. The two meanings that were sharing one class are now two sets:
>
> | Class | Applied by | Meaning | Mark |
> | --- | --- | --- | --- |
> | `.change-down` | `signClass()`, on `net_benefit > 0` | the impact fell — a saving | ↓, Kale |
> | `.change-up` | `signClass()`, on `net_benefit < 0` | the impact rose | ↑, Beetroot |
> | `.change-none` | `signClass()`, on \|`net_benefit`\| < 1e-9 | no change | —, muted |
> | `.value-negative` | `results.js` and `scenarioValue()` | this **quantity** is below zero | no arrow, Beetroot |
>
> **`.value-negative` carries no arrow deliberately.** `formatNumber` already prints the minus sign, and a quantity is not a movement. D's charts (§7.4) should use the same four classes rather than a second set. Nothing marks an ordinary positive quantity: a green mark against every figure on the page is decoration, not a signal.
>
> **`mass` is held out of the comparison lists** because §6.2 requires an entry's two scenarios to describe the same mass, so its `net_benefit` is zero by construction — "Mass: No change" on every comparison, in a list whose subject is what changed. Same exclusion as `results.js`, different reason.
>
> **The mass check is §6.2's own rule, applied in kilograms.** `improvementValidation` compared allocation percentages to within ±0.01 **percentage points**, which is a different rule at every tonnage: 0.01 points is 0.15 kg on a 1,500 kg entry, fifteen times §6.2's absolute 0.010 kg limit, so the panel enabled Compare on a submission the server then refused with a 400 — for the whole submission, after the user had left the screen with the numbers on it. It now sums the lines that will actually be sent. The seeded allocation was itself invalid under the corrected check (52.17 + 34.78 + 13.04 = 99.99%), so `currentAllocationPercentages` gives the rounding remainder to the largest share, and `improvedLines` anchors on the entry's **allocated** current mass rather than the total typed at step 3 — step 4 deliberately permits allocating less than the total, and anchoring on the typed total made every under-allocated entry send an alternative heavier than its current scenario.
>
> The alternative lines are built as `(totalKg × percentage / 100).toFixed(3)` **per line independently**, so Σ parts can differ from the entry total by up to 0.0005 × n. The dual-scenario design depends on the two scenarios conserving mass; this can break it by fractions of a gram. **Settled in v1.2, in C's favour:** §6.2's mass-conservation rule is derived from exactly this behaviour and its 0.010 kg tolerance is 20 lines × 0.0005 kg, so the drift this module produces is accepted rather than rejected — but only because §6.2 also caps a scenario at 20 lines per entry. The worst case sits on the boundary, and the comparison is `<=`. If that cap ever rises, this allocation must round to a running remainder instead.

### `main.js` — entry point for `index.html`

No exports. Wires `subscribe(→ renderChrome + render)`, calls `bindCalculator`, binds the header home and "Clear all data" buttons, defines `loadTaxonomy({preserveError})` (also passed to `bindCalculator` as the `UNKNOWN_CODE` reload path), and performs the first render and taxonomy fetch. It does **not** retry the taxonomy on load; the retry is the user's, through the `retry` action on the failure screen.

> **The focus policy, which is now a requirement rather than an implementation detail.** `render()` replaces `main.innerHTML` wholesale, so every re-render detaches whatever the user had focused. This module called `main.focus()` after *every* `setState`, so arrow-keying the sector radio group fired `change` → full re-render → focus yanked to `<main>`, and a keyboard-only user could not get past step 1.
>
> Moving focus to `<main>` is right on a **step transition** and wrong on every other `setState`. On a **same-step** re-render, focus goes back to the element that had it, looked up by `id` — which is why the food-category radios needed ids. Scoping the focus call alone is not sufficient and was the first attempted fix: the focused radio is detached regardless, so the keyboard user lands on `<body>` instead of `<main>`, which is worse. Any change here has to preserve both halves.

### `methodology.js` — entry point for `methodology.html`

No exports. Uses top-level `await` to call `getFactors()`, then writes the factor-set metadata and the published-formula table into `#factor-content`, prefixed by the placeholder-data banner when `factor_set.is_mock`. Renders an escaped error block on failure. `formula.expression` is staff-authored content reaching a public page and is escaped inside `<code>`.

> **The downstream table carries a `Sector` column (v1.31)**, rendering `All sectors` where `downstream[].sector` is `null`, beside the `All food categories` the food column already renders. It is not optional: with a set that prices by sector, omitting it prints rows that are identical in every visible column and differ only in the number — the figure published without its basis that §2.2's provenance columns exist to prevent. The sentence above the table states §4.1's order as well, because the two columns each show a scope and neither can say which one gives way.

## 7.4 `charts.js` (written by D)

```js
/**
 * @param {HTMLCanvasElement} el
 * @param {Array<{code,label,count,share}>} buckets
 * @param {{title?: string}} [opts]
 * @returns {Chart}  Chart.js instance; the caller is responsible for destroy()
 */
export function renderDonut(el, buckets, opts);

/**
 * @param {HTMLCanvasElement} el
 * @param {Array<{label, value, unit}>} rows
 * @param {{allowNegative?: boolean}} [opts]  Downstream factors may be
 *        negative, so the bar chart must render negative values
 * @returns {Chart}
 */
export function renderBar(el, rows, opts);
```

## 7.5 `news.js` (written by D)

```js
/**
 * Fetches news from the client's WordPress site. No second news system
 * is built.
 * @param {number} [limit=6]
 * @returns {Promise<Array<{title, excerpt, link, date, imageUrl}>|null>}
 *          Returns [] on failure so the home page never blanks out
 *          because the news feed is down, and null when no news origin
 *          is configured at all (§7.8) — the home page removes its news
 *          section for the second and reports an outage only for the first.
 */
export async function fetchNews(limit);
```

Source: `${NEWS_ORIGIN}/wp-json/wp/v2/posts?per_page={limit}&_embed`

**The route is fixed by WordPress; the origin is `NEWS_ORIGIN` from §7.8 and appears in no file in this repository.** It used to be written out here as a literal *and* in `docker/nginx.conf`'s `connect-src`, two copies that had to agree and failed asymmetrically when they did not. Do not restore the literal: `tests/test_d_statistics_content.py` fails on an absolute origin in `api.js`, and on one in the nginx configuration.

> **§7.4 and §7.5 were specifications rather than descriptions, and both have since been built (v1.36).** `web/js/charts.js`, `web/js/news.js` and `web/js/home.js` are in the tree, Chart.js is vendored under `web/vendor/`, and `home.html` links the feed from every public page's navigation. Read this section as the contract they are held to, not as work outstanding.

## 7.6 Front-End Hard Constraints

1. **The front end performs no impact calculation.** Apart from unit conversion in `units.js`, every number comes from the API. This includes cross-entry totals: read `totals` and `net_benefit` from §6.2, never a sum over `entries[]`.
2. **When `is_mock` is true, the warning banner is mandatory** and cannot be dismissed. This covers **every results view and every export**, and it must be conditional on `is_mock` rather than unconditional — an export that always carries the placeholder disclaimer becomes an export that disclaims real data the day real factors are published, which is the more damaging direction of the same bug.
3. The calculator page is **mobile-first**, baseline width 375px. **The baseline is not a floor — check the band between the breakpoints.** A layout can pass at 375px and at desktop and fail in between: `.results-page`'s −80px bleed had its reset at ≤480px and its desktop counterpart at ≥850px and nothing in between, so from 481px to 849px the results page sat 60px off the left edge with the body scrolling sideways. A tablet is the likeliest non-desktop device a demo runs on.
4. After every successful calculation, write the returned `token` back to `sessionStorage`.
5. **Iterate over metrics and equivalences; never hard-code their codes.** A view that lists `['co2e','water','cost']` silently omits the metric a staff member added, and adding a metric is meant to cost one `INSERT` and one formula (§2.1).
6. **Charts must render negative values.** `downstream` may be negative (§2.2), so a metric total may be too. Discarding the sign hides the reuse-and-offset result the calculator exists to show. The sign classes and the arrow convention are in §7.3a under `improvement.js`; use those four classes rather than a second set.
8. **Text that comes from the database may contain a token that cannot be broken, and a flex or grid item will not shrink below it.** A `min-width: auto` item — the default — is never narrower than its min-content width, and for a URL that is its full width. The published ReFED set's `notes` cite a 124-character URL; rendered in the factor-set summary it held a row at 768px inside a 310px list and pushed the methodology page to **875px in a 390px viewport, in English as well as in Arabic**. Give the element holding that field `overflow-wrap: anywhere` — the one value that also shrinks the min-content contribution, so the flex floor drops with it and no `min-width: 0` is needed. **Do not give it to the label beside it:** a label is a catalogue string, the value will take every pixel it is allowed, and `anywhere` on both collapsed the Arabic label `ملاحظات` to a 1px column 760px tall while every overflow assertion still passed. **Assert this by measuring `scrollWidth` against `clientWidth` in a browser**, and assert alongside it that the rendered data still contains a token long enough to reproduce it: publish a factor set with short notes and an overflow test goes green on a page that could never have overflowed.
9. **No page may request an asset from a third-party host at runtime.** Fonts, scripts, stylesheets, icons and images are served from this origin. `styles.css` opened with an `@import` from `fonts.googleapis.com`, so every visitor's browser announced itself to a third party before the first paint — on a calculator whose stated privacy position is §2.3's, and whose statistics page says so in its own copy — and the first paint waited on a network the project does not control. The brand fonts are in `web/assets/fonts/`. **This binds §7.4:** Chart.js is self-hosted, never loaded from a CDN.
10. **No deployment domain is written into the front end, the nginx configuration or an image (v1.36).** DNS, certificates and hosting are out of scope for this project, and a domain that appears in two files which must agree is the defect §7.8 exists to remove — the two fail asymmetrically, so nothing catches the drift. Every origin arrives at container start from one environment variable and reaches both the Content-Security-Policy and `web/js/config.js` from it. **When a resource is refused, the fix is a variable, never a host added to `docker/nginx.conf`** — that is how the pair comes back, and `tests/test_d_statistics_content.py` fails on an absolute origin in either file.

---

## 7.7 Interface Translation

> **This section is the normative one.** Until v1.27 the only i18n text in this document was the change log, and the `§7` / `§7.6` / `§2.1` references in the v1.24–v1.26 rows pointed at sections that said nothing about translation. Those references resolve here.

Owners: C and D on the calculator, E on the panel. Open item O-8.

### 7.7.1 The shape of a catalogue

**The English source string is the key.** `t('Save')` looks up `"Save"`. There is no separate key namespace, a missing key renders its own English source, and one file is one language.

```
admin/locales/<lang>.json    read by Python, shipped as wheel package data
web/locales/<lang>.json      fetched by the browser
web/locales/index.json       the manifest a browser reads before any catalogue
```

Two locations because `[tool.setuptools.package-data]` cannot reach outside its own package and the two Dockerfiles copy disjoint trees — a top-level `i18n/` would be in neither image. What the two share is the contract: one key rule, one fallback rule, one file shape, one notice rule, **one stored choice**.

```json
{ "language": "zh", "endonym": "中文（简体）", "machine_translated": false,
  "dir": "ltr", "tags": ["zh", "zh-Hans", "zh-CN"],
  "strings": { "Save": "保存" } }
```

`endonym`, `machine_translated`, `dir` and `tags` live in the file so that **adding a language is adding a file**. `web/locales/index.json` repeats `language`, `endonym`, `machine_translated` and `tags` for every catalogue, so the chooser can label twenty-one options from the one fetch the front end already makes; a test holds the manifest to the catalogues it summarises.

**Two rules about the markers themselves, both learned the hard way at v1.30:**

**A `data-i18n` element may not contain element children.** `applyToDocument` assigns `element.textContent`, so a child element inside a marked one is deleted the first time the language changes — silently, and in every language except English. Write the sentence in its own `<span data-i18n>` beside the link, never a marker on the paragraph holding both. The keys are extracted with `html.parser`, not a regex: the regex that preceded it matched the *outermost* element whose attributes began `data-i18n` — which `data-i18n-attr` satisfies — and consumed everything to its closing tag, so the four `<a data-i18n>` links inside a marked `<nav>` were invisible to the whole suite.

**A translatable literal must be an argument to `t()`, never to something that calls `t()` for it.** `sentenceAround('Across %(count)s calculations run in this tool.', …)` reads as translated code and is not: the literal is an argument to a local helper, so the key extractor never sees it, no catalogue is required to carry it, and the string renders in English in every language with the whole suite green. That shipped on the statistics page's headline and was found by looking at a screenshot of the Arabic page, not by a test. Pass `t('…')` in and let the helper work on the translated string.

**An entry may be character-identical to its English source only when it is declared.** `Code` in French, `Name` in German, `Sector` in Dutch and `No` in Spanish are the natural words; forcing a synonym to satisfy the test that forbids an untranslated entry would make the interface worse to read. A per-language allowlist carries them, and a second test fails on any allowlisted entry that is **not** in fact identical, so the list can only grow deliberately and cannot outlive its reason.

### 7.7.2 How the language is chosen

**One order, both surfaces.** `admin/i18n.py::resolve` and `web/js/i18n.js::resolve` implement it, and two surfaces that answered one visitor differently would be the defect a shared rule exists to prevent.

| Rank | Source | Persists? |
| --- | --- | --- |
| 1 | `?lang=` — matched on its own, ignored if unrecognised | **No.** One request. |
| 2 | The `kaicalc_lang` cookie, when it names a catalogue | Already stored |
| 3 | `auto`, absent, or unrecognised ⇒ negotiate: the browser's **highest-priority tag only** (v1.26) | No |
| 4 | English | — |

Matching inside one tag is unchanged: RFC 4647 truncation, with a catalogue's own `tags` claim tried first, so `en-NZ` reaches English, `zh-CN` reaches Simplified and `zh-TW` reaches Traditional.

**`?lang=` and the chooser must not be confused for one another.** `?lang=` exists for testing, screenshots and support; it writes nothing, and **no control emits it** — a link pasted into a support thread must not silently re-language the recipient's browser for good. They share no mechanism: the chooser is a `<form method="post">` on the panel and a `<select>` writing a cookie on the calculator, and neither touches the URL.

### 7.7.3 What is stored, and why §2.3 permits it

**One cookie: `kaicalc_lang`, path `/`, `SameSite=Lax`, `Max-Age` one year, not `HttpOnly`, not `Secure`.** Its value is `auto` or a language code with a catalogue; anything else is treated as `auto`.

**A cookie rather than `localStorage`, and the usual reason is wrong.** Both surfaces are the same origin, so `localStorage` would in fact be shared. The decisive reason is that **the panel renders server-side and must know the language before it emits HTML**, which `localStorage` cannot answer. Not `HttpOnly`, because the calculator's JavaScript reads and writes the same cookie. Not `Secure`, because it carries no secret and `Secure` would stop it working on the plain-http localhost the stack is developed and demonstrated on.

> **§2.3 forbids storing an IP address, a user agent or a browser fingerprint. This cookie is none of them, and the reason has two halves that are both required.**
>
> 1. **It records something the visitor deliberately declared**, not something inferred from their browser. Reading `Accept-Language` and forgetting it, and storing "this visitor chose English", are different acts with different justifications. The first observes; the second obeys.
> 2. **Its value space is closed, tiny and free of entropy** — twenty-two values in total, being twenty-one languages and `auto`, shared identically by everyone who picks the same one. A field that cannot distinguish two visitors cannot correlate them, whatever else it records.
>
> **The second half is the load-bearing one.** Property 1 alone would equally justify storing a name somebody typed into a form, which would be a fingerprint by any measure. It is the absence of entropy, not the presence of consent, that makes this incapable of identifying anyone. **Both halves are written here so that the next reader neither deletes this cookie as a §2.3 violation nor adds a genuinely identifying field believing that consent licenses it** — this distinction has already been mishandled once in the other direction, when an implementer declined to read `Accept-Language` at all.

**"Follow the system" is a stored value — the literal `auto` — not the absence of one.** Otherwise "chose to follow" and "never chose" are indistinguishable and the chooser cannot show what is in effect. The mechanical reason matters more: reverting becomes an ordinary write rather than a cookie deletion, and deleting a cookie reliably requires re-sending it with `Max-Age=0` and an exactly matching path and domain — get that wrong and the old value survives, so the chooser appears to revert and snaps back on the next page.

**It is not entangled with the de-duplication token.** `submission.token` (§2.3) is a different name, a different lifetime and a different purpose. The language cookie neither extends nor refreshes it, and **never appears in `submission`, in `audit_log`, or in the access log.** Because path `/` cannot be scoped away when both surfaces need the cookie, the browser also attaches it to `POST /api/v1/calculate`; **the API receives it and ignores it**, and that is asserted by a test rather than left as obvious — the access log was found writing four §2.3-forbidden fields on 2026-08-12.

### 7.7.4 The chooser

**Top inline-start of every page.** "Top left" is physical and two catalogues render right-to-left, so it is implemented as an inline-start position and lands top-right in Arabic and Urdu. It is never above the machine-translation notice, which is a statement about the whole page and stays `document.body.firstElementChild`.

**On the calculator it is the first item in the header's own row, and that is a measured decision rather than an aesthetic one.** It was built as a separate strip between the notice and the header, which is the obvious reading of "top left" and what was originally specified. That strip cost **57px on every page** — a 44px control, its padding and a rule. This calculator had already deleted an 87px step-indicator band to stop short steps scrolling, and `styles.css` carries a "do not put the constant back" warning over the arithmetic that band left behind; `tests/web/test_step_navigation.py::test_a_short_step_is_not_floored_by_a_stale_min_height` failed the moment the strip existed, and passed again with the strip hidden through `KAICALC_MUTATION_CSS`. **Persistent chrome above the fold is exactly the budget that work was protecting.**

The header row is already 93px tall and carries a 67px logo, so a 44px control fits in space that is there and the row does not grow. **The lockup is not altered**: the logo keeps its own element, its own size and its own spacing, nothing is drawn over or through it, and `.brand` takes `margin-inline-end: auto` so it stays hard against the reading-start edge rather than being stranded mid-row by `space-between`. Below 720px the row wraps, putting the chooser on its own line above the brand — at 390px the control and the 220px wordmark cannot share a line, and without wrapping the wordmark was clipped by the viewport edge.

**On the panel it is a floating capsule at the top inline-start, not a strip** (v1.34, amending v1.27 item 9). The panel has no above-the-fold budget to protect, so the capsule is not squeezed into an existing row the way the calculator's is; but a full-bleed white band with a rule under it is chrome the panel grew in order to hold one small control, and it is what made the chooser read as debug scaffolding. It floats on the page's own ground instead.

**And the page's own ground is the page's, not the shell's** (v1.35). The five gate pages are a Kale field painted by `.gate`, and the chooser is a **sibling** of `.gate` — `brand/base.html` owns it and every page's `{% block shell %}` replaces what is under it — so "the page's own ground" above the shell was `body`'s white. The band v1.34 deleted came straight back as a 72px white strip with a hard edge under it, and the page scrolled by exactly the height of the chooser, because `.gate` asks for `100vh` and something now sat above it. **`body` carries the ground on any page wearing the gate shell, and the shell claims what is left of the viewport.**

**The ground is derived, never declared twice.** `body:has(> .gate)` selects it, in `brand.css` for the background and in `language.css` for the capsule's skin. A class on `<body>` was refused: the gate shell is chosen by *not* overriding a block, so the six templates that override it with `<main class="page">` would each have to remember to say so, and the failure mode is white text on a white page. Where `:has()` is unsupported the body stays white and the capsule keeps its light skin — the fallback is v1.34's appearance, not an unreadable one.

**One capsule, two skins, and only colour differs** (v1.34, amended v1.35). It is a 1px hairline, a 12px radius, a layered shadow, and an 18px globe at the inline-start — and the `<select>` inside gives up its own border, radius and ground, because the frame belongs to the component and drawing a second one 2px inside it is the implementation that reads as a mistake. The panel's submit button is a **segment** of the same capsule, joined at its inline-end corners; the button cannot be designed away, since that surface has to work with scripting off.

Every colour is a `--lang-*` token declared on `.language-bar` itself, which is what keeps `language.css` free of any dependency on `brand.css`; one block restates them for the Kale ground. **Nothing but colour is restated** — 44px, the radius, the margins, `width: 20ch` and the 720px label rule are shared, so the two skins cannot come to disagree about the shape of the control.

| | Light ground (Tabler, and any `.page` shell) | Kale ground (the gate shell) |
| --- | --- | --- |
| Surface | White | Kale at 12% white |
| Ink | Kale | White |
| Globe's disc | Pea tint | Pea |
| Submit segment | Kale ground, white ink | White ground, Kale ink |
| Elevation | contact shadow and a soft lift, in Kale | a **lit** top edge and a deep seat |
| Focus ring | Blueberry | Banana |

Elevation is inverted rather than recoloured, which is why the whole stack is one token: on a light ground an object is raised by the shade it casts, and #003223 cannot be darkened perceptibly at a 16px blur. White was refused for the Kale surface because it is the sign-in card's material and a second white object at the top of the page competes with the one the reader came for.

**A ground block out-specifies the state rules, and that is a trap this file has already fallen into.** `body:has(> .gate) .language-bar` is 0-2-1; `.language-bar:focus-within` is 0-2-0. A ground assigning `--lang-hairline` therefore **beats** focus and hover, and both went silently dead on the ground where the focus ring matters most. A ground names `--lang-hairline-rest`, `-hover` and `-focus`; only the state rules assign `--lang-hairline`.

**The focus ring is two lines and it is chosen per ground.** Blueberry measures 2.2:1 on Kale, under the 3:1 a non-text indicator needs; it stays Blueberry on the light ground, where `brand.css` rings the card's own inputs with it, and is Banana at 10.9:1 on Kale. Underneath either, the capsule's hairline goes to full contrast, so losing one line still leaves an indicator. The ring carries `outline-offset`, so it is drawn on the **page's** ground and is measured against that.

**The hairline is an inset `box-shadow` rather than a `border`, and the reason is the budget above.** The `<select>` is 44px because that is the touch target; a 1px border round it makes the capsule 46px and the wrapped row at 390px 2px taller than it was measured at. An inset shadow draws the same line and occupies no layout, so the treatment costs nothing: **92px at 1278×983, 92px at 938×898 and 126px wrapped at 390×700, measured before the change and again after it, identical**. (92 rather than the 93 above: the same row, remeasured — `.header-inner` declares `min-height: 92px` and the logo is 67px inside 24px of padding, so the min-height is what decides it.)

**The globe is inline SVG on both surfaces, and `aria-hidden="true"`.** §7.6 rule 7 forbids a runtime asset from a third-party host, the public policy is `img-src 'self' data:`, there is no icon font and there is no build step, so it is drawn rather than fetched — by `web/js/i18n.js` on the calculator and in `brand/_language_chooser.html` on the panel. It is silent because the `<label>` already names the control. Below 720px, where the label is `.sr-only`, it is the only thing on screen saying what the control is for.

**The mirroring is from `dir` and nothing else.** The globe is at the capsule's `padding-inline-start`; the `<select>`'s arrow is the browser's own, drawn at the select's inline-end. They therefore sit on opposite sides of the capsule in both directions, which is why the arrow is left alone rather than replaced with a drawn one.

**The panel's rules live in `admin/static/language.css`, linked from `brand/base.html` and from `sqladmin/layout.html`'s `head` block.** `brand.css` reaches the five gate pages only — the panel proper is Tabler, whose `<head>` sits in a template this project does not fork — so rules placed there style the login page and no screen behind it. One file for both skins is what keeps them from drifting; `brand.css` must not carry a `.language-bar` rule again.

**Each language in its own name** — `Deutsch`, never `German`. An endonym is never passed through `t()`.

**Machine-translated languages are marked on their own option**, so the warning can be read *before* somebody picks and not only after. The non-dismissible notice is unchanged. English and Chinese carry neither.

**It must work without JavaScript, and the two surfaces satisfy that differently:**

- **The panel** renders through FastAPI and genuinely works with scripting off, so its chooser is a real `<form method="post">` posting to `/admin/language`, with a visible submit button and no `onchange`. It is registered outside sqladmin's `login_required` wrapper, because the login page is the page a person who does not read English needs most.
- **The calculator** is ES modules end to end and renders nothing without scripting. Its chooser is therefore **built by `web/js/i18n.js`**, so it can never exist as a control that is present and does nothing, and `index.html` carries a `<noscript>` note about the page as a whole. A chooser requiring JavaScript adds no degradation this page did not already have.

**Changing language re-renders in place; it must not reload.** The wizard's state lives in memory, so a reload would discard every entry a visitor had typed.

**The panel's endpoint carries no CSRF token, deliberately.** `admin/csrf.py::issue_token` writes into the session and would mint a signed session cookie for every anonymous visitor to the login page, which today mints none — a real per-visitor identifier created to protect a cosmetic preference. A stateless same-origin check on `Origin` stands in its place. The `next` field is validated as a path within `/admin` and never used as a URL.

### 7.7.5 The two surfaces do not ship the same languages

The calculator ships twenty-one; the panel ships English and Chinese. **The panel's chooser lists only what the panel has** — no dead entries.

When the stored choice names a language the panel has no catalogue for, the panel renders **English** (not the browser's language, which the visitor had already overridden), and **states beside the chooser which language it could not give them, in that language's own name.**

**The cookie is left exactly as it was.** Silently rendering English would pretend no choice was made; showing "Follow the system" as selected would be a lie about what is stored; and rewriting the cookie to `auto` would destroy the calculator's language from a screen the visitor opened for an unrelated reason. That last is the tempting implementation and is asserted against.

This branch is narrower than it looks: it requires a stored language that no panel catalogue claims *even after truncation*, so `zh-Hant` never reaches it.

### 7.7.6 `Vary` and caching

**The panel appends `Vary: Accept-Language, Cookie`** to every response, including the ones its inner middlewares refuse — a 403 cached without it is served to everyone. Appended, never assigned: FastAPI sets `Vary: Cookie` on session responses and overwriting it would let a cache serve one staff member's page to another. `Cookie` joined the header at v1.27 because the response now depends on the stored choice, which **overrides** the header and is therefore the more dangerous half to omit. The cost is nil: the panel is authenticated and no shared cache stores it.

**The static origin sets no `Vary`, and still does not.** This is the payoff of building the calculator's chooser client-side: every visitor is served a byte-identical `index.html` and the cookie is read by JavaScript after the response arrives. Had the calculator negotiated server-side, `Vary: Cookie` there would mean a separate cached copy of the HTML, the stylesheet and both font faces per visitor — a cache that stores everything and hits on nothing.

### 7.7.7 What is never translated

Decimals (they cross the wire as strings and take no locale-aware separator on either surface); `code` identifiers; factor set version labels; metric units and `metric.name`; the equivalence sentences, which §3 defines as `label_template` interpolated by the engine; the operator messages in `admin/cli.py` and `docker/init.sh`; and **everything a staff member typed** — destination names, food categories, sector names, factor notes, audit log contents. Ruled 2026-08-14: anything a staff member can edit is published exactly as written.

The consequence, stated rather than discovered: a Thai visitor gets a Thai interface listing English destination names.

**On the statistics page this reaches into the charts**, and it is the rule most likely to be "fixed" by somebody who reads an English legend on a Thai page as a bug. Every bucket label — in a legend, in a tooltip and in the text list beneath the chart — is the API's `label`, so it is **identical in every language**, and a test asserts the legends are unchanged across a language switch rather than merely present. Every figure is likewise pinned to `en-NZ`: axis ticks, counts, shares and masses. Read in Arabic, where following the active locale would render Eastern Arabic numerals, and asserted there.

**News content is the same rule pointed outward.** The home page's post titles, excerpts and dates come from the client's WordPress site and are published as written; only the page's own furniture around them is translated — the fallback wording for a post with no title, the link text and its accessible name.

**Date and number *formats* are O-4, not O-8**, and O-4 promises nothing. `Intl.DateTimeFormat('en-NZ', …)` stays pinned on both the statistics page and the news feed; the sentence around a timestamp is translated, the timestamp is not reformatted.

### 7.7.8 The document element

**Every page on both surfaces carries `lang` and `dir` on `<html>`, and both state what was actually rendered.**

| Rendered | `lang` | `dir` |
| --- | --- | --- |
| English | `en-NZ` | `ltr` |
| Any other language | its own code (`zh`, `ar`, …) | from that catalogue |

**`lang` is the rendered language, never the stored choice.** The two differ whenever somebody has chosen a language the surface has no catalogue for — the calculator has twenty-one and the panel two, so Arabic chosen on the calculator renders the panel in English. English is then what `lang` says. Announcing `ar` on an English page would hand a screen reader English words to read with Arabic phonetics, which is the same defect as the one below pointed the other way, and no better for being well meant.

**`dir` is emitted on the panel although no panel catalogue is right-to-left.** It is derived from the catalogue rather than fixed, so the first RTL catalogue added to `admin/locales/` announces itself; an absent `dir` is not neutral, it keeps asserting left-to-right by omission. **This is not a claim that the panel's layout mirrors** — no RTL panel catalogue exists to try it on. The calculator's RTL layout is separately exercised, in Arabic and Urdu.

> **This was a recorded defect from v1.27 and is closed at v1.28.** The panel proper rendered `<html lang="en">` in every language. `sqladmin`'s own `base.html` hardcodes it and the `<html>` element sits outside every block that template defines, so the seam that already replaces `topbar` cannot reach it. It is closed **without a second vendored template**: `admin/i18n.py::_HtmlElement` is a Jinja extension that rewrites that one line in sqladmin's own source as the template compiles. Nothing is copied, so an upgrade that adds to `base.html` is picked up in full — the opposite of the vendored `_macros.html`, whose stale copy suppressed 0.31.0's menu markup for a release cycle. The rewrite carries that copy's drift guard: a literal it cannot find raises rather than silently not applying, and the message names version skew as the first thing to check.

### 7.7.9 Strings that are not in the DOM

**`applyToDocument()` is not the whole mechanism, and a surface that assumes it is will translate everything except its most visible label.**

`applyToDocument()` walks `data-i18n` markers and re-renders text nodes and attributes. That covers static HTML and every element a module builds. It cannot reach a string that was handed to something else at construction time — on this front end, a **Chart.js chart**: `plugins.title.text` is copied into the instance and painted onto a canvas bitmap, and the canvas's own `aria-label` is set once when the element is created.

**So a chart is destroyed and rebuilt when the language changes.** `stats.js` holds its last statistics response and re-renders from it; `renderStats` calls `destroyCharts()` and then constructs each chart again, reading `t()` at construction. Three consequences are binding:

1. **Nothing re-fetches.** The figures do not depend on the language, and a page that calls `GET /api/v1/stats` on every language change rate-limits itself. The same rule already applies to `methodology.js` and `GET /api/v1/factors`.
2. **A translatable string reached by reference must be a function, not a constant.** `stats.js::BREAKDOWNS` holds `() => t('Destinations entered')`, the shape `methodology.js::METADATA_FIELDS` already used. A module-level constant is evaluated once, at import, and stays in the language the page opened in — which looks correct until somebody switches.
3. **The test must read the rendered title, not the call.** `tests/web/test_i18n_browser.py::test_the_charts_are_rebuilt_in_the_new_language` reads the title off the live chart's laid-out title block, whose measured height is what distinguishes a drawn title from a stored string, compares it with the catalogue file in two languages, and asserts the previous canvases are no longer known to Chart.js — a chart left alive behind its replacement is the failure this path invites.

---

## 7.8 `config.js` — deployment configuration (owners: C and D; produced by E's container)

```js
/** Origin of the client's WordPress site, `scheme://host[:port]`. '' = no news feed. */
export const NEWS_ORIGIN;   // string
/** Origin of the public API. '' = the relative `/api/v1`, which is the default topology. */
export const API_ORIGIN;    // string
```

**No deployment domain appears in `web/`, in `docker/nginx.conf`, or in any built image.** Both values arrive from the environment at container start. `docker/web-config.sh` runs from nginx's `/docker-entrypoint.d/` before the server binds and writes **two** things from the same variables: the `connect-src` and `img-src` of the public Content-Security-Policy, and this module. The checked-in `web/js/config.js` carries the same exports with empty values, so a plain checkout serves a working front end with no build step involved.

| Variable | Feeds | Unset |
| --- | --- | --- |
| `KAICALC_NEWS_ORIGIN` | `NEWS_ORIGIN`, `connect-src`, `img-src` | no news feed; the home page removes its news section |
| `KAICALC_API_ORIGIN` | `API_ORIGIN`, `connect-src` | `/api/v1`, relative — the designed same-origin topology |
| `KAICALC_NEWS_IMAGE_ORIGINS` | `img-src` only (space-separated) | nothing added |
| `KAICALC_TRUST_FORWARDED_HEADERS` | the `map` blocks that build `X-Forwarded-Proto` and `X-Forwarded-For` | `false` — both headers are built from what this nginx observed, and an inbound copy of either is discarded |

### 7.8.1 The two forwarded headers, when this nginx is not the outermost proxy

`docker/nginx-proxy-headers.conf` sends `X-Forwarded-Proto` and `X-Forwarded-For` to both applications. It built them from `$scheme` and `$remote_addr` — from what *this* nginx observed — which is correct while this nginx is the outermost proxy and wrong the moment it is not. It very often is not: a public IPv4 has one port 443, and a client whose address already serves another site over TLS will put this stack behind that terminator.

```
browser --https--> the client's edge nginx :443 --http--> this stack :18080
```

DNS, certificates and hosting are out of this project's deliverable. *Behaving correctly behind a TLS terminator somebody else operates* is not — and in that topology the old configuration failed in two directions at once. `X-Forwarded-Proto` became `http`, overwriting the edge's `https`, so the panel reasons about scheme from a false premise. `X-Forwarded-For` became the edge's own address, so every visitor on earth arrives as one caller: §6.5's per-address rate limit becomes a single site-wide counter and one `ip_block` row denies everyone — the state `admin/config.py` already names as the cost of getting this wrong.

**Both headers now come from `map` blocks in `docker/nginx.conf`, gated on one opt-in.** `KAICALC_TRUST_FORWARDED_HEADERS` is rendered into the template by `docker/web-config.sh` as the literal `on` or `off`; anything that is not a boolean stops the container, on the same terms as a malformed origin.

| | `X-Forwarded-Proto` | `X-Forwarded-For` |
| --- | --- | --- |
| **off** (default) | `$scheme` — an inbound copy is discarded | `$remote_addr` — overwritten, never appended |
| **on** | the inbound value **if it is exactly `http` or `https`**, normalised to lower case; otherwise `$scheme` | `$proxy_add_x_forwarded_for` — the inbound chain with this proxy's peer appended, so the left-most entry stays the visitor the edge saw; `$remote_addr` alone when no header arrived |

> **The obvious repair — "forward what came in, fall back to what we saw" — is wrong unconditionally, and the two headers are not equally dangerous.** A forged `X-Forwarded-Proto: https` marks the staff session cookie `Secure` and the browser stops sending it over http: self-inflicted, not an attack. A forged `X-Forwarded-Proto: http` on a request that really is https takes `Secure` **off** a live session, which is the direction that costs something. And a forged `X-Forwarded-For` lets a caller name their own address — out of the rate-limit bucket, out of the blocklist, and into whatever else reads it. So trusting is an opt-in an operator turns on only when an edge they control is genuinely the only way in.

> **It is a separate setting from `PROTECTION_TRUSTED_PROXY`, and that is a decision.** The two answer different questions about different hops: `PROTECTION_TRUSTED_PROXY` asks whether `api/` and `admin/` may believe the `X-Forwarded-For` **this proxy sends them** (`db/detection.py::client_ip`), and the production answer is yes *precisely because* this proxy overwrites the header rather than trusting it. `KAICALC_TRUST_FORWARDED_HEADERS` asks whether this proxy may believe the header **it receives**. Reusing one name would mean that turning on application trust also, silently, made this proxy believe headers from any caller who can reach it — one switch meaning two things in two layers, which is its own defect. All four combinations are real deployments, and the one that looks configured and does nothing (`on` here, `false` there) is reported by `docker/web-config.sh` at start-up. It is a warning and not a refusal because this container's view of the applications' setting is second-hand.

> **Nothing new is written down.** §2.3 forbids storing an address, and the `kaicalc` access-log format still carries time, request, status, size and latency and no header and no address. These variables exist only to be handed to `proxy_set_header`. `X-Real-IP` is deliberately *not* switched: it stays `$remote_addr`, the peer this nginx is actually talking to, so one header always states the unembellished truth about the connection.

> **What this does not do.** It does not check *which* address the request arrived from. nginx's `set_real_ip_from`/`real_ip_header` would be the stronger control, and it needs the edge's address written into this configuration — which this contract has just finished removing the last hard-coded host from. An operator turning this on is stating that the only route to `:18080` is through their edge, which is the same statement `PROTECTION_TRUSTED_PROXY` already asks for and the `ports:` removal in `docker/compose.yaml` enforces.

> **Why this section exists at all: two copies of one domain that had to agree.** `web/js/api.js` held `https://kaicommitment.org.nz/wp-json/wp/v2/posts` and `docker/nginx.conf` held the same host in `connect-src`. The API was not affected — `API_BASE` was relative — but the pair was a live trap for the moment it stopped being: put the front end on a CDN or the API on its own subdomain and every call is refused by our own policy, with nothing in the failure pointing at nginx. **The two copies also fail asymmetrically, which is why nothing would have caught the drift:** a wrong policy means the news quietly does not load, and a wrong URL means the browser goes and asks a domain nobody chose. This project's deliverable is source code and documentation, with DNS, certificates and hosting explicitly out of scope, so the domain belongs to the deployment recipe (`docker/compose.yaml`) and not to an artefact.

> **Why a generated ES module and not a `<meta>`, a `sub_filter` or a config endpoint.** The front end has no build step and no framework (§7.6.10), so the value has to arrive at runtime, and it has to survive the policy it is helping to write. A same-origin module is fetched under `script-src 'self'` like every other module and joins a module graph a deferred `<script type="module">` already loads, so it costs no request before first paint. A `<meta>` needs every HTML page rewritten and hands the value to anything that can inject an element; `sub_filter` rewrites every response body and interacts badly with the `gzip on` two directives away; `GET /config.json` adds a request the feed has to wait on for nothing.

> **An unset news origin is a supported deployment, not an error.** Most installations of this calculator have no WordPress behind them. `getNewsPosts` returns `null` — distinct from the `[]` that means "asked and got nothing usable" — `fetchNews` passes it through, and `home.js` removes the whole `.home-news` section. It does **not** leave the heading standing over "temporarily unavailable", which reports an outage for a service nobody configured, and it does not request a guessed host.

> **`API_ORIGIN` is configurable, and the guards on it are the point.** Same-origin remains the default and the designed topology. Setting it makes this the front end of a tool whose numbers are the product, pointed at an API somebody chose: so the value is read only from the operator's environment at container start — never from a URL, a query parameter, `sessionStorage` or an element in the page — `docker/web-config.sh` **refuses to start the container** unless it is a bare `scheme://host[:port]`, and `connect-src` is generated from the same string, so the page can reach the one origin named and no other. Refusing to start is deliberate: a typo must be a container that does not come up, not a calculator quietly showing the wrong figures. That validation is also the injection guard — nothing reaching the header or the JavaScript literal can hold a quote, a semicolon, a space or a newline.

> **`envsubst` is called with an explicit three-name shell-format list.** With no argument it substitutes every `$name` in the file, and this configuration is full of nginx's own: `$time_local`, `$request`, `$status`, `$body_bytes_sent`, `$request_time`, `$uri`, `$scheme`, `$http_host`, `$remote_addr`, `$http_x_forwarded_proto`, `$proxy_add_x_forwarded_for`. Each would become the empty string, producing valid nginx syntax that logs blank lines for every request, redirects to nothing, and — since §7.8.1 — forwards an **empty** client address to both applications, which `db/detection.py::client_ip` normalises to `None`: the blocklist and the rate limit skipped for every caller, with nothing raised. `tests/test_web_runtime_config.py::test_envsubst_does_not_eat_nginx_own_variables` is what holds it; the mutation that drops the list fails on `$time_local`. **A name on that list is a name `envsubst` is licensed to replace, so each is a string that appears in the template for exactly that purpose and nowhere else** — `KAICALC_TRUST_FORWARDED` occurs once, as the sole `default` of one `map`, and the only two values that can reach it are the literals `on` and `off`. The alternative weighed was a second generated file holding whichever of two literal `map` blocks applied, which keeps the list at two names at the cost of a second place the forwarding rule is written down.

> **`img-src` follows `KAICALC_NEWS_ORIGIN`, which reverses an earlier decision.** The directive was kept at `'self' data:` because widening it meant *guessing* the media origin. The origin is configuration now, so there is no guess left to make, and it grants nothing new in practice because `connect-src` already reaches that host. `post.imageUrl` is still rendered nowhere; the note on `createNewsCard` in `web/js/home.js` records what changed and what is left to check — a WordPress library that serves from a CDN is a different host, and `KAICALC_NEWS_IMAGE_ORIGINS` is where it goes.

**Tests.** `tests/test_web_runtime_config.py` sets a value that is not the default and reads it back out of the built image, from both the rendered header and the served module, for the configured and the unconfigured case; `tests/web/test_csp.py` starts a second, unconfigured container and asserts, with the policy enforced in a real browser, that the home page removes its news section **and** makes no off-origin request.

---

# 8. Admin Panel (owner: E)

Built on `sqladmin`, mounted at `/admin`, authentication required.

## 8.1 Models Exposed for Direct CRUD

**Taxonomy and factors — eleven, from v0.1:** `sector`, `food_category`, `destination`, `destination_group`, `metric`, `unit_preset`, `constant`, `formula`, `equivalence`, `factor_upstream`, `factor_downstream`

**Comparison scenarios — two, added v0.10 (§2.2a):** `comparison_scenario`, `comparison_scenario_line`. Edited under their own "Comparison" category. Unlike the taxonomy tables these may be **deleted** through the panel: a scenario is a staff member's own saved test case and is referenced by nothing else in the schema.

**Blocklist — one, added v0.12 (§2.3):** `ip_block`. Listed here for completeness but **it is not generic CRUD, and treating it as such would leak the thing it exists to avoid storing.** The view is list-only and restricted to `role = admin`; `ip_hmac` never appears in `column_list`; a block is created through the custom form at `/admin/ip-block/block` and removed through the audited `unblock` action rather than `sqladmin`'s generic delete. §8.3 is the specification.

Requirements: list views must offer search and filtering.

**Every write must produce an `audit_log` entry.** Admin CRUD achieves this through an `AuditedModelView` base class that every view above inherits; factor-set lifecycle operations do it inline in the repository. Both call `write_audit()` (§5.5), which is the only code that inserts into `audit_log`.

> This list said "eleven" for four revisions after it stopped being eleven. `comparison_scenario` and `comparison_scenario_line` landed in v0.10 and `ip_block` in v0.12, and each was specified in §2 and §8.2/§8.3 without being added here — so §8.1, the section that states the audit requirement, named three fewer tables than the panel actually writes to. A reader auditing "does every write produce an audit entry" against this list would have concluded yes while three tables sat outside it.

`AuditedModelView` captures the pre-change row in the before-write hook — the after-write hook only ever sees the new values — and hard-codes `can_create = can_edit = can_delete = False` on the `audit_log` view itself.

## 8.2 Custom Views

| View | Path | Function |
| --- | --- | --- |
| Factor sets | `/admin/factor-sets` | Clone, publish, archive, roll back; shows draft/published/archived state. **`status` is not on the edit form** — these four actions are the only way it changes, so each one takes `SELECT ... FOR UPDATE`, revalidates the set's formulas where relevant, stamps `published_at` / `published_by`, and writes its own audit entry. Archiving the currently published set is permitted and takes the calculator offline: `NO_PUBLISHED_FACTOR_SET` (503) is the designed response to having none |
| Flag as placeholder data | `/admin/factor-set/action/flag-placeholder` | Sets `is_mock`. Any status, either role, one confirmation and no proof — §2.2's safe direction |
| Clear placeholder flag | `/admin/factor-set/clear-placeholder` | Clears `is_mock`, after the current password or a live TOTP code. Any status, either role. Names the consequence on the page and in the dialog: this removes the placeholder warning from every public result and export, immediately. Writes `audit_log.action = 'clear_placeholder'` with both values |
| Dry run | `/admin/try` | Enter a test scenario, call `POST /api/v1/calculate` with **`X-Dry-Run: true`** and a `dry_run` object (§6.2.1), and display the line-by-line breakdown |
| Pre-publish comparison | `/admin/factor-sets/{id}/compare` | Run a fixed set of standard test scenarios against **both** the published set and this draft, and show the published value and the draft value per metric, side by side. The last gate before publishing. |
| Submissions | `/admin/submissions` | Record-level list with search and filtering; allows setting `excluded_from_public` with a reason |
| Audit log | `/admin/audit-log` | Read-only, filterable by actor, time and table. **`role = admin` only** from v1.15 — see the change log for why "their own entries only" was rejected |

> The dry-run view **must** send the dry-run header. Staff will run dozens of calculations while tuning a formula, and persisting them would directly pollute the public statistics.

The comparison view is two dry-run calls per scenario — one with `factor_set_version` set to the published label, one to the draft — shown side by side, **not** differenced. Decision 6 puts every impact number server-side, in exactly one place; `POST /api/v1/calculate` computes `net_benefit` only for a current-versus-alternative comparison made *within one call*, and has no concept of a difference between two separate calls made at two different `factor_set_version`s. Subtracting the two response strings in the view or the template would put a number in front of staff that no server-side calculation ever produced, which is exactly what Decision 6 forbids — so the page renders both values, plainly labelled, and says in words that no difference is shown. Whether `POST /api/v1/calculate` should grow a two-version diff so this page can show one is open (raised in the E7 task report; not yet assigned an owner). The standard scenarios it runs are staff-editable rather than hard-coded; hard-coding them would reintroduce "change the code to change the configuration", which Decision 2 exists to prevent. They live in `comparison_scenario` / `comparison_scenario_line` (§2.2a), edited through their own CRUD screens like every other §8.1 table.

Because a dry-run request body is a `bundle` plus a scenario, the dry-run view can offer a **Save as regression case** action that writes `tests/golden/case_NN/{bundle,request,expected}.json` (§10.1) directly from a run staff considers worth keeping. Tuning factors then produces golden cases as a by-product rather than requiring them to be authored separately.

## 8.3 Accounts, Authentication and Recovery

`sqladmin` provides an `AuthenticationBackend` abstract class — `login()`, `logout()`, `authenticate()` — and nothing more. There is no built-in user store, password hashing or login page; all of it is specified here.

### Roles

| Capability | `staff` | `admin` |
| --- | --- | --- |
| Taxonomy, factor and formula CRUD | ✅ | ✅ |
| Dry run, view submissions | ✅ | ✅ |
| View the audit log | ❌ | ✅ |
| Set `excluded_from_public` | ✅ | ✅ |
| Publish, roll back | ✅ | ✅ |
| Create, deactivate, re-role and delete accounts | ❌ | ✅ |
| Reveal an unclaimed password | ❌ | ✅ |
| Reset another account's MFA, issue a random password | ❌ | ✅ |

Publishing is available to both roles deliberately: `audit_log` records who published and rollback is one action, so accountability and recovery are already covered. Restricting it would stall routine work whenever the administrator is unavailable, in a team of three to five people.

### The unclaimed password (v1.15, widened v1.16)

`staff.unclaimed_password_enc` holds **the password this system last minted for
an account and nobody has claimed yet** — the one it was created with, or a
replacement an administrator issued since — encrypted with Fernet under a key
derived from `SECRET_KEY` by HKDF with its own `info` (never the one
`staff_totp_device.secret_enc` uses). It is NULL for the whole of an account's
life except the window between a minting and the next password change.

**This is the only reversibly-stored credential in the system, and it is a
deliberate weakening taken by the repository owner with the cost stated.**
Everything else a person types is hashed and cannot be read back: `password_hash`
is bcrypt from the instant the row exists, and `must_change_password` says
nothing whatever about how the password is stored. What v1.15 added is a second,
separately encrypted copy — not the removal of a hash.

**Why.** A one-time reveal is easy to lose. The page that mints a password shows
it once; a closed tab loses it, and the administrator then has to issue a
replacement, which stops the password already read out to the colleague from
working. This project has already lost a set of recovery codes to exactly that
shape.

**Why it covers issued passwords too (v1.16).** v1.15 stored only the creation
password and had `issue_password` clear the column. So the loss just described
was prevented on one screen and not on the one next to it, and the only recovery
from losing an issued password was to issue *another* — invalidating one that
may already have been handed over. The two are now aligned: `issue_password`
**overwrites** the column with what it mints. Overwrites, never leaves alone: a
previous value is a password that no longer opens the account, and a row that
went on offering it would hand an administrator a dead string to read out.

**The cost, and how it changed.** Anyone holding both a database dump and
`SECRET_KEY` can log in as every account holding an unclaimed password. A newly
created account is pre-MFA in the way that matters — the attacker reaches the
forced-enrolment page and enrols their own authenticator, so the password is the
whole of the protection; an account that has only had a password *issued* still
holds its second factor, so there the password is one of two. What bounds the
exposure is the column's lifetime, and v1.16 lengthened it: no longer "between
creation and first login" but "until whoever holds the account sets a password
of their own", which for a password issued on a Friday may be Monday. Note that
in the shipped container arrangement `SECRET_KEY` lives in a named volume that
is **not** mounted into the database container, so an ordinary dump does not
carry it; a compromise of the Docker host carries both.

**Written by** `create_staff` and `issue_password` — the two functions that mint
a password its holder did not choose — and by nothing else. **Cleared
unconditionally by** `set_password`, which is what both the self-service screen
and the forced change at first login call, and which is the only place a
password arrives that its holder chose. **Gone with the row** on `delete_staff`.
`kaicalc-admin rotate-key` re-wraps it alongside the TOTP secrets, in the same
decrypt-everything-before-writing-anything window.

`must_change_password` and a non-NULL column now agree on both writing paths,
and they are still **different facts that must not be read off each other**: an
account created before v1.15, or one whose stored copy went undecryptable across
an unrotated `SECRET_KEY` change, owes a password change with nothing to reveal.
`StaffAdmin` renders them as separate columns for that reason — conflating them
would have the list promising a reveal the reveal page then refuses.

**Reading it.** `/admin/staff/unclaimed-password`, `role = admin`, reached from
the `Show the password waiting to be collected` action on `/admin/staff/list`.
It takes the same re-authentication proof as creating an account and deleting
one — the current password or a live TOTP code, on top of the administrator
session — because being signed in is the one thing somebody holding a stolen
session would also have. Every successful reveal writes an `audit_log` entry
with `action = "reveal"` and `after_json = {"username": ..., "revealed":
"unclaimed_password"}`; the entry never carries the value, and
`unclaimed_password_enc` is in §5.5's `REDACTED_FIELDS` so that a whole-row
snapshot cannot land the ciphertext in an append-only table either. The list
page shows *that* a password is waiting without showing it.

There is no CLI command for the reveal. Anyone with a shell on the container can
read the column and `SECRET_KEY` directly, so one would add a path without
adding a capability.

**The column was named `initial_password_enc` in v1.15.** Migration `0012`
renames it in place; the HKDF `info` byte string is deliberately left as
`b"initial-password-encryption"`, because it is key-derivation material and
editing it to match would make every stored value undecryptable.

### Mandatory MFA

Every account enrols a TOTP authenticator. There is no opt-out.

```
admin creates account  ->  random password, shown once on the page,
                           handed over out of band; also kept
                           encrypted (staff.unclaimed_password_enc)
                           and re-readable at
                           /admin/staff/unclaimed-password
        |                  (an administrator issuing a replacement
        |                   later re-enters this state: same column,
        |                   same reveal, same forced change)
        v
first login            ->  must_change_password = true
        v
forced password change ->  unclaimed_password_enc cleared; nothing
                           left to reveal, for good
        v
forced TOTP enrolment  ->  QR code plus 5 single-use recovery codes,
                           shown once; one correct TOTP required to finish
        v
mfa_enrolled_at set    ->  access granted
```

**While `mfa_enrolled_at IS NULL`, every route except the password-change and enrolment pages is refused, `require_staff()` included.** Without that, enrolment is advisory — a user can navigate straight past it by typing a URL.

### The account's own security screen — `/admin/security` (owner: E)

**Not an administrator screen.** Any signed-in account reaches it, `staff` included, and it acts only on the account named in the session — no form here carries a username or an account id. A `staff` member must be able to manage their own security without asking anyone; `/admin/staff` stays administrator-only.

It exists because §8.3's self-recovery rule removed the only way to change one's own credentials alone: `issue_password` and `reset_mfa` are refused against the actor's own account, and `/admin/change-password` opens only while `must_change_password` is set, so it is the forced-change page and not a self-service one.

| Action | Requires |
| --- | --- |
| Change the password | **The current password.** A TOTP code is *not* accepted — the session presenting it has already cleared the second factor, so a code proves nothing the cookie did not, while the current password is the one secret a stolen session does not carry |
| Add an authenticator | The current password, **or** a code from a device already enrolled |
| Remove an authenticator | The same, **and** it must not be the last confirmed device |
| Rename an authenticator | The same. Any device may be renamed, including an unconfirmed one |

Every one of the four writes an `audit_log` entry naming the actor. Changing the password and removing a device each bump `session_generation` — a credential change ends every other session — and each re-stamps the acting session, which did the thing deliberately and is not what is being evicted. **Renaming deliberately does not bump it:** a name is a note on the row, not a credential, and signing every session out over an edited caption would be a cost with nothing bought.

**A device name is editable at any time, and the screen says what renaming does not do.** The name reaches the authenticator app through the `otpauth://` label, which is consumed once, at the scan — so renaming changes this list and nothing on the phone. Re-minting the secret so the app could be relabelled is not on offer: it would invalidate a working second factor to correct a caption. Names are matched to devices by the ordinal, the date added and "last used". The alternative considered and rejected was dropping the name for the bare ordinal; the ordinal already exists and says nothing about the device, and a name is useless only while nothing can revise it.

**An unconfirmed enrolment is destroyed rather than left in the list.** `staff_totp_device.enrolled_at IS NULL` is a QR that was displayed and never proved, and it **cannot be continued** — the secret and the QR exist only in the response to the re-authenticated `POST` that minted them. It therefore dies at two moments, both in `admin/self_service_view.py`, calling `admin.accounts.discard_unconfirmed_devices`:

* on a **GET of `/admin/security`**, which is what leaving the page means here (the enrolment dialog's own Cancel is a link to this route), and
* on **beginning a new enrolment**, before the new secret is minted.

There is no timer, and no scheduled job. A deadline would leave a stored TOTP secret that has never authenticated anything sitting there for the length of it, in exchange for nothing — the row's only possible future is to be destroyed. The reap is audited and the page states it when it happens; a row vanishing silently reads as data loss.

**The reap can never touch a confirmed device.** It filters on `enrolled_at IS NULL` and nothing else, it removes rows individually rather than clearing the collection, and `/admin/security` is unreachable until `mfa_enrolled_at` is set, so a confirmed device always remains. `_sync_mfa_enrolled_at` stays the sole writer of `mfa_enrolled_at` and is called by every path that adds or removes a device, `begin_mfa_enrolment` now included.

**The last confirmed device cannot be removed.** An account whose only second factor is deleted does not announce it: the session in hand goes on working exactly as before, and the discovery comes at the next login when `require_staff()` refuses the account. Recovery codes do not count as the other factor — they are single-use, there are five, and an account holding only those is counting down.

Re-authentication failures are charged to the **same throttle** as the login steps, for the reason given just below: this page accepts a six-digit code from a caller who by construction already holds a session.

Implementation notes: `pyotp` with `valid_window=1` (±30 s clock drift); `qrcode` with the SVG factory, which avoids a Pillow dependency; initial passwords and recovery codes from `secrets`, never `random`.

### Login throttling

Consecutive failures lock a username for a cooling-off period. **Password failures and TOTP failures share one counter** — throttling only the password step leaves a six-digit second factor, a 10⁶ search space, open to anyone who already has the password.

Counters are keyed by **username and held in memory**. They are not keyed by IP address and no IP address is stored, per Decision 3. Username keying is also the more precise signal.

### Recovery, with no email system

The project builds no email capability, so there is no reset link. Three layers, all required:

| Layer | Mechanism | Covers |
| --- | --- | --- |
| L0 | A second authenticator enrolled in advance at `/admin/security` | Lost phone, **before** it is lost; needs no second person and spends nothing |
| L1 | 5 single-use recovery codes issued at enrolment | Lost or wiped authenticator; needs no second person |
| L2 | Another administrator resets MFA and issues a random password | Recovery codes also lost |
| L3 | `python -m admin.cli reset-mfa <username>` on the server | Every administrator locked out |

**At least two administrator accounts must exist at all times, and at least two must be able to log in.** Deleting, deactivating or demoting an administrator is refused while *either* count is 2 or fewer:

| Count | Definition | Used by |
| --- | --- | --- |
| Active | `role = admin` and `is_active` | Bootstrap's "does this system have any administrator yet" check |
| **Usable** | Active, **plus** MFA enrolled, **plus** past the forced password change | The removal guard |

The second count exists because bootstrap creates two administrators carrying `must_change_password` and no enrolment. Counting only active accounts reports two usable administrators when there is one — so a system where the client onboarded `admin` and filed `admin2`'s printed password away would permit deactivating the only account anyone can actually log in as, and with no email system there is no way back.

This must be enforced in the service layer, not only in the form — `sqladmin`'s form validation can be bypassed.

> Consequence worth knowing: while fewer than two administrators are usable, **no** administrator can be deactivated or demoted, including one that was never onboarded. Eviction is still possible without deactivation — reset the account's MFA and issue a new password — but it is indirect. This errs toward "cannot be locked out" over "can always evict", which is the correct side for a small organisation with no email recovery.

### Removing an account — `/admin/staff/delete` (owner: E)

An account list that cannot shrink stops being an answer to "who can get into this system". Accounts are **deleted outright**, and the deletion costs the audit trail nothing.

**Why a hard delete rather than a tombstone.** `audit_log` holds **no foreign key to `staff`**, in the model or in `0002_audit_log.py`'s DDL, and cannot: `audit_log.actor` is `VARCHAR(128)` and also carries `cli`, `bootstrap`, `deploy-seed` and `unknown`, none of which is a row in `staff`. The trail therefore already stores identity the way a tombstone would be introduced to make it store it. Every entry a deleted account wrote **stays complete and still names it**; the same holds for `staff.created_by`, `factor_set.published_by` and `ip_block.created_by`, all text and all unaffected. A tombstone would leave the row and the username in place — not the capability asked for — while buying a property the trail already has.

**What is destroyed with the account** is exactly its credentials: `staff_recovery_code` and `staff_totp_device` cascade, at the database and through the ORM. That is correct rather than incidental — a stored TOTP secret must not outlive the account it authenticates.

Four guards, all in `admin/accounts.py::delete_staff` except the last:

| Guard | Why |
| --- | --- |
| **The account must already be deactivated** | Deletion then acts on a row `deactivate_staff` has made inert — sessions ended, generation bumped — rather than racing a request in flight, and the floor is checked at both steps. Refused with `AccountStillActiveError`, deliberately a different class from `LastAdministratorsError`: they are different problems with different next steps |
| **The two-administrator floor** | Named for deletion by the rule above. In practice it bites at deactivation, since a deactivated row is already past it |
| **No self-deletion** | `_guard_not_self`, the guard `issue_password` and `reset_mfa` already carry. It runs **before** the deactivation check, so aiming this at your own account is refused for being yours rather than for being active. An administrator deleting their own account removes the second party from a procedure whose whole value is that there was one |
| **Re-authentication** | The current password or a live code, at the point of the action — the proof `/admin/staff/new` already takes. A stolen session otherwise empties the staff list holding only the cookie |

Deletion writes its own `audit_log` entry carrying the whole identity — username, display name, role, and the counts of authenticators and recovery codes destroyed — rather than a reference to a row that is about to stop resolving. After the commit it is the only record the account existed.

**`reactivate_staff` ships with it**, because requiring deactivation first is defensible only if that step can be undone; without it, deactivating the wrong account is a trap whose only exits are leaving it in the list for ever and deleting it. It takes no floor guard (it only ever adds an active account) and no self guard (`authenticate()` refuses an inactive account, so nobody can be signed in as the account they would be reactivating — a guard there could never fire).

**One consequence to know about.** `last_password_change()` reads the trail by `(table_name='staff', row_id)`, and `staff.id` is a plain autoincrement integer. MySQL 8 persists its counter and does not reuse ids, but **SQLite hands out `max(rowid) + 1` and reuses one immediately**, and `admin/accounts.py` is imported by both. That read is therefore additionally scoped to `AuditLog.at >= staff.created_at` — an account cannot have changed its password before it existed. Scoped on the read side rather than by rewriting the deleted account's rows, so `audit_log` stays append-only.

Operational commands (`admin/cli.py`), both pass-throughs to the same service functions:

```
python -m admin.cli delete-staff <username>
python -m admin.cli reactivate-staff <username>
```

`delete-staff` passes `allow_self=True`, the exemption `reset-mfa` and `issue-password` already carry: layer L3 has no acting session to be the second party. **It reaches the self-recovery guard and nothing else** — the floor and the deactivation requirement are unconditional there, exactly as in the panel.

**Session invalidation.** Every credential change — password change, MFA
reset, deactivation — increments `staff.session_generation`. The signed
session cookie carries the generation it was minted under, and
`require_staff_username()` (§8.4) refuses any mismatch. An administrator
resetting a compromised colleague's account therefore ends that account's
live sessions immediately, without server-side session storage.

Note for B: this is enforced inside `require_staff()`, so the API layer
inherits it with no change on your side.

For E: `require_staff_username()` is not the only implementation of this
comparison. The admin panel is not routed through it — it is gated by
`AdminAuth.authenticate()` in `admin/backend.py`, which carries its own,
deliberately duplicated `session_generation` comparison rather than calling
`require_staff_username()`. The two are documented as needing to change
together (each names the other in its own comment), but nothing enforces
that beyond the comment — they have already drifted out of sync once during
this branch. E-4 through E-6 read this section: if either comparison
changes, check the other.

> **The submissions migration is `0008`, with `down_revision = "0007"`. Recorded here because it is the second reconciliation item this merge settles, and because the document that says otherwise is still on the shelf.** `docs/ToB_v3.0.md` §1.3 instructs B to file the three submission tables (`submission`, `submission_entry`, `submission_line`, plus the `COALESCE(food_category_id, 0)` functional index) as `alembic/versions/0006_submissions.py` with `down_revision = "0005"`. That was correct when it was written. Since then `0006` (comparison scenarios, v0.10) and `0007` (`ip_block`, v0.12) have both landed, so **ToB v3.0 is stale on this point and must not be followed literally.**
>
> Following it produces a **third Alembic head**, not a merge conflict: two migrations both claiming `down_revision = "0005"`, `alembic upgrade head` aborting on ambiguity, and `upgrade heads` then failing partway through on a table that already exists. **MySQL DDL autocommits**, so there is no transaction to roll back and the recovery is `DROP DATABASE` — which is survivable on a developer's machine and is not survivable anywhere else. This is the cheapest possible thing to get right and one of the most expensive to get wrong, which is the only reason it is written into the contract rather than left in a task brief.



`admin.accounts.issue_password(session, username, *, actor, secret_key, allow_self=False) -> str` performs the L2 half named above: it sets a random password, forces `must_change_password = True` (an issued password is in the same position as a bootstrap one and gets the same forced change — this is what distinguishes it from `set_password`, which clears that flag because the user chose the password themselves), stores that password encrypted in `staff.unclaimed_password_enc` so it can be read back until it is claimed (v1.16 — hence `secret_key`, required and keyword-only), and bumps `session_generation` so the account's live sessions end immediately. The plaintext is returned to be read out and handed over out of band, and never reaches `audit_log` — the entry records that `password_hash` changed, not what it changed to.

The CLI account-creation command is exempt from that rule; it only ever adds, and a system with no accounts yet must be able to bootstrap. While exactly one active administrator exists, the panel displays a non-dismissible banner advising that a second be created.

### Bootstrap

**On first start, when no administrator account exists, the application creates two.** A deployment therefore satisfies the two-administrator rule from the moment it comes up, rather than depending on whoever installs it remembering to run the CLI twice — and a system that starts with one administrator is a system that can be locked out by a single lost phone.

| Property | Behaviour |
| --- | --- |
| Trigger | Application start, only when the active administrator count is zero |
| Accounts | `admin` and `admin2` |
| Passwords | **Randomly generated per deployment, printed once to standard output.** There is no default password and no fixed value anywhere in the source. Since v1.15 each is also kept encrypted until that account claims it, so one lost line is recoverable by the *other* administrator from `/admin/staff/unclaimed-password`; losing both is terminal for the panel and the way back is `docker exec kaicalc-admin kaicalc issue-password` from the host — `kaicalc`, not `kaicalc-admin`, because `docker exec` runs no entrypoint and the console script exits on a missing `SECRET_KEY`. |
| State | Both carry `must_change_password` and no MFA enrolment, so `require_staff()` refuses them until both steps are completed |
| Idempotence | Runs once. A restart with administrators present creates nothing. |

**A fixed default password would be the single worst defect this system could ship.** `admin`/`admin` on a public panel is exactly how community-sector accounts get taken over, and it is the reason MFA is mandatory here in the first place. The generated passwords are shown in the start-up output and kept encrypted until claimed (see the row above); once both accounts have logged in and changed them there is nothing left to recover, and the way back is `docker exec kaicalc-admin kaicalc issue-password`.

> Deployment note for the handover documentation: the start-up output contains live credentials. Capture them, log in with both accounts, change both passwords, enrol both authenticators, then discard the output. Do not pipe first-start output into a shared log collector. Changing both passwords is also what clears the stored copies, so it is the step that ends the exposure described under §8.3's unclaimed password, not merely good hygiene.

> Two administrators is not sufficient on its own. A small organisation is likely to hand both accounts to the same person, or to replace phones at the same time. L1 is the layer that does not depend on a second human being available, which is why it is mandatory rather than a convenience. The panel prompts for regeneration once 2 codes remain.

### Operational commands (owner: E, must be documented for handover)

| Command | Purpose |
| --- | --- |
| `python -m admin.cli create-staff <username> "<name>" [--admin]` | Bootstrap and routine account creation |
| `python -m admin.cli reset-mfa <username>` | L3 break-glass |
| `python -m admin.cli issue-password <username>` | L3 break-glass — issues a random password and forces a change at next login; ends the account's live sessions |
| `python -m admin.cli rotate-key --old <k> --new <k>` | Re-encrypt every `staff_totp_device.secret_enc` after a `SECRET_KEY` change — **every device row, not one per account**, or every second phone is left readable only with the old key — **and clear `ip_block`**, because an HMAC cannot be re-keyed; it reports how many blocks were cleared and that they must be re-applied |
| `python -m admin.cli unblock <address>` | E-8's own break-glass: remove a block from the server when the panel itself is unreachable because of it. Rejects a value that is not a single IP address rather than silently doing nothing |
| `python -m admin.cli bootstrap` | Create the initial administrator accounts if none exist |

One module with subcommands rather than three separate module entry points: `python -m admin.cli --help` then lists every operational command in one place, which is what the handover documentation needs, and settings loading and session construction are written once rather than three times.

The rotation command is not optional. `SECRET_KEY` lives in `.env`, and without rotation the day the client changes it is the day every account loses its second factor.

### Blocklist

**Corrected in v0.12 — the previous paragraph here (`ip_blocklist(id, cidr, reason, created_at, created_by)`, "the middleware ... stores nothing") predates E-8 and described the wrong table under the wrong name.** §2.3 is now the authoritative description of what is actually built: the real table is `ip_block(id, ip_hmac, reason, created_at, created_by, expires_at)`, keyed on a 64-character HMAC of the address rather than a CIDR, and it does store something — a fingerprint, never the address itself. Storing nothing at all derived from the address was considered and rejected here, for a different reason than §2.3's own "browser fingerprinting rejected" callout (see below): an operator needs to be able to *remove* a specific block, which requires recomputing the same fingerprint for the same address again on request, not merely detecting a one-time match — a construction with no stored, comparable value cannot support that.

> **Three things this document calls "fingerprint," and they are not the same thing.** §2.3's "no fingerprint of any kind is stored" and "browser fingerprinting was considered and rejected" both refer to a *browser* fingerprint — a persistent quasi-identifier built from device/header characteristics, rejected there as ineffective against the traffic it would defend against and the highest privacy risk of the options considered. The `ip_hmac` fingerprint described in this section is a different construction entirely — a keyed HMAC of a single known address, not a browser characteristic — kept specifically *because* an operator needs to recompute and compare it, which is exactly the property that made the browser kind unacceptable. Do not read §2.3's rejection of browser fingerprinting as covering this one; it doesn't, and the two are evaluated on different grounds.

`db/blocklist.py` (owner: E, layer: `db/`) is the whole of the write/read surface: `block_ip`, `unblock_ip`, `is_blocked`, `ip_fingerprint`. **Two middlewares read it, and neither writes to it.** `admin/protection.py`'s `ProtectionMiddleware` (owner: E) reads `is_blocked` ahead of every other check on every request under `/admin`, with no exemption — not even for an authenticated staff session, because a block is another administrator's deliberate act. `api/app.py`'s `blocklist` middleware (added v1.3, owner: B) does the same for every request under `/api/v1/`, ahead of routing so that a dead path is refused identically to a live one (§9.2). Until v1.3 there was no second reader at all: a block made on the screen below held on the panel and did nothing where the public traffic actually arrives, which is the one thing a blocklist is for.

Three differences between the two, each deliberate and each explained where it is implemented:

| | `admin/protection.py` | `api/app.py` |
| --- | --- | --- |
| Refusal body | `PlainTextResponse("Refused.")` — a browser surface | §9.2's `BLOCKED` envelope, built by `api/errors.py` like every other error. A JSON client that got plain text back for one error out of twelve would have to special-case it, and C and D would each have to do so separately |
| Header check | `looks_automated` applies | Does **not** apply — scripting a public JSON API is a legitimate use, and §6.3's CSV export exists to be fetched by a tool |
| Rate limit | `PROTECTION_MAX_REQUESTS_PER_MINUTE`, refusals counted, `/admin/login` and `/admin/verify` exempt | §6.5's fixed hourly limits, refusals not counted, nothing exempt |

Both key on `db.detection.client_ip`, so a caller with no usable address is skipped by both rather than given a stand-in key — and both inherit the deployment hazards §6.5 records.

The admin screen, `/admin/ip-block/list` (`admin.blocklist_views.IpBlockAdmin`), is where a block is actually created or removed by a person: `column_list` shows `reason`, `created_by`, `created_at` and `expires_at` — never `ip_hmac` — and is restricted to `role = admin`, the same floor `StaffAdmin` sets for account management. A manual block is entered through its own form at `/admin/ip-block/block` (address, reason, an optional duration in minutes); removal is an audited `unblock` action, not sqladmin's generic delete. Both write their own `audit_log` entry, built from `reason`/`created_by`/`created_at`/`expires_at` only — never from `ip_hmac` — since `db/blocklist.py` itself writes none (see §2.3). `ip_hmac` is additionally named in `write_audit`'s `REDACTED_FIELDS` (§5.5), so a future caller that serialises a whole `IpBlock` row through `row_to_dict` still cannot land the fingerprint in `audit_log` — which since v1.15 only administrators can read, but which is also exportable and append-only, so the redaction does not depend on that.

**Where an operator gets an address to type into that form.** Nowhere in this system — and that is worth stating, because the form otherwise reads as more capable than the panel is. Nothing here ever shows staff a caller's address: §2.3 forbids storing one, and the panel deliberately does not log one either. The address has to come from outside: the reverse proxy's or hosting platform's own access log, an alert from the host, or a report from someone who can see the traffic. The form's purpose is to *apply* an address an operator already has in hand from one of those, during an incident, with no CDN or upstream firewall available to do it for them. Anyone planning to rely on this screen should confirm the deployment keeps a proxy access log at all, before an incident rather than during one.

> **Resolved — `write_audit` lives in `db/repository.py`, and it always did on B's branch.** v0.13 recorded this as an open item awaiting a decision from B, on the evidence available to E at the time: `write_audit` was in `admin/audit.py`, `api/` may not import `admin/` (CLAUDE.md's layering rule, AST-pinned by `tests/db/test_blocklist.py`), and so an API-side automatic block could not audit itself. **The survey of `origin/database` closed it.** B's branch already carries `write_audit` and `_json_safe` in `db/repository.py` — exactly where §5.5 has placed them since v0.3 — so there is no decision left to take, only a duplicate to remove. It was never two designs; it was one design and two branches.
>
> **What that means for the integration, concretely:**
>
> - **`db/repository.py`'s `write_audit` is the one that survives.** `admin/audit.py` becomes a re-export of it, or is deleted. E's copy gives way.
> - **B's `_json_safe` recurses into nested dicts and lists and redacts at every level; E's `_scrub` only handles top-level keys.** That is not a style difference. A `mfa_secret_enc` or a `password_hash` nested one level down inside a `before_json` payload passes straight through `_scrub` and lands in `audit_log`, which every staff member can read — the exact privilege-escalation path §5.5's field blocklist exists to close. **B's is the one that survives**, on this ground alone.
> - **E's `_encode` handles `date` more carefully than B's.** That one branch folds into B's function; nothing else of E's does.
> - Once the re-export is in place, "the caller audits" becomes executable from `api/`, and an API-side automatic block appears in `/admin/audit` like every other write.
>
> **Resolved in v1.3 — the three names moved to `db/detection.py`.** This paragraph recorded the split as genuinely unresolved and B's to decide: `admin.detection.looks_automated`, `admin.detection.RequestRate` and `admin.protection._client_ip` were all in `admin/` and were all things the public-traffic middleware needed. **The recommendation recorded here is the one that was implemented.** They are now `db.detection.looks_automated`, `db.detection.RequestRate` and `db.detection.client_ip`; `admin/detection.py` is a re-export, and `admin/protection.py` keeps `_client_ip` as an alias for the same object rather than a second copy — `tests/db/test_detection_shared.py` asserts that identity with `is`, and AST-pins that `db/detection.py` imports nothing from `admin/`, the same pin `tests/db/test_blocklist.py` applies to the blocklist. The deciding argument was the one written here: two copies of a detection rule drift, and the copy that stops matching is the one nobody notices.
>
> One part of the rationale above was wrong and is corrected rather than repeated: **`db/detection.py` is not standard-library-only at runtime.** It imports `db.blocklist` for address normalisation, which pulls in SQLAlchemy and `cryptography`. Starlette is imported under `TYPE_CHECKING` only, so the module is still usable and testable without a web framework, but the stdlib-only property the recommendation leaned on does not survive the move in full. What does survive — and what the layering rule actually required — is that nothing in it needs `admin/`.
>
> **`looks_automated` is applied to `/admin` and deliberately not to `/api/v1/`.** The other two are used by both. This is a decision, not an oversight: `/admin` is a browser-only surface, so a caller there that is plainly a script is refused, whereas scripting a public JSON API is a legitimate way to use it and §6.3's CSV export exists precisely to be fetched by a tool. Refusing `curl` at `/api/v1/` would refuse a use this contract invites. The API applies the blocklist and §6.5's rate limit and nothing else.
>
> **`RequestRate` is still per-process**, so under more than one worker the effective limit is multiplied by the worker count — true of both layers now, since both count with the same class. The API's own rate limiting (§6.5) is no longer a separate problem: `api/rate_limit.py` wraps this counter rather than being a third implementation of one, keeping only the policy §6.5 needs (a per-call limit, and `Retry-After` derived from the oldest hit still inside the sliding window). It does **not** count refused requests, which is where it deliberately differs from `admin/protection.py` — see §6.5.

**The one case this whole design is built around not causing:** an administrator blocks the address they are sitting behind, and the block itself now stands between them and every page that would let them undo it — including the login page, because the blocklist check has no exemption. `python -m admin.cli unblock <address>` (above) is the only way back short of editing the database by hand, and is the reason that command exists at all.

`ProtectionMiddleware` also carries a stateless header check and a per-address rate limit (`PROTECTION_MAX_REQUESTS_PER_MINUTE`), both configurable and both able to be turned off in one place: `PROTECTION_ENABLED=false` disables the blocklist, the header check and the rate limit together, with no finer-grained switch — the documented escape hatch for a false-positive lockout that is not a blocklist entry. **It needs no code change, but it does need a process restart**: settings are read once, by `load_settings()` at start-up. See `docs/architecture.md` §9.1.1 for the operational detail, the `PROTECTION_TRUSTED_PROXY` warning, the four recovery paths, and this design's explicit limits. **That section lands with PR #9 and is not on `main` yet** — until #9 merges, `architecture.md` stops at §9.1 Deployment and the reference resolves only on the `admin_panel` branch. Stated rather than left to be discovered, because a cross-reference that dangles for a known reason is a known state and one that dangles silently reads as an error in this document.

**`/admin/login` and `/admin/verify` are exempt from the rate limit** — and from that check only; the blocklist and the header check still apply to both. Behind a reverse proxy with `PROTECTION_TRUSTED_PROXY` false (which is the shipped arrangement, since TLS is terminated upstream and trusting `X-Forwarded-For` without a proxy that overwrites it would let any caller forge any address) every caller arrives as the proxy's own address and shares one rate-limit bucket, and refused requests are counted too — so without this exemption one request a second from any unauthenticated caller kept that bucket permanently over the limit and answered 429 to every unauthenticated request in the deployment, the login pages included. The authenticated-staff exemption cannot rescue that, because it needs the session only those two pages mint. Login attempts are still throttled per account by `LOGIN_MAX_FAILURES`/`LOGIN_LOCKOUT_MINUTES`, which is the check that actually defends a credential-stuffing run.

## 8.4 Staff Authentication Interface (owner: E, consumed by B)

The only coupling between the admin authentication system and the API layer.

```python
# admin/auth.py

def require_staff(request: Request) -> str:
    """Return the authenticated staff username.

    Raises StaffAuthRequired when the session is absent or expired, when the
    account is no longer active, or when MFA enrolment is incomplete
    (§8.3). The API layer maps that to UNAUTHORIZED (401).

    The mechanism — sqladmin's AuthenticationBackend over a same-origin
    Starlette session cookie — is E's concern and is not part of this
    contract. B calls this and nothing else.
    """
```

The return value is the staff username and is written directly to `audit_log.actor` (`VARCHAR(128)`).

B calls it in exactly one place: the `X-Dry-Run` branch of `POST /api/v1/calculate`.

> **Terminology.** `submission.token` (§2.3) is the anonymous de-duplication token — a UUID4 that expires after an hour and identifies a draft record, not a person. Staff credentials are **not** a token and are never carried in the request body; they travel as a same-origin session cookie. The admin dry-run page is served from the same origin as the API, so the browser attaches the cookie without any explicit handling.
>
> Should the public front end later be split onto a separate origin, only the public requests are affected (CORS); they carry no cookie, so the dry-run path is unchanged.

---

# 9. Error Contract (owner: B)

Every non-2xx response uses one envelope:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "A single line may not exceed 10,000,000 kg",
    "details": [
      { "field": "entries[0].current[1].qty_kg",
        "issue": "exceeds_max",
        "message": "A single line may not exceed 10,000,000 kg" }
    ]
  }
}
```

**`details` has exactly two shapes, and which one you get is decided by the `code`, never by inspection.**

| Shape | Keys | `code` |
| --- | --- | --- |
| **Field problem** | `field`, `issue`, `message` — all three, always | `VALIDATION_ERROR` |
| **Formula problem** | `expression`, `line`, `column`, `reason` | `FORMULA_ERROR`, and only on an **authenticated dry run** (§9.1) |

Every other code carries `details: []`, except §9.2's `BLOCKED`, which carries `null`. **So: if `code` is `VALIDATION_ERROR`, every entry has `field`, `issue` and `message`; if it is `FORMULA_ERROR`, entries are the formula shape or the array is empty; otherwise the array is empty.** No entry mixes the two shapes and no key is conditionally absent within a shape — a consumer needs no presence checks, only a branch on `code`.

On the field shape's three keys: `field` is §9's bracket path, `issue` is a stable machine-readable slug (Pydantic's error `type` where the failure is Pydantic's, e.g. `value_error`; a name this API chooses otherwise, e.g. `mass_not_conserved`, `bundle_invalid`), and `message` is per-field prose distinct from the envelope's single `message`, which describes the request as a whole. **Branch on `issue`, display `message`, target `field`.**

> **Both halves of this were wrong until v1.5, in opposite directions.** The `message` key was emitted from the first commit and shown in no version of this section, while `tests/fixtures/errors/validation_error.json` has always carried it — so a front end built from the old sample rendered `Request validation failed` against every highlighted row and discarded the only text saying what was wrong with that row. And `api/router.py`'s inline-bundle check emitted a *third* shape, `{field, issue}` with no `message`, putting `FactorBundle.validate()`'s human-readable prose (§4.1) into `issue` — inverting the two keys, so a consumer told to branch on `issue` got a sentence that changes whenever the engine's wording changes, and found no `message` to display. **The code was corrected to the contract rather than the contract widened to the code**, because two shapes a consumer can predict from `code` is a contract, and three shapes it must sniff at runtime is not.

**`field` is a bracket-indexed path into the request body**, exactly as a front end would write it: `entries[0].current[1].qty_kg`. Array positions are `[n]`, object keys are `.key`, and the path starts at the root of the request.

> This needs stating because the two obvious implementations disagree and the disagreement is silent. Pydantic's native `loc` is a tuple that renders as `entries.0.current.1.qty_kg`; a front end building a lookup key from its own render loop writes `entries[0].current[1].qty_kg`. Neither is wrong, but if the API emits one and the client looks up the other, **field-level highlighting simply never binds** — no error, no console warning, the user just sees the generic banner and never learns which row is bad. Both fixture sets on the team had already chosen different formats. The API is responsible for converting Pydantic's `loc` to this form before it goes on the wire.

| HTTP | `code` | Trigger | Front-end response |
| --- | --- | --- | --- |
| 400 | `VALIDATION_ERROR` | Missing field, out of bounds, duplicate destination, malformed dry-run bundle | Highlight the offending field |
| 400 | `UNKNOWN_CODE` | A code in the request does not exist | Re-fetch the taxonomy and prompt a refresh |
| 401 | `UNAUTHORIZED` | `X-Dry-Run: true` without a valid staff session | Redirect to `/admin/login` |
| 403 | `BLOCKED` | The caller's address is on the blocklist (`db.blocklist.is_blocked`, §2.3) | Show the `message` and stop. **Do not retry, and do not offer a retry button** |
| 429 | `RATE_LIMITED` | Rate limit exceeded | Ask the user to retry later; disable the button for 60s |
| 404 | `NOT_FOUND` | No route matches the path | Bug in the caller. Show the generic banner; do not retry |
| 405 | `METHOD_NOT_ALLOWED` | The path exists, the method does not | Bug in the caller. Show the generic banner; do not retry |
| 500 | `FORMULA_ERROR` | A staff-configured formula is invalid | See below |
| 500 | `INTERNAL_ERROR` | Any unhandled server-side failure | Show the generic banner. A retry may succeed; do not retry automatically |
| 503 | `NO_PUBLISHED_FACTOR_SET` | No factor set has been published | Show "calculator under maintenance" |
| 503 | `ENGINE_UNAVAILABLE` | The calculation engine is not installed or failed to load | Show "calculator under maintenance", as for `NO_PUBLISHED_FACTOR_SET` |
| *(other)* | `HTTP_ERROR` | Residual: any other framework-level HTTP failure, carrying that failure's status | Show the generic banner |

`message` is written for end users and may be displayed verbatim. `details` is for developers and form-field targeting.

> **The last five were emitted by `api/errors.py` from the beginning and appeared in no version of this section**, which listed no generic 500 at all. That is the shape of the defect: §7.1 tells C to branch on `body.error.code`, and a code that reaches her from a closed set she was given is one she can handle, while a code that reaches her from nowhere falls into whatever her `default` branch does. All five already emit the correct envelope — this is the table catching up with the code, not a behaviour change.
>
> **Every one of them is a `code` the front end must treat as terminal-and-generic.** None carries actionable `details`, none names a field, and none should be retried automatically. `ENGINE_UNAVAILABLE` is the one worth distinguishing in copy: like `NO_PUBLISHED_FACTOR_SET` it means the calculator cannot run at all right now, rather than that this particular request was bad, and the two deserve the same maintenance message rather than the generic failure banner.
>
> **`HTTP_ERROR` is a name collision, deliberately left standing.** §7.1's `ApiError.code` already uses `HTTP_ERROR` for a *client-side* condition — a response the browser could not parse as JSON. The API now also emits it as the residual server code for a framework-level `HTTPException` that is neither 404 nor 405. The two are not the same event, and they are not distinguishable from `code` alone. They are left sharing a name because **the front end's response to both is identical** — a generic banner, no field targeting, no automatic retry — so the distinction would cost C a branch and buy nothing; `status` separates them if it is ever needed (`0` or an unparsed body on C's side, a real status and a well-formed envelope on the API's). Written down so that a reader who finds the same string in two sections does not conclude one of them is a mistake.
>
> **What is *not* here is also a rule:** the API does not invent codes beyond this table. §4.4's four engine exceptions map onto rows above; anything else the engine raises is an engine bug, not a documented condition, and lands on `INTERNAL_ERROR` deliberately — a code minted at the point of failure is a code no consumer could have branched on.

## 9.1 `FORMULA_ERROR` Has Two Presentations

| Request | Response |
| --- | --- |
| Public | Generic message. The expression is **never** echoed, and no location is given. |
| Authenticated dry run | `details` carries `expression`, `line`, `column` and `reason` from the `FormulaError` (§4.4) |

Withholding the location from the public protects staff-authored configuration from disclosure. Withholding it from the staff member who is at that moment editing the formula would make the editor unusable — they cannot fix what they are not told.

## 9.2 `BLOCKED` (403)

Defined here so that B does not have to invent a code and C and D do not each
handle it differently. Distinct from `RATE_LIMITED` on purpose: `RATE_LIMITED`
means "too fast, try again", and the front end is told to re-enable the button
after 60 seconds. `BLOCKED` means a staff member decided this caller should not
be served, and a retry will never succeed — a front end that treated the two
the same would poll a blocked caller against the API forever.

```json
{
  "error": {
    "code": "BLOCKED",
    "message": "This request was refused. If you believe this is an error, contact the Kai Commitment team.",
    "details": null
  }
}
```

**`details` is always `null`, and `message` never varies.** The refusal must not
say which rule fired, when the block expires, or that a blocklist exists at all
— the same reasoning `admin/protection.py`'s bare `"Refused."` body records: a
caller being refused is not owed the rule it broke, because that is a free
tuning signal for whoever is probing. Staff read the reason and the expiry on
`/admin/ip-block/list` and in `audit_log`.

**The block is checked before anything else**, including request validation, so
a blocked caller cannot use the API's own error messages to probe the
taxonomy — and, being a single indexed lookup on `ip_hmac`, it costs one query.
It applies to every endpoint under `/api/v1/`, `GET` included.

**It must run ahead of routing, which means middleware and not a router
dependency** (`api/app.py`'s `blocklist` middleware). FastAPI solves a router's
dependencies only once a request has matched a route, so a dependency leaves
`/api/v1/does-not-exist` answering `404` while every live path answers `403` —
a working route scanner for a blocked caller, and a difference no amount of
reticence in the body above can hide. Running ahead of routing also keeps the
one-query cost honest: a dependency *and* a middleware would be two lookups.
The response is built directly rather than raised, because an exception raised
in middleware never reaches the handlers registered with
`add_exception_handler` — Starlette's `ExceptionMiddleware` sits inside them,
and the session middleware outside would turn a raised `ApiProblem` into a
`500 INTERNAL_ERROR`.

---

# 10. Mock Data Convention

Located in `tests/fixtures/`. C and D consume these directly before the backend is ready.

**These files are the executable form of the contract.** Backend contract tests assert that real responses match their shape, the fixtures are checked against each other and against the shipped seed data, and the front end develops against them directly. They must be updated whenever the contract changes (see §0).

**One canonical set, in this tree, in the v1.3 shape.** Thirteen files. v1.2 recorded two divergent sets on two unmerged branches and neither of them here; that is now history and the paragraph describing it has been replaced by what is actually on disk.

| File | Content |
| --- | --- |
| `taxonomy.json` | A complete `GET /taxonomy` response: six sectors, ten food categories including `standard_mix`, **fourteen destinations across the three `destination_group` rows `reuse`, `recycle_recovery` and `disposal`** — `prevention` is a destination in the `reuse` group, not a group of its own — the metrics, and the unit presets. **Its codes are `admin/seed.py`'s codes**, not prose invented for the fixture — `code` is the cross-layer identifier (§1.1), and a fixture that renames one produces a front end bound to a code the API will never send |
| `calculate_request.json` | A two-entry `POST /calculate` request (§6.2), mass-conserving per entry, and the request that produces `calculate_response.json` |
| `calculate_response.json` | The corresponding 200 body: `totals` plus two `entries`, dual scenario, with `by_destination` per entry and absent at the totals level |
| `calculate_response_single.json` | A 200 body with no alternative scenario: `alternative` and `net_benefit` null at both levels (§3 rule 4) |
| `stats.json` | A `GET /stats` response with a suppressed `other` bucket in every breakdown, an `unspecified` food-category bucket, and shares that sum to exactly 1 |
| `factors.json` | A `GET /factors` response: constants, five formulas, upstream and downstream rows including a **negative** downstream factor and a generic (`food_category: null`) row, and `source_note` / `data_quality` on every row. **`prevention` is at zero on both sides** — all three downstream rows, and since v1.8 an upstream row for every `(sector, food_category, metric)` that has a general one (open item O-7). `test_prevention_is_a_whole_offset_upstream_as_well_as_down` is what keeps the upstream half complete |
| `errors/*.json` | **Seven files, one per §9 code that has a fixed body**: `validation_error`, `unknown_code`, `unauthorized`, `blocked`, `rate_limited`, `formula_error`, `no_published_factor_set`. `errors/blocked.json` is the only one whose `details` is `null` rather than `[]` (§9.2) |

`errors/` does not carry the four codes v1.4 added to §9 — `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `INTERNAL_ERROR`, `ENGINE_UNAVAILABLE` — and that is deliberate rather than an omission: their bodies are the same three-key envelope with a fixed `message` and an empty `details`, none is reachable from a front-end code path C or D can exercise, and a fixture per framework failure would add four files that pin nothing the envelope check does not already pin. `HTTP_ERROR` has no fixture for the same reason **and** because its status varies. If a code ever gains a body worth reading, it gains a fixture.

## 10.0 What Enforces This Section

A fixture that agrees with nothing is a fixture that drifts. Two test modules hold this set to the contract, and they check different things:

| Module | What it holds | Examples |
| --- | --- | --- |
| `tests/api/test_fixture_consistency.py` | The fixtures against **each other, the arithmetic, and `admin/seed.py`** — no HTTP, no app | Every decimal is a string at the contracted scale; no fixture leaks a primary key (§1.1); the request and the response describe the same calculation; every entry conserves mass to §6.2's 0.010 kg; a destination's factors do not change between scenarios; the response's own arithmetic closes; every line equals its formula applied to `factors.json`; every equivalence is derived from the metric total it names; `taxonomy.json`'s codes **and names** are the shipped seeds; `prevention` never appears in a current scenario; `stats.json`'s shares sum to 1; every §9 code has a fixture; `blocked` is the one `details: null`; `details[].field` uses the bracket form |
| `tests/api/test_api.py` | The fixtures against **real responses from the real app** | `test_contract_fixtures_have_the_same_top_level_shapes` (taxonomy, both calculate responses), `test_factors_fixture_matches_the_published_export`, `test_stats_fixture_shape_holds_against_a_populated_database`, and the per-code error assertions inside the behavioural tests |

Both matter, and neither substitutes for the other. The shape check proves the API can produce the fixture; it cannot prove the fixture's numbers are right, because `_assert_shape` compares JSON types and key sets rather than values — which is exactly how a `stats.json` of three empty arrays and a `calculate_response.json` of empty `metrics` passed for two revisions while giving C and D nothing to build against. The consistency check proves the numbers, and cannot prove the API emits them.

> **`_assert_shape` compares types, not text.** A string is a string. Anything whose correctness lives in the *content* of a string — §3 rule 5's `label` format is the case that has already bitten — is invisible to it and needs an assertion in `test_fixture_consistency.py` or a rule in this document. Preferably both.

> **Settled in v1.17. `tests/fixtures/` now exists, in the v1.2 shape, as one canonical set** — the seven files above plus `errors/`'s seven, and `tests/golden/`'s nine cases beside them. What follows is why there is one set and not two, because the reasoning outlives the situation that produced it.
>
> Between v1.2 and the B integration two sets existed on two unmerged branches, and they disagreed with each other and with this contract: `origin/database` carried twelve files with `factor_source` and Pydantic's dotted `current.0.qty_kg`; `origin/Demo-UI` carried ten without `factor_source` and with the bracket form `current[0].qty_kg`. **A fixture that disagrees with the contract does not fail** — it quietly produces code bound to fields the API will never send, and the two front ends built against the two forms would never have bound to each other. v1.0 §9 ratified the bracket form and extended it to `entries[0].current[1].qty_kg`; B's set was the one that changed, and it landed once, with her integration PR, rather than twice in parallel.
>
> **That is the rule worth keeping: two people writing fixtures from one contract produce two sets that differ wherever the contract is silent, which is exactly where a fixture is load-bearing.** The owner of the section owns the shape. B owns §6, so B owned these; C's `taxonomy.json` supplied the content, being the better one (six sectors with real descriptions, ten NZ-appropriate food categories, nine MfE destinations).
>
> Two of them were worth the argument on their own and still are, so they are requirements and not history. **`errors/blocked.json` is the one error whose `details` is `null` rather than `[]`** (§9.2) — it was absent from every branch, and every other error fixture uses `[]`, so a set that made `[]` universal is precisely how that requirement would have been implemented away. **`taxonomy.json` must contain a `prevention` destination and at least one destination in the `reuse` group**, or the mass-conserving offset and the entire non-waste half of the MfE taxonomy — the client's headline story — cannot be demonstrated at all. `stats.json` must carry a suppressed `other` bucket, since §6.4's copy constraint is what D writes against, and `factors.json` must not be all-empty arrays, or the methodology page renders "No published formulas were returned" in every demo.

## 10.1 Golden Test Suite (owner: A)

Under `tests/golden/`, three files per case, and the directory name says what the case is for:

```
tests/golden/
  test_golden.py                 the runner
  case_03_prevention_whole_offset/
    bundle.json     a fixed factor set          <- shape defined in §10.2
    request.json    a fixed request             <- §3 CalculationRequest, as JSON
    expected.json   the expected full CalculationResult   <- §3, as JSON
```

Every change to the engine must leave all golden cases passing. This suite is the only evidence that the calculator computes correctly, and it is what the team can present at handover. `tests/golden/test_golden.py` discovers `case_*/`, loads the three files, calls `calculate()` and compares.

**The runner lives beside the cases** so that `pytest tests/golden` means what everyone will assume it means. It was `tests/test_golden.py` for one commit, and `pytest tests/golden` then collected zero tests and reported green — which put the guard against a renamed case directory inside a module the obvious command never loaded.

**`request.json` and `expected.json` are §3's domain objects, not §6.2's wire shapes.** Field for field: `sector_code` and `food_category_code` rather than `sector` and `food_category`, `destination_code` rather than `destination`, `source_metric_code` rather than `source_metric`, `by_destination` present and empty at the totals level rather than omitted, and a `total_kg` on `totals.alternative` for which §6.2's single hoisted `totals.total_kg` has no room. A golden case written against §6.2's body would certify `api/engine_adapter.py` as well as the engine, and a hoist or an omitted key there would then read as an engine defect. Decimals are strings (§1.2) rendered as `format(value, "f")` — the same rendering `api/serialization.wire()` performs, so `"0.0000000000"` and `"0"` are different answers in a golden file exactly as they are on the wire.

| Rule | Reason |
| --- | --- |
| A case is **self-contained**: its `bundle.json` is the whole factor set, not a reference to a fixture | The engine is a pure function of two documents (§4.2). A case that reached out to `tests/fixtures/` would change its answer when a fixture was corrected, which is how a suite starts failing for reasons that have nothing to do with the engine |
| **The runner never writes an expected file.** There is no regenerate mode | A runner that can rewrite its own expectations certifies whatever the engine currently does. §10.0's warning applies with more force here than anywhere: a suite certifying the wrong semantics certifies them very convincingly |
| **A failure names the case, the path, the metric and both values** | A golden failure reading only `assert False` wastes the debugging session it exists to shorten |
| **Every case states what it is evidence of**, in `_PROVENANCE` in the runner | A case nobody can state the purpose of is the case that gets deleted the first time it fails |

> **Where a case's numbers come from is the whole question, and only the first case can avoid the circle.** `case_01` and `case_02` are derived from `calculate_response.json` and `calculate_response_single.json` — figures produced on B's line from the published formulas, independently of A's engine, so those two cases are a genuine cross-check between two implementations. Every case after them would otherwise be the engine certifying itself, so cases 03 to 08 are **hand-computed**: each is small enough to check on paper, each is designed so that the failure mode it targets changes the answer by an amount nobody could mistake for rounding, and the arithmetic is written out in the task-7 report.
>
> **`case_03_prevention_whole_offset` is the one that carries open item O-7.** 800 kg moved from `not_harvested` to `prevention` gives `net_benefit.co2e` of **456.000**, the figure v1.8 recomputed independently. `test_case_03_fails_if_the_upstream_destination_dimension_is_removed` reverts `FactorBundle.upstream()` to its pre-v1.8 behaviour and asserts that the case then fails **with 96.000** — not merely that it fails. A case that only proves today's engine agrees with today's expected file is not evidence that a closed defect stays closed.

## 10.2 `bundle.json` Shape (owner: A)

One shape, three consumers: the golden suite above, `FactorBundle.from_json()` (§4.1), and the `dry_run.bundle` field of `POST /calculate` (§6.2.1).

It is a **complete, self-contained snapshot** — the taxonomy as well as the factors. §4.1's `has_destination()`, `has_sector()`, `has_food_category()` and `standard_mix_code()` are unimplementable otherwise, and staff must be able to trial a destination or food category that does not yet exist in the database.

> **No file in `tests/fixtures/` is a bundle, and `factors.json` in particular is not one.** §10's table has always called it a `GET /factors` response and that is exactly what it is: it wraps `version_label` and `is_mock` inside a `factor_set` object, and it carries **none** of the five taxonomy sections. Passing it to `from_json()` raises `BundleFormatError` naming the seven missing keys — the five taxonomy sections plus `version_label` and `is_mock`, which are present but nested — correctly, since a bundle without the taxonomy cannot answer `has_destination()` and would otherwise reject every destination in the request with `UNKNOWN_CODE`. This is not a defect in either file. `db/repository.build_bundle_data` is the projection **both** shapes come from, and §6.3's export is that dictionary with the taxonomy sections dropped and those two keys nested.
>
> **A bundle is composed, not fetched**, and the composition is a re-keying with no arithmetic in it:
>
> ```
> bundle.version_label   <- factors.json  .factor_set.version_label
> bundle.is_mock         <- factors.json  .factor_set.is_mock
> sectors, food_categories, destination_groups, destinations, metrics
>                        <- taxonomy.json (the same five keys)
> constants, formulas, upstream, downstream, equivalences
>                        <- factors.json  (the same five keys)
> ```
>
> `tests/test_bundle.py::canonical_bundle_json` is that composition, and it is what `tests/golden/case_01_*/bundle.json` and `case_02_*/bundle.json` were built with — so the canonical numbers reach the golden suite without a thirteenth fixture being added and without either existing file having to change shape. **A fourteenth file holding a pre-composed bundle was rejected**: it would be a second copy of every factor row, and the copy that stops matching is the one nobody notices.

```json
{
  "version_label": "GOLDEN-case-01",
  "is_mock": true,

  "sectors": [
    { "code": "processing", "name": "Processing / Manufacturing", "sort_order": 2 }
  ],
  "food_categories": [
    { "code": "standard_mix", "name": "Standard mix", "is_standard_mix": true,  "sort_order": 0 },
    { "code": "dairy",        "name": "Dairy",        "is_standard_mix": false, "sort_order": 6 }
  ],
  "destination_groups": [
    { "code": "disposal", "name": "Disposal", "is_waste": true, "sort_order": 3 }
  ],
  "destinations": [
    { "code": "landfill", "name": "Landfill", "group": "disposal", "sort_order": 1 }
  ],
  "metrics": [
    { "code": "co2e", "name": "Greenhouse gas", "unit": "kg CO2e",
      "display_unit": "kg CO2e", "display_precision": 1, "sort_order": 1 }
  ],

  "constants": [
    { "code": "GWP_CH4_100", "value": "28.0000000000", "unit": "", "note": "" }
  ],
  "formulas": [
    { "metric": "co2e", "expression": "qty_kg * (upstream + downstream)", "notes": "" }
  ],
  "upstream": [
    { "sector": "processing", "food_category": "dairy", "destination": null,
      "metric": "co2e", "value_per_kg": "1.9000000000" },
    { "sector": "processing", "food_category": "dairy", "destination": "prevention",
      "metric": "co2e", "value_per_kg": "0.0000000000" }
  ],
  "downstream": [
    { "destination": "landfill", "sector": null, "food_category": "dairy",
      "metric": "co2e", "value_per_kg": "0.9900000000" },
    { "destination": "landfill", "sector": null, "food_category": null,
      "metric": "cost", "value_per_kg": "0.0650000000" },
    { "destination": "landfill", "sector": "primary_production",
      "food_category": null,
      "metric": "cost", "value_per_kg": "0.0400000000" }
  ],
  "equivalences": [
    { "code": "km_driven", "name": "Kilometres driven", "source_metric": "co2e",
      "value_per_unit": "4.1800000000",
      "label_template": "Equivalent to driving {value} km", "sort_order": 1 }
  ]
}
```

| Convention | Reason |
| --- | --- |
| No `id` fields | §1.1 — `code` is the only cross-layer identifier |
| No `active` fields | Anything present in a bundle is active. §4.1 already states `metrics` is "active only"; filtering happens in the repository, and the engine does not re-check. |
| No `unit_presets` | Volume-to-kilogram conversion happens in the front end (§7.3); the engine only ever receives kilograms. |
| Every decimal is a **string** | §1.2. `from_json()` converts with `Decimal()`; `float` is never an intermediate. |
| `source_note` and `data_quality` are **optional and ignored** | They may appear on any `upstream`, `downstream` or `equivalences` row and may be `null`. The engine does not read them — provenance changes no number. **`from_json()` must accept and ignore them, never raise `BundleFormatError`, and `validate()` must not report them.** |

> **Why the provenance columns are optional here but required in §6.3.** v1.1 added `source_note` and `data_quality` to both factor tables and `source_note` to `equivalence` (§2.2). §6.3 is the public factor export and must carry them — that is what they are for. `bundle.json` is a different object with three consumers (§10.1's golden cases, `FactorBundle.from_json()`, and `dry_run.bundle`), none of which computes anything from provenance, so requiring them would mean writing a note on every row of every golden case to say nothing.
>
> Optional-and-ignored rather than forbidden, because the bundles that reach `from_json()` are not all hand-written. §8.2's **Save as regression case** action writes a `bundle.json` straight out of a dry run, and a dry-run bundle is the natural place to paste a `GET /factors` response — which carries both fields. A parser that rejects an unknown key turns that into a `BundleFormatError` on a bundle that is otherwise entirely valid, at the moment a staff member is trying to capture a case worth keeping.
>
> **The database and §6.3 are the authoritative provenance surface, not the bundle.** Provenance may be dropped on a round trip through a dry run; that is acceptable because an inline bundle is never written back (§6.2.1) and a golden case is not a factor source. If provenance ever has to survive a round trip, this convention is the line that changes.

`downstream[].food_category` may be `null`, meaning the row applies to every food category for that destination (§2.2 — this is how per-tonne charges such as the waste levy are expressed). **`null` is a legal key value, not a missing field**, and must survive both serialisation and deserialisation.

`downstream[].sector` (v1.31) may be `null` on identical terms, meaning the row applies to every sector for that destination, and `null` is the *usual* value — the New Zealand set carries it on every row. A **missing** `sector` key is a malformed row, not a `null` one: a bundle whose rows had silently lost it would load as every-sector rows and price every stage of the supply chain the same, computing a plausible, wrong answer instead of raising. The example above shows all three states in one section — a category-specific row, the row naming neither dimension, and a sector-specific row — because §4.1's four-step order is only exercised when more than one of them is present.

`upstream[].destination` may be `null` on exactly the same terms, meaning the row applies to every destination for that `(sector, food_category, metric)` — and here `null` is the *usual* value rather than the exception. The non-null rows are what make `prevention` a real 100% offset (§2.2, open item O-7): a `prevention` row at zero for every general row. `from_json()` must treat a missing `destination` key as a malformed row rather than as `null`, for the same reason §10.2 requires `null` to survive the round trip on the downstream side — a bundle whose generic rows have silently lost their key computes a plausible, wrong answer instead of raising.

## 10.3 The ReFED Comparison Fixture (owner: A)

**Everything above descends from this document.** The golden cases, the fixtures, the engine and the contract were written by the same people from the same source, so they share one ancestor: together they prove the engine does what §3 and §4 say, and they cannot prove that §3 and §4 are right. `tests/benchmark/refed/` is the one check here whose expected answer nobody on this team produced.

It runs one scenario through two implementations — ours, and ReFED's Impact Calculator, the product this calculator is modelled on. Full instructions, provenance and the unit conversions are in **`docs/refed-comparison.md`**; what belongs in the contract is only what constrains other people's code.

| Rule | Why |
| --- | --- |
| **`refed_` is a reserved taxonomy code prefix.** No `sector`, `food_category` or `destination` row that is part of the New Zealand product may take it | Taxonomy rows are global — §2.1's tables carry no `factor_set_id`, and there is no way to give a factor set a private vocabulary. The fixture's 5 sectors, 9 food categories and 12 destinations are therefore rows in the same tables the product uses. The prefix is the whole of what keeps the two vocabularies distinguishable, in the database and in a dropdown |
| **The fixture is loaded as a `draft` and must never be published** | It is United States data. `is_mock` is `true` on it and the placeholder banner is correct while it is selected (§6.2). Publishing it would archive the live New Zealand set — that is what `publish_factor_set` is for and it is not what this fixture is for. It is reached through §6.2.1's `dry_run.factor_set_version`, which persists nothing |
| **No metric rows are added for it** | `metric` is global and the engine iterates every active row (§4.1). An added metric would appear, at zero, in every New Zealand result. This is why ReFED's meals-recovered figure is not represented and cannot be compared |

> **Our two factor tables hold ReFED's shape one-for-one, since v1.31.** `factor_upstream` is keyed `(sector, food_category, destination)` and always matched ReFED's key exactly. `factor_downstream` had no sector column, and ReFED's downstream factors genuinely differ by sector in **82 of their 102** (food type, destination) groups — so the fixture absorbed the difference by making its `food_category` codes carry the stage: one sector row, and 39 categories named `refed_retail_produce`, `refed_farm_dry_goods`.
>
> **That was numerically lossless and structurally wrong, and the cost landed in the product rather than in the fixture.** No two ReFED cells were merged and no number changed — the comparison agreed with ReFED's live calculator to the integer before the rebuild and agrees to ~1e-9 after it. But taxonomy rows are global, so those 39 rows were rows in the product's own tables: the calculator's first step, "which stage of the food supply chain", offered exactly **one** option, a radio button that selected itself; its second listed 39 compound entries like `ReFED Farm / Dry Goods`, which the client read as a stage rather than a food; and §5.4's `by_sector` breakdown was a single bucket at 100%, so that chart carried no information at all. The previous revision of this note said adding a sector column "to serve a test fixture would be the tail wagging the dog" and asked to be re-read the first time a downstream factor really did need to vary by sector. It was right to ask; the answer is that the fixture was never the reason — the reason is that the shape was wrong and three visible defects were downstream of it.
>
> **The fixture is now 5 sectors × 9 food categories.** `refed_farm`, `refed_manufacturing`, `refed_retail`, `refed_foodservice`, `refed_residential`; `refed_breads_bakery` … `refed_standard_mix`.
>
> **Every downstream row states both its sector and its food category; none is left NULL.** ReFED publishes only **39 of the 45** pairs — Farm has Dry Goods, Produce and Standard Mix and nothing else — but a 5 × 9 taxonomy offers all 45, so a NULL-sector row would be found by a Farm / Frozen lookup and would answer it with another sector's number. With every row explicit those six pairs price at zero on every metric: visibly nothing rather than plausibly wrong. This is the one place the fixture deliberately does *not* exercise §4.1's fallback, and `tests/benchmark/refed/` asserts both halves — that no row is NULL, and that all six unpublished pairs return zero.

The tolerance the comparison asserts is **derived, not chosen**: `value_per_kg` is `DECIMAL(20,10)` (§2.2), so each factor is rounded at the tenth decimal place and a line of `qty` kilograms carries at most `1e-10 × qty` of error in the metric total. That is invisible for water and dominates for methane. **If that tolerance ever has to be widened, the storage precision has changed and this document is what should have changed first.**
