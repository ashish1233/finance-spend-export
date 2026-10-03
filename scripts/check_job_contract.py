#!/usr/bin/env python3
"""ADR-3 CI check: this job conforms to the platform contract.

ADR-3 asks for health-contract conformance in CI, because ADR-4's operator view
across twenty-five apps only works if they all report identically. A batch job
has no HTTP surface, so there is no `/health` to poll — the equivalent contract
is that the job runs *through the harness*, which is what emits the started,
completed, duration and failure signals an operator uses to answer the only
questions a job raises: did it run, how long did it take, and has it silently
stopped?

So this check asserts two things, and they are the two a tenant can break:

1. The job body is invoked through `insights_platform.jobs.run`, not called
   directly. A job that calls its own body produces no telemetry and no service
   identity, and looks fine in review.
2. The principal the body receives is a **service** identity. ADR-1 makes this
   an identity distinction rather than a convention, because a job attributed to
   a user corrupts the audit trail ADR-4 depends on — and the corruption is
   invisible until an auditor acts on it.

It runs offline against a fake identity provider. Nothing real is dialled.

Usage:  check_job_contract.py [module]   (default: job.main)
"""

from __future__ import annotations

import asyncio
import os
import sys
from importlib import import_module
from unittest.mock import patch

# Generous: the body is replaced below, so the only work is wiring.
RUN_TIMEOUT_SECONDS = 10.0

PLACEHOLDER_ENV = {
    "INSIGHTS_TENANT_ID": "ci-job-contract-check",
    "INSIGHTS_IDP_URL": "http://idp.invalid",
    "INSIGHTS_WAREHOUSE_URL": "http://warehouse.invalid",
    "INSIGHTS_AUDIT_URL": "http://audit.invalid",
}


def fail(message: str) -> None:
    print(f"job-contract: FAIL — {message}", file=sys.stderr)
    raise SystemExit(1)


def main(module_name: str) -> None:
    for key, value in PLACEHOLDER_ENV.items():
        os.environ.setdefault(key, value)

    from insights_platform import PrincipalKind, issue_token
    from insights_platform import jobs as sdk_jobs

    try:
        module = import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — any import failure is a failure
        fail(f"could not import {module_name!r}: {type(exc).__name__}: {exc}")

    if not hasattr(module, "main"):
        fail(f"{module_name!r} has no async `main()` to run")

    observed: dict[str, object] = {}

    async def fake_token(config, job_name):
        """Stand in for the identity provider.

        Note what this fake is *allowed* to mint: a service token. If the job
        under test wanted a user token it would have to go somewhere other than
        the harness to get one, which is the failure this check exists to catch.
        """
        observed["job_name"] = job_name
        return issue_token(
            kind=PrincipalKind.SERVICE,
            subject=job_name,
            tenant_id=config.tenant_id,
            scopes={"export:run", "insights:read"},
        )

    real_run = sdk_jobs.run

    async def watched_run(name, body, *, config):
        observed["ran_through_harness"] = True

        async def watched_body(context):
            observed["principal_kind"] = context.principal.kind
            observed["principal"] = context.principal.describe()
            # Do not execute the real body: it would reach for the warehouse,
            # and this check is about wiring, not about the query.
            return None

        return await real_run(name, watched_body, config=config)

    async def run_once() -> None:
        # A job must return. The scheduler owns the cadence (one invocation per
        # trigger), so a `main()` that does not terminate is a job that has
        # taken over scheduling — which is the thing ADR-1's job template asks
        # teams not to do, and which would hang this check rather than fail it.
        await asyncio.wait_for(module.main(), timeout=RUN_TIMEOUT_SECONDS)

    with (
        patch.object(sdk_jobs, "_fetch_service_token", fake_token),
        patch.object(module, "run_job", watched_run),
    ):
        try:
            asyncio.run(run_once())
        except TimeoutError:
            fail(
                f"`main()` did not return within {RUN_TIMEOUT_SECONDS}s. A job "
                "runs once and exits; the platform scheduler decides the "
                "cadence. If your job has a deliberate loop for local use, set "
                "its interval to the single-run value in this hook's "
                "environment, as finance-spend-export does."
            )
        except Exception as exc:  # noqa: BLE001
            fail(f"the job raised {type(exc).__name__}: {exc}")

    if not observed.get("ran_through_harness"):
        fail(
            "the job body was not run through `run_job`. Without the harness "
            "there is no service identity and no job telemetry, so an operator "
            "cannot tell a job that failed from one that was never scheduled."
        )
    if observed.get("principal_kind") is not PrincipalKind.SERVICE:
        fail(
            f"the job body received a {observed.get('principal_kind')} "
            "principal. Jobs run as service identities (ADR-1); attributing "
            "unattended work to a person corrupts the audit trail."
        )

    print(f"job-contract: ok — {observed['job_name']} runs as {observed['principal']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "job.main")
