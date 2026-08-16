"""``/admin/deployment`` — driven with headers whose values nothing else can produce.

**What this file refuses to do.** A test that asserts the page renders a
heading called "X-Forwarded-For" asserts that a template contains a
placeholder. It does not assert that the value beside it is the one the
request carried, and that difference is this repository's entire defect list:
a statistics headline that stayed English while 301 tests passed, a corner
probe that passed against a clipped triangle, a health check that reported
healthy over a socket the server had not bound. So every assertion below sends
a header whose value is chosen so that nothing else in the system can put it on
the page, and reads it back out of the rendered response.

``203.0.113.x`` is RFC 5737 TEST-NET-3, and ``198.51.100.x`` is TEST-NET-2.
Neither is routable, neither is an address a test client or a container network
can be assigned, and neither appears anywhere else in this repository outside
the forwarded-header tests. If one of them is on the page, it got there from the
header this test sent and from nowhere else.

**The address is checked through ``client_ip``'s own reader, not beside it.**
The row that matters most is "the address the system decided on", because that
is the value ``db/detection.py::client_ip`` hands to the blocklist and to the
rate limit. Driving the view with ``PROTECTION_TRUSTED_PROXY`` in both states
and asserting the decided address *changes* is what makes it a test of the
decision rather than of a header echo.
"""

import dataclasses
import re

import pytest
from sqlalchemy import func, select

from admin.config import Settings
from admin.deployment_view import (
    FROM_CONNECTION,
    FROM_FORWARDED,
    Observation,
    _split_chain,
    assess,
)
from admin.models import AuditLog

#: Deliberately NOT a file-level `pytestmark`. Half of this file drives real
#: HTTP against a real session and needs both marks; the other half calls
#: `assess` directly and needs neither - it is a pure function over a
#: dataclass and a settings object, which is what lets combinations this
#: repository cannot actually deploy (TLS in front, trust on, protection off)
#: be driven at all. A file-level `db` mark would exclude those from
#: `-m "not db"`, and a file-level `asyncio` mark makes pytest-asyncio warn
#: once per synchronous test. So the two marks go on the tests that need them.
_HTTP = (pytest.mark.db, pytest.mark.asyncio)


def http_test(fn):
    """`@pytest.mark.db` and `@pytest.mark.asyncio`, applied together."""
    for mark in _HTTP:
        fn = mark(fn)
    return fn

#: RFC 5737. The edge's idea of the visitor, and a forged claim.
EDGE_CLIENT = "203.0.113.9"
MIDDLE_HOP = "203.0.113.44"
FORGED_CLIENT = "198.51.100.7"
#: The stack's own nginx, as the panel sees it on the container network. What
#: `X-Real-IP` carries, and - with the trust flag off - the whole of what
#: `X-Forwarded-For` carries, for every visitor alike.
NGINX_PEER = "172.18.0.5"


def _flat(html: str) -> str:
    """Whitespace-collapsed markup.

    The page is wrapped prose inside table cells, so a value and the words
    around it are split across lines about as often as not. Every assertion
    here that spans more than one token runs against this.
    """
    return re.sub(r"\s+", " ", html)


# --- the part that answers the operator's question --------------------------


@http_test
async def test_the_page_reports_the_forwarded_chain_this_request_carried(
    admin_client,
):
    """Two entries in, two entries out, in order, as separate items.

    The order is load-bearing and is the whole of how an operator reads this:
    the left-most entry is what their edge reported the visitor to be, and a
    page that showed the chain reversed, or joined into one string, would tell
    them the opposite of what happened.
    """
    response = await admin_client.get(
        "/admin/deployment",
        headers={"X-Forwarded-For": f"{EDGE_CLIENT}, {MIDDLE_HOP}"},
    )

    assert response.status_code == 200
    body = _flat(response.text)

    assert EDGE_CLIENT in body, "the address the edge reported is not on the page"
    assert MIDDLE_HOP in body, "the second hop is not on the page"
    assert body.index(EDGE_CLIENT) < body.index(MIDDLE_HOP), (
        "the chain is rendered in the wrong order; the left-most entry is the "
        "visitor the front-most proxy saw and must be first"
    )


@http_test
async def test_a_different_chain_produces_a_different_page(admin_client):
    """The mutation this file exists to survive.

    A template that hard-coded an example address, or a view that rendered a
    constant, would pass the test above. This one sends a second, different
    address and asserts the first one is gone - which no page that is not
    reading the actual request can do.
    """
    first = await admin_client.get(
        "/admin/deployment", headers={"X-Forwarded-For": EDGE_CLIENT}
    )
    second = await admin_client.get(
        "/admin/deployment", headers={"X-Forwarded-For": FORGED_CLIENT}
    )

    assert EDGE_CLIENT in first.text and FORGED_CLIENT not in first.text
    assert FORGED_CLIENT in second.text and EDGE_CLIENT not in second.text


@http_test
async def test_the_page_reports_the_forwarded_scheme_this_request_carried(
    admin_client,
):
    """`https` on the page when the header said `https`, and the page says the
    cookie is not Secure when it is not.

    The test fixture's Settings leave SESSION_HTTPS_ONLY at its default; what
    matters here is that the *header* reaches the page, so the assertion is on
    the value in the X-Forwarded-Proto row.
    """
    secure = await admin_client.get(
        "/admin/deployment", headers={"X-Forwarded-Proto": "https"}
    )
    plain = await admin_client.get(
        "/admin/deployment", headers={"X-Forwarded-Proto": "http"}
    )

    assert secure.status_code == plain.status_code == 200
    assert "https" in _row(secure.text, "X-Forwarded-Proto")
    # The other direction, and it is the half that matters. `https` also
    # occurs in the prose further down the page, so an unbounded
    # `X-Forwarded-Proto.*?https` matched a row hard-coded to `http` and let
    # that mutant live - measured, not hypothesised. Both requests are read
    # out of the row itself, and the plain one must not claim TLS.
    assert "https" not in _row(plain.text, "X-Forwarded-Proto"), (
        "the scheme row said https for a request that carried http"
    )
    assert "http" in _row(plain.text, "X-Forwarded-Proto")


@http_test
async def test_an_absent_header_is_named_as_absent_rather_than_blank(admin_client):
    """A blank cell reads as "I did not look"; "not present" is a finding.

    The httpx transport sends neither forwarded header, so this is the
    direct-to-panel case an operator hits when they open :18001 instead of
    going through the proxy.
    """
    response = await admin_client.get("/admin/deployment")

    body = _flat(response.text)
    assert body.count("not present") >= 2, (
        "an absent forwarded header should be named, not left as an empty cell"
    )
    # No apostrophe in the pattern: Jinja autoescapes `'` to `&#39;`, so
    # asserting on "the stack's nginx" as written in the source matches
    # nothing in the rendered page and passes an absence assertion for the
    # wrong reason. Anchored on a phrase that survives escaping instead.
    assert "did not come through the stack" in body, (
        "a request that bypassed the proxy should say so"
    )
    assert "&#39;" in body, (
        "no apostrophe was escaped anywhere on the page, so the note above is "
        "not actually guarding against the escaping trap it describes"
    )


# --- the address the system actually decided on -----------------------------


@http_test
async def test_trusting_the_proxy_changes_which_address_is_in_force(
    admin_client, admin_app, monkeypatch
):
    """The row that matters, driven in both directions.

    With PROTECTION_TRUSTED_PROXY false, `client_ip` ignores X-Forwarded-For
    and the address in force is the connection - so the forged left-most entry
    must NOT be reported as the decided address. With it true, it must be. A
    test that drove only one direction would pass against a page that always
    printed the same thing.

    Both `Settings` and `Runtime` are frozen dataclasses, so the flag is
    flipped with `dataclasses.replace` and the whole Runtime is swapped on the
    mounted application's state - which is the object `get_runtime` reads and
    the same one `create_app` assigns.
    """
    state = _admin_state(admin_app)
    original_runtime = state.runtime

    async def _decided(trusted: bool) -> str:
        patched = dataclasses.replace(
            original_runtime.settings, protection_trusted_proxy=trusted
        )
        monkeypatch.setattr(
            state, "runtime", dataclasses.replace(original_runtime, settings=patched)
        )
        response = await admin_client.get(
            "/admin/deployment",
            headers={"X-Forwarded-For": FORGED_CLIENT},
        )
        assert response.status_code == 200
        return _decided_row(response.text)

    untrusting = await _decided(False)
    assert FORGED_CLIENT not in untrusting, (
        "with PROTECTION_TRUSTED_PROXY false the page reported a header-supplied "
        "address as the one in force; client_ip does not read it and the page "
        "must not claim it does"
    )
    assert FROM_CONNECTION in untrusting

    trusting = await _decided(True)
    assert FORGED_CLIENT in trusting, (
        "with PROTECTION_TRUSTED_PROXY true the address in force is the left-most "
        "forwarded entry and the page did not report it"
    )
    assert FROM_FORWARDED in trusting


def _admin_state(app):
    """The `.state` of sqladmin's own mounted application.

    That is where `create_app` attaches the Runtime and where `get_runtime`
    reads it from - not the outer FastAPI's state, which a view never sees
    (admin/runtime.py).
    """
    for route in app.routes:
        state = getattr(getattr(route, "app", None), "state", None)
        if state is not None and getattr(state, "runtime", None) is not None:
            return state
    raise AssertionError("no Runtime is attached to any mounted application")


def _row(html: str, label: str) -> str:
    """One table row of the evidence table, sliced from its heading to the
    next `</tr>`.

    Bounded on purpose. Every value this page reports also appears somewhere
    in the prose around it - `https` in the cookie finding, an address in the
    chain above the row that decides on it - so an assertion that searches the
    whole document passes for a page rendering a constant.
    """
    flat = _flat(html)
    start = flat.index(label)
    end = flat.index("</tr>", start)
    return flat[start:end]


def _decided_row(html: str) -> str:
    """The one table row that reports the address in force.

    Sliced out rather than searched for across the whole page, because the
    forged address legitimately appears in the X-Forwarded-For row above it in
    both configurations - an assertion against the whole body would pass for
    the wrong reason in exactly the direction that matters.
    """
    return _row(html, "The address the system decided on")


# --- displaying is not storing ----------------------------------------------


@http_test
async def test_loading_the_page_writes_no_audit_entry(admin_client, _committed_session):
    """§2.3, checked rather than assumed.

    The page renders an address into a response. The rule forbids *storing*
    one, and the only table on this panel that a read could plausibly reach is
    `audit_log` - every mutating view writes one, and a diagnostics page that
    quietly did the same would put a visitor's address in a table staff can
    export as CSV.
    """
    # SCOPED TO ROWS THIS REQUEST COULD HAVE WRITTEN, and it has to be. The
    # first version scanned the whole table for the address, which fails
    # against any row some *other* test left behind carrying the same string -
    # and one did, within an hour of the file being written. A test that goes
    # red for something that happened before it started is a test people learn
    # to re-run rather than read.
    high_water = _committed_session.scalar(select(func.max(AuditLog.id))) or 0
    before = _committed_session.scalar(select(func.count()).select_from(AuditLog))

    response = await admin_client.get(
        "/admin/deployment", headers={"X-Forwarded-For": EDGE_CLIENT}
    )
    assert response.status_code == 200

    # A different connection wrote (or did not write); see `_resync` in
    # tests/admin/conftest.py for why the transaction has to be closed out
    # before this session can see anything the request committed.
    _committed_session.commit()
    _committed_session.expire_all()
    after = _committed_session.scalar(select(func.count()).select_from(AuditLog))

    new_rows = _committed_session.scalars(
        select(AuditLog).where(AuditLog.id > high_water)
    ).all()

    assert after == before and not new_rows, (
        f"{len(new_rows)} audit_log row(s) appeared while reading a page that "
        "only reads headers: "
        + " || ".join(f"{r.action} {r.table_name} {r.after_json!r}" for r in new_rows)
    )


@http_test
async def test_the_page_is_still_served_when_protection_is_disabled(
    admin_client, admin_app, monkeypatch
):
    """The decision, driven over HTTP rather than asserted in prose.

    `PROTECTION_ENABLED=false` turns off the blocklist, the header check and
    the rate limit together. Hiding this page then would remove the diagnosis
    exactly when the deployment has least protection - and the two other
    settings it reports are not governed by that switch at all. So the route
    stays open and leads with a finding naming the state.
    """
    state = _admin_state(admin_app)
    monkeypatch.setattr(
        state,
        "runtime",
        dataclasses.replace(
            state.runtime,
            settings=dataclasses.replace(
                state.runtime.settings, protection_enabled=False
            ),
        ),
    )

    response = await admin_client.get(
        "/admin/deployment", headers={"X-Forwarded-For": EDGE_CLIENT}
    )

    assert response.status_code == 200, (
        "the page became unreachable with protection off, which is when an "
        "operator most needs to be told so"
    )
    body = _flat(response.text)
    assert "PROTECTION_ENABLED is false" in body
    # And the reading is still taken - the header this request carried is
    # still on the page, so the finding did not replace the diagnosis.
    assert EDGE_CLIENT in body


@http_test
async def test_the_page_heading_is_translated_inside_its_own_element(admin_client):
    """The menu entry and the heading name the same page, so they must not be
    in two different languages.

    sqladmin's layout renders the `title` a view passes as the page heading
    **verbatim** - it applies no `_()` of its own - so a literal there puts an
    English heading directly above a Chinese menu entry. Asserted inside
    `.page-title` rather than "the page contains Chinese", which would pass
    against a page with one translated word on it (tests/admin/
    test_i18n_pages.py's rule).
    """
    from admin import i18n

    expected = i18n.catalogue("zh").strings["Deployment"]
    response = await admin_client.get("/admin/deployment", params={"lang": "zh"})

    assert response.status_code == 200
    heading = re.search(
        r'class="[^"]*page-title[^"]*"[^>]*>\s*([^<]+)', response.text
    )
    assert heading, "no .page-title element on the page"
    assert heading.group(1).strip() == expected, (
        f"the heading rendered {heading.group(1).strip()!r}, not {expected!r}"
    )

    # The English half, without which the above would pass against a page
    # that always renders Chinese.
    english = await admin_client.get("/admin/deployment")
    heading_en = re.search(
        r'class="[^"]*page-title[^"]*"[^>]*>\s*([^<]+)', english.text
    )
    assert heading_en and heading_en.group(1).strip() == "Deployment"


# --- the role floor ---------------------------------------------------------


@http_test
async def test_a_staff_member_is_refused(staff_client):
    """Also covered by the role matrix; asserted here too because that file's
    completeness check is what would notice a *missing* entry, and this is
    what notices the guard itself being removed."""
    response = await staff_client.get("/admin/deployment", follow_redirects=False)
    assert response.status_code == 403


@http_test
async def test_the_sidebar_does_not_offer_it_to_a_staff_member(staff_client):
    """`_require_admin` refuses the URL; the sidebar entry is what stops the
    panel telling somebody they have a capability they do not have.

    **Which of the two predicates hides it, measured rather than assumed.**
    sqladmin's `_macros.html` renders a menu item only
    `{% if menu.is_visible(request) and menu.is_accessible(request) %}`, so
    either one returning False hides the entry and neither is individually
    load-bearing here: mutating `is_visible` alone to True survives this
    test, because `is_accessible` still refuses. Dropping `AdministratorOnly`
    from the view's bases - the realistic mistake, and the one where both go
    at once - fails this test and the 403 above together. That mutation was
    applied and both failed.

    Anchored on the full href attribute including its closing quote:
    `menu.url(request)` renders an absolute URL, so a bare path substring
    matches nothing and passes an absence assertion for the wrong reason -
    which is exactly how a mutant lived on this branch before.
    """
    staff_index = await staff_client.get("/admin/")
    assert staff_index.status_code == 200
    assert not re.search(r'href="[^"]*/admin/deployment"', staff_index.text)


@http_test
async def test_the_sidebar_offers_it_to_an_administrator(admin_client):
    """The other half, without which the absence above proves nothing."""
    index = await admin_client.get("/admin/")
    assert index.status_code == 200
    assert re.search(r'href="[^"]*/admin/deployment"', index.text), (
        "the sidebar withheld the deployment page from an administrator"
    )


# --- the coherence rules, driven without HTTP -------------------------------


def _settings(**overrides) -> Settings:
    base = dict(
        secret_key="k" * 32,
        database_url="sqlite://",
        session_max_age_minutes=480,
        login_max_failures=5,
        login_lockout_minutes=15,
        session_https_only=True,
        protection_enabled=True,
        protection_trusted_proxy=False,
    )
    base.update(overrides)
    return Settings(**base)


def _observation(**overrides) -> Observation:
    base = dict(
        forwarded_for=f"{EDGE_CLIENT}, 172.18.0.5",
        chain=(EDGE_CLIENT, "172.18.0.5"),
        forwarded_proto="https",
        real_ip="172.18.0.5",
        peer="172.18.0.6",
        scheme="http",
        decided_address="172.18.0.6",
        decided_from=FROM_CONNECTION,
    )
    base.update(overrides)
    return Observation(**base)


def _titles(findings) -> str:
    return " || ".join(f.title for f in findings)


def _details(findings) -> str:
    return " || ".join(f.detail for f in findings)


def test_a_forwarded_chain_that_the_application_ignores_is_a_warning():
    """The incoherence that looks configured and does nothing.

    nginx believed an edge and forwarded the visitor's address;
    PROTECTION_TRUSTED_PROXY is false, so the application throws it away. The
    page must name the consequence - one rate-limit bucket, one block that
    denies everyone - not print two values and leave the reader to reason.
    """
    findings = assess(_observation(), _settings(protection_trusted_proxy=False))

    warnings = [f for f in findings if f.level == "warn"]
    assert warnings, f"no warning raised; findings were: {_titles(findings)}"
    joined = _details(warnings)
    assert "one shared rate-limit bucket" in joined
    assert "denies everyone" in joined


def test_the_shipped_default_behind_our_own_nginx_is_a_warning():
    """The state `docker compose up` actually produces, which read as an
    all-clear until 2026-08-16.

    With `KAICALC_TRUST_FORWARDED_HEADERS` off - also the default - nginx
    OVERWRITES `X-Forwarded-For` with the peer it saw, so exactly ONE entry
    arrives. That falls past the two-entry warning, and the page's final `ok`
    then said "the address in force is the connection this panel accepted" and
    stopped. True, and not the finding: the connection is the nginx container,
    the same value for every visitor, so the rate limit is one bucket and one
    `ip_block` row denies everyone. Measured on the running stack - a container
    exhausted the panel's minute and the next request from a different machine
    was refused 429 on its first try.

    `real_ip` is what makes this branch reachable rather than the chain: nginx
    sets `X-Real-IP` in both branches of the trust flag, so it is the evidence
    that the stack's proxy is in the path at all.
    """
    findings = assess(
        _observation(
            forwarded_for=NGINX_PEER,
            chain=(NGINX_PEER,),
            real_ip=NGINX_PEER,
            decided_address=NGINX_PEER,
        ),
        _settings(protection_trusted_proxy=False),
    )

    warnings = [f for f in findings if f.level == "warn"]
    assert warnings, f"no warning raised; findings were: {_titles(findings)}"
    joined = _details(warnings)
    assert "one shared rate-limit bucket" in joined, joined
    assert "denies everyone" in joined, joined
    # Named as a trade, with the condition that would settle it. A warning
    # that only says "this is wrong" invites the unsafe repair, which is
    # flipping the flag while 18000 and 18001 are still published.
    assert "18000 and 18001" in joined, joined


def test_the_shipped_default_with_no_proxy_in_the_path_is_not_a_warning():
    """What stops the rule above from being "warn whenever trust is off".

    A request straight to the panel's published port carries neither header,
    and there `PROTECTION_TRUSTED_PROXY` false is simply correct: the
    connection IS the caller. Without this, a page that warned unconditionally
    would pass the test above.
    """
    findings = assess(
        _observation(forwarded_for=None, chain=(), real_ip=None),
        _settings(protection_trusted_proxy=False),
    )

    address_findings = [f for f in findings if "address in force" in f.title]
    assert address_findings, _titles(findings)
    assert all(f.level != "warn" for f in address_findings), _titles(address_findings)
    assert "one shared rate-limit bucket" not in _details(findings)


def test_trusting_with_no_forwarded_header_is_a_warning():
    """The other direction: the application will believe a header that is not
    arriving, so anything that can reach it directly can supply one."""
    findings = assess(
        _observation(forwarded_for=None, chain=(), real_ip=None),
        _settings(protection_trusted_proxy=True),
    )

    warnings = [f for f in findings if f.level == "warn"]
    assert warnings, f"no warning raised; findings were: {_titles(findings)}"
    assert "any address in X-Forwarded-For" in _details(warnings)


def test_a_single_entry_does_not_claim_to_know_the_trust_flag():
    """The honesty requirement. One entry is produced identically by both
    branches, and a page that guessed would be asserting something it cannot
    see."""
    findings = assess(
        _observation(forwarded_for=EDGE_CLIENT, chain=(EDGE_CLIENT,)),
        _settings(),
    )

    joined = _details(findings)
    assert "identical from here" in joined
    assert "will not guess" in joined

    # The headings as well as the prose. A finding whose detail says "these
    # are indistinguishable" under a heading that says the flag is off is a
    # page that lies to anybody who skims it, and asserting on `detail` alone
    # let exactly that mutant live.
    titles = _titles(findings)
    assert "KAICALC_TRUST_FORWARDED_HEADERS is on" not in titles, titles
    assert "KAICALC_TRUST_FORWARDED_HEADERS is off" not in titles, titles


def test_two_entries_do_not_claim_the_edge_belongs_to_the_operator():
    """A chain proves the flag is on. It does not prove who sent it, and the
    page has to say the second part or it reads as an all-clear."""
    findings = assess(_observation(), _settings(protection_trusted_proxy=True))

    joined = _details(findings)
    assert "cannot tell you whether that proxy is yours" in joined


def test_tls_in_front_with_an_insecure_cookie_is_a_warning():
    """X-Forwarded-Proto says https and the cookie carrying admin access is
    issued without Secure - the one scheme combination that is provably
    wrong from a single request."""
    findings = assess(
        _observation(forwarded_proto="https"),
        _settings(session_https_only=False, protection_trusted_proxy=True),
    )

    titles = _titles(findings)
    assert "TLS terminates in front and the staff session cookie is not Secure" in titles
    assert any(
        f.level == "warn" and "Secure" in f.title for f in findings
    )


def test_plain_http_with_an_insecure_cookie_is_not_reported_as_a_warning():
    """The counterpart, and what stops the rule above from being "always
    warn". A local stack over http with SESSION_HTTPS_ONLY false is the
    shipped arrangement, and it must not be dressed up as a finding."""
    findings = assess(
        _observation(forwarded_proto="http"),
        _settings(session_https_only=False, protection_trusted_proxy=True),
    )

    scheme_findings = [f for f in findings if "Secure" in f.title]
    assert scheme_findings, f"nothing said about the cookie: {_titles(findings)}"
    assert all(f.level != "warn" for f in scheme_findings)
    assert "turn the trust flag on first" in _details(scheme_findings), (
        "the page should say why an https edge may be invisible here"
    )


def test_protection_disabled_is_named_and_the_page_still_reports():
    """`PROTECTION_ENABLED=false` is not a reason to hide this page - it is
    the state most worth telling an operator about - and it must not silence
    the rest of the reading."""
    findings = assess(_observation(), _settings(protection_enabled=False))

    assert any(
        f.level == "warn" and "PROTECTION_ENABLED is false" in f.title
        for f in findings
    ), _titles(findings)
    # The reading is still taken: the address and cookie findings are present.
    assert any("address in force" in f.title for f in findings), _titles(findings)
    assert any("Secure" in f.title for f in findings), _titles(findings)


def test_a_non_public_address_in_force_is_noted():
    findings = assess(
        _observation(decided_address="172.18.0.6"), _settings()
    )
    assert any("is not a public address" in f.title for f in findings), _titles(findings)


def test_a_public_address_in_force_is_not_noted():
    """Without this, the note above would pass against a page that always
    printed it.

    `9.9.9.9` and not one of the TEST-NET addresses this file uses elsewhere:
    Python's `ipaddress.is_private` is True for 192.0.2.0/24, 198.51.100.0/24
    and 203.0.113.0/24 as well as for RFC1918, which is correct for the page
    (none of them is a visitor) and would make this assertion untestable with
    a documentation address.
    """
    findings = assess(
        _observation(decided_address="9.9.9.9", decided_from=FROM_FORWARDED),
        _settings(protection_trusted_proxy=True),
    )
    assert not any("is not a public address" in f.title for f in findings)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ()),
        ("", ()),
        (" , , ", ()),
        ("203.0.113.9", ("203.0.113.9",)),
        ("203.0.113.9, 172.18.0.5", ("203.0.113.9", "172.18.0.5")),
        ("  203.0.113.9  ,172.18.0.5  ", ("203.0.113.9", "172.18.0.5")),
    ],
)
def test_the_chain_is_split_the_same_way_client_ip_splits_it(raw, expected):
    """`client_ip` takes `forwarded.split(",")[0]` and normalises it, so the
    page's first entry has to be the same token client_ip read. An empty
    element rendered as a hop would describe a proxy that is not there."""
    assert _split_chain(raw) == expected
