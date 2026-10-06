"""Published methodology tables stay readable at the widths and languages we ship.

The source-table fix is a rendered layout contract: headings must remain readable,
cells must not be clipped or ellipsized, and narrow screens must scroll the table
inside its labelled region rather than widening the document itself.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright is required to measure rendered table layout")

from tests.web.base_url import ORIGIN


WIDTHS = (320, 390, 1278)
LANGUAGES = ("en", "de", "ar", "ja", "zh", "fr", "th")


def _stack_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{ORIGIN}/methodology.html", timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


if not _stack_is_up():  # pragma: no cover - environment guard
    pytest.skip(
        f"the stack is not answering on {ORIGIN}; run "
        "`docker compose -f docker/compose.yaml up -d --build web admin`",
        allow_module_level=True,
    )


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("language", LANGUAGES)
def test_methodology_tables_keep_headings_and_cells_readable(browser, width, language):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    try:
        page.goto(f"{ORIGIN}/methodology.html?lang={language}", wait_until="networkidle")
        page.wait_for_selector(".methodology-page .table-scroll table")
        measurements = page.evaluate(
            """() => {
                const tables = [...document.querySelectorAll('.methodology-page .table-scroll')]
                const headings = [...document.querySelectorAll('.methodology-page thead th')]
                const cells = [...document.querySelectorAll('.methodology-page th, .methodology-page td')]
                return {
                    pageScroll: document.documentElement.scrollWidth,
                    pageClient: document.documentElement.clientWidth,
                    regions: tables.map(region => ({
                        scroll: region.scrollWidth,
                        client: region.clientWidth,
                        labelled: region.getAttribute('aria-label'),
                    })),
                    headings: headings.map(heading => ({
                        width: heading.clientWidth,
                        scroll: heading.scrollWidth,
                        height: heading.getBoundingClientRect().height,
                        lineHeight: parseFloat(getComputedStyle(heading).lineHeight),
                    })),
                    cells: cells.map(cell => ({
                        clipped: cell.scrollWidth > cell.clientWidth + 1,
                        overflow: getComputedStyle(cell).overflow,
                        textOverflow: getComputedStyle(cell).textOverflow,
                    })),
                }
            }"""
        )
        assert measurements["pageScroll"] <= max(measurements["pageClient"], 320)
        assert all(
            heading["scroll"] <= heading["width"] + 1
            and heading["height"] <= heading["lineHeight"] * 1.25
            for heading in measurements["headings"]
        )
        assert all(not cell["clipped"] for cell in measurements["cells"])
        assert all(cell["overflow"] not in {"hidden", "clip"} for cell in measurements["cells"])
        assert all(cell["textOverflow"] != "ellipsis" for cell in measurements["cells"])
        if width <= 390:
            assert all(region["scroll"] >= region["client"] for region in measurements["regions"])
            assert all(region["labelled"] for region in measurements["regions"])
    finally:
        context.close()
