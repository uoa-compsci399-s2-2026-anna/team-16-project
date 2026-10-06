#!/usr/bin/env python
"""Run the browser suite in batches, restarting `api` between them.

**Why this exists rather than `pytest -m browser`.** §6.5 caps a caller at 600
`GET`s an hour and `POST /calculate` plus `/export/pdf` at 120, keyed on an HMAC
of the client IP (`api/router.py`'s `_limit`), with no environment override. The
browser suite is over a thousand cases and does not fit in one window: past it
the API answers 429 and every later case fails waiting for a control that was
never drawn, which reads as a code failure and is not one. The practice has been
"run it in batches with `docker compose restart api` between them", written in
CLAUDE.md and carried out by hand. This is that practice as a program, so CI can
do it the same way a developer does and neither has to remember it.

Restarting `api` is what clears the buckets: the counter is a dict in that
process, so a restart empties it. `web` is left alone -- its nginx resolves the
api hostname when its configuration loads, so recreating `api` underneath it is
what produces the 502s this project has chased twice.

    python tests/web/run_browser_suite.py                      # every browser module
    python tests/web/run_browser_suite.py --cap 90             # smaller batches
    python tests/web/run_browser_suite.py tests/web/test_x.py  # just these

Exit status is 0 only if every batch passed. A batch that fails is reported with
its own output and the run continues, so one failure does not hide the rest.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker" / "compose.yaml")]
#: Cases per batch. 150 is measured rather than chosen: `test_results_export.py`
#: (148) and `test_step_navigation.py` (127) each run clean on a fresh window,
#: and the batches that have hit 429 in this project were larger than either.
DEFAULT_CAP = 150


def browser_modules(paths: list[str]) -> list[pathlib.Path]:
    """Every test module that drives Playwright, in a stable order."""
    roots = [ROOT / p for p in paths] if paths else [ROOT / "tests"]
    found: list[pathlib.Path] = []
    for root in roots:
        if root.is_file():
            found.append(root)
            continue
        found.extend(sorted(root.rglob("test_*.py")))
    return [p for p in found if re.search(r"\bplaywright\b", p.read_text(encoding="utf-8"))]


def case_count(module: pathlib.Path) -> int:
    """How many cases a module collects, or 0 if it cannot be collected.

    **A module that collects nothing is the failure this whole file exists
    beside**, so it is reported rather than quietly packed into a batch: it is
    what `playwright` being absent looks like, and what a stack that is down
    looks like at collection time.
    """
    done = subprocess.run(
        [sys.executable, "-m", "pytest", str(module), "-o", "addopts=", "-q", "--collect-only"],
        cwd=ROOT, capture_output=True, text=True,
    )
    match = re.search(r"(\d+) tests? collected", done.stdout)
    return int(match.group(1)) if match else 0


def batches(modules: list[pathlib.Path], cap: int) -> list[list[pathlib.Path]]:
    """Pack modules into batches of at most `cap` cases, keeping file order.

    Greedy and order-preserving rather than optimal: a module never moves away
    from its neighbours, so a failing batch names a contiguous run of files and
    is easy to re-run by hand. A single module larger than the cap gets a batch
    of its own.
    """
    packed: list[list[pathlib.Path]] = []
    current: list[pathlib.Path] = []
    size = 0
    for module in modules:
        count = case_count(module)
        if count == 0:
            print(f"  !! {module.relative_to(ROOT)} collects no cases", flush=True)
        if current and size + count > cap:
            packed.append(current)
            current, size = [], 0
        current.append(module)
        size += count
    if current:
        packed.append(current)
    return packed


def restart_api() -> None:
    subprocess.run([*COMPOSE, "restart", "api"], cwd=ROOT,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    #: The container answers before FastAPI is listening, and a batch that starts
    #: into a half-open socket fails on its first navigation. Short and fixed
    #: rather than a health poll, because the health check is the thing that
    #: answers early.
    time.sleep(4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="files or directories (default: tests/)")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP,
                        help=f"cases per batch (default {DEFAULT_CAP})")
    parser.add_argument("--no-restart", action="store_true",
                        help="do not restart `api` between batches")
    args = parser.parse_args()

    modules = browser_modules(args.paths)
    if not modules:
        print("no browser modules found — nothing to run, which is itself a result", flush=True)
        return 1

    print(f"{len(modules)} browser module(s), cap {args.cap} cases a batch", flush=True)
    plan = batches(modules, args.cap)
    print(f"{len(plan)} batch(es)\n", flush=True)

    failed: list[int] = []
    for number, batch in enumerate(plan, 1):
        if number > 1 and not args.no_restart:
            restart_api()
        names = " ".join(str(m.relative_to(ROOT)) for m in batch)
        print(f"-- batch {number}/{len(plan)}: {len(batch)} module(s)", flush=True)
        done = subprocess.run([sys.executable, "-m", "pytest", *[str(m) for m in batch]],
                              cwd=ROOT, text=True)
        if done.returncode != 0:
            failed.append(number)
            print(f"   batch {number} FAILED — rerun with: python -m pytest {names}", flush=True)

    print()
    if failed:
        print(f"{len(failed)} of {len(plan)} batches failed: {failed}", flush=True)
        return 1
    print(f"all {len(plan)} batches passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
