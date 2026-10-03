"""The one thing that matters about this job.

A scheduled job has no human behind it. The shortcut everyone reaches for — mint
a user token for a "service account" person — works immediately and makes scope
grants simpler, and it turns every audit record this job writes into a false
statement. An auditor reading *"alice@ read the spend dataset at 03:00"* has no
way to know alice was asleep (ADR-1, ADR-4).

So the test that earns its place is not "the export produced numbers". It is
"the identity in the audit trail is a service, and nothing in this job ever held
a user token".

Nothing is mocked here. The job talks to a fake identity provider, warehouse and
audit sink over real HTTP (see conftest), so what is asserted is what an auditor
would actually read.
"""

from __future__ import annotations

import asyncio

from spend_export.main import JOB_NAME, main, summarise

from .conftest import AUDIT_EVENTS, SPEND_ROWS, TENANT, TOKEN_REQUESTS


def test_the_job_runs_as_a_service_principal_not_a_user():
    before_tokens = len(TOKEN_REQUESTS)
    before_audit = len(AUDIT_EVENTS)

    asyncio.run(main())

    # It asked the identity provider for a *service* token, by job name. There
    # is no user credential anywhere in this exchange — the job does not have
    # one to offer.
    requests = TOKEN_REQUESTS[before_tokens:]
    assert requests == [{"tenant_id": TENANT, "service": JOB_NAME}]

    # And the audit trail names a service, not a person. This is the assertion
    # the whole test exists for: it is what a compliance reader sees.
    events = AUDIT_EVENTS[before_audit:]
    assert events, "the job recorded no audit event for its warehouse read"
    assert all(event["principal"] == f"service:{JOB_NAME}" for event in events)
    assert all(not event["principal"].startswith("user:") for event in events)

    read = next(e for e in events if e["action"] == "warehouse.read")
    assert read["resource"] == "spend"
    assert read["tenant_id"] == TENANT


def test_a_malformed_row_is_counted_not_fatal():
    """A single bad amount should not fail the export, and should not be
    printed — the value is the untrusted part."""
    totals, skipped = summarise(SPEND_ROWS)
    assert skipped == 1
    assert totals["Cloud Infrastructure"] == 525_000.0
    assert "Travel" not in totals
