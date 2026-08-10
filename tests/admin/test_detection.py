"""Behavioural signals. No fingerprinting, no storage. Contract §2.3."""

import pytest

from admin.detection import RequestRate, looks_automated

BROWSER = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-NZ,en;q=0.9",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-dest": "document",
}


def test_a_real_browser_navigation_is_not_flagged():
    """The counter-case, and the one that matters most: a check that flags
    real staff is worse than no check, because it will be turned off."""
    assert looks_automated(BROWSER) is None


def test_a_request_with_no_user_agent_is_flagged():
    """Every browser sends one. Nothing that omits it is a person."""
    headers = {k: v for k, v in BROWSER.items() if k != "user-agent"}

    assert looks_automated(headers) is not None


@pytest.mark.parametrize("agent", [
    "curl/8.4.0",
    "python-requests/2.31.0",
    "python-httpx/0.28.1",
    "Wget/1.21.4",
    "Go-http-client/2.0",
])
def test_a_named_scripting_tool_is_flagged(agent):
    """These identify themselves honestly. Blocking them costs nothing and
    stops the least sophisticated traffic, which is most of it."""
    headers = dict(BROWSER, **{"user-agent": agent})

    assert looks_automated(headers) is not None


def test_a_navigation_without_sec_fetch_headers_is_flagged():
    """Every browser released since 2020 sends Sec-Fetch-* on a navigation.
    A caller that sends an HTML Accept header but no Sec-Fetch-Mode is
    imitating a browser rather than being one."""
    headers = {k: v for k, v in BROWSER.items() if not k.startswith("sec-fetch")}

    assert looks_automated(headers) is not None


def test_the_reason_says_which_signal_fired():
    """It is rendered to staff on the blocklist screen and written into the
    audit entry. "Automated" alone is not something anyone can act on."""
    headers = dict(BROWSER, **{"user-agent": "curl/8.4.0"})

    assert "curl" in looks_automated(headers).lower()


def test_header_names_are_matched_case_insensitively():
    """Starlette lowercases them; a raw dict from a test or another caller
    may not."""
    upper = {k.upper(): v for k, v in BROWSER.items()}

    assert looks_automated(upper) is None


def test_the_rate_counter_counts_within_the_window():
    rate = RequestRate(window_seconds=60)
    for i in range(5):
        rate.record("abc", now=1000.0 + i)

    assert rate.count("abc", now=1004.0) == 5


def test_the_rate_counter_forgets_beyond_the_window():
    """A sliding window, not a bucket that resets on a boundary — otherwise a
    caller can double its allowance by straddling the reset."""
    rate = RequestRate(window_seconds=60)
    rate.record("abc", now=1000.0)
    rate.record("abc", now=1050.0)

    assert rate.count("abc", now=1061.0) == 1


def test_counters_are_independent_per_key():
    rate = RequestRate(window_seconds=60)
    rate.record("abc", now=1000.0)

    assert rate.count("def", now=1000.0) == 0


def test_the_counter_does_not_grow_without_bound():
    """This lives in process memory for the life of the deployment. A
    counter that never evicts is a slow leak an attacker can drive."""
    rate = RequestRate(window_seconds=60, max_keys=100)
    for i in range(500):
        rate.record(f"key-{i}", now=1000.0)

    assert rate.size() <= 100
