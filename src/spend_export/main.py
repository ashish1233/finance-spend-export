"""Finance spend export — an example scheduled job.

Reads the `spend` dataset and writes a summary to stdout. It is deliberately
trivial; it exists to show what the consumption model costs a team, not to be a
useful export.

- **Tenant:** `finance`
- **Tier:** standard (ADR-2) — assigned by the platform team
- **Identity:** a service principal. There is no human behind this job, and
  attributing its reads to one would corrupt the audit trail (ADR-1, ADR-4).

Built from `templates/job` with three changes: the job name, the dataset, and
the body. The loop at the bottom is the fourth, and it is a demo affordance —
see the comment there.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from typing import Any

from insights_platform import JobContext, load_config, run_job

# The service principal's name, as the identity provider knows it. The
# repository is `finance-spend-export`; the principal is `spend-export` within
# the `finance` tenant, because the IdP keys service principals by
# (tenant, service) — the same job name in two tenants is two different
# principals with two different approvals behind them.
JOB_NAME = "spend-export"
DATASET = "spend"


def summarise(rows: list[dict[str, Any]]) -> tuple[dict[str, float], int]:
    """Aggregate spend by category. Returns the totals and a skipped count.

    The SDK's telemetry gate cannot reach `print()` — stdout is this job's
    product, not a log call. That makes the no-payload rule from ADR-4 a choice
    here rather than something enforced, and stdout from a deployed container is
    collected like any other output, so the choice matters: aggregate, then
    print.
    """
    totals: dict[str, float] = defaultdict(float)
    skipped = 0
    for row in rows:
        try:
            amount = float(row.get("amount", 0))
        except (TypeError, ValueError):
            # One malformed row should not fail the export, but it should be
            # visible. Counted, never printed — the value is the untrusted part.
            skipped += 1
            continue
        totals[str(row.get("category", "uncategorised"))] += amount
    return dict(totals), skipped


async def body(context: JobContext) -> dict[str, Any]:
    """Read spend and write a summary to stdout."""
    rows = await context.warehouse.fetch(context.principal, DATASET)
    totals, skipped = summarise(rows)

    # Counts and identifiers only. `context.log.info(rows=rows)` raises (ADR-4).
    context.log.info(
        "Read dataset.", dataset=DATASET, row_count=len(rows), skipped_rows=skipped
    )

    print(f"--- {JOB_NAME}: {DATASET} summary ---")
    print(f"rows read       : {len(rows)}")
    print(f"rows skipped    : {skipped}")
    print(f"total spend     : {sum(totals.values()):,.2f}")
    for category, amount in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {category:<24} {amount:>14,.2f}")
    print(f"--- end {JOB_NAME} ---", flush=True)

    return {"row_count": len(rows), "category_count": len(totals), "skipped": skipped}


async def main() -> None:
    config = load_config()

    # A real deployment does not have this loop. The platform scheduler invokes
    # the container once per trigger and reads the exit code; owning a cadence
    # here would mean twenty-five jobs with twenty-five retry behaviours for an
    # operator to learn during an incident.
    #
    # The loop exists so the job is observable in a demo — otherwise it runs for
    # 40 milliseconds and exits, and there is nothing to watch. Set
    # EXPORT_INTERVAL_SECONDS=0 for the production shape: one run, then exit
    # with the status the scheduler should see.
    interval = float(os.environ.get("EXPORT_INTERVAL_SECONDS", "30"))

    if interval <= 0:
        await run_job(JOB_NAME, body, config=config)
        return

    while True:
        try:
            await run_job(JOB_NAME, body, config=config)
        except Exception:
            # Swallowed *only* because this loop is a demo and a stub restart
            # should not end it. `run_job` has already logged the failure with
            # its error class. In the production shape above the exception
            # propagates and the process exits non-zero, which is the whole
            # point: a failed export that exits zero is a silent one.
            pass
        await asyncio.sleep(interval)


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
