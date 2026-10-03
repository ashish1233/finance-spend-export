# Finance spend export

An example tenant job. It reads the `spend` dataset and prints a summary. It is
deliberately trivial — it exists to prove the consumption model, not to be a
useful export.

- **Tenant:** `finance`
- **Tier:** standard (ADR-2) — assigned by the platform team
- **Dataset:** `spend`
- **Identity:** a service principal, `service:spend-export`

The repository is `finance-spend-export`; the service principal is `spend-export`
*within* the `finance` tenant, because the identity provider keys service
principals by `(tenant, service)` — the same job name in two tenants is two
different principals with two different approvals behind them.

## What it demonstrates

**That a job is not a web app with the HTTP removed.** ADR-1 ships two
templates rather than one because the two differ in runtime model, identity,
signals and deployment. This job has no port, no health endpoint, and no user.

**That the identity distinction is real and enforced.** `run_job()` exchanges
the deploy-time credential for a short-lived *service* token and hands the body
a service principal. The body never sees, and cannot obtain, a user token.

The shortcut this exists to rule out is minting a user token for a "service
account" person. It works immediately and it makes every audit record this job
writes a false statement — an auditor reading *"alice@ read the spend dataset at
03:00"* cannot tell that alice was asleep. That is why ADR-1 makes it an
identity *kind* rather than a naming convention, and why `require_human()`
elsewhere in the SDK means anything at all.

Service tokens also live 15 minutes against a user's hour. ADR-1 chose a library
over a service, so there is no central revocation path; lifetime is the only
control there is.

**That the standard tier's trade is the opposite of the restricted one.** If the
audit sink is down, this job's events buffer locally and the run proceeds. The
People Analytics dashboard refuses the request instead. Both are deliberate:
converting a telemetry outage into a platform-wide outage for ordinary spend
reporting would be a bad trade, and serving an unrecorded compensation figure
would be a worse one (ADR-2).

**That telemetry refuses payloads.** `context.log.info(rows=rows)` raises
(ADR-4). The summary printed to stdout is aggregate-only — and that part is a
choice, not an enforced gate, because `print()` is outside the SDK's reach.
Container stdout is collected like any other output, so the choice matters.

## The loop is a demo affordance

`main()` runs on an interval set by `EXPORT_INTERVAL_SECONDS` (default 30) so
the job is observable while someone is watching. **A real deployment does not
have this.** The platform scheduler invokes the container once per trigger and
reads the exit code; a job that owns its own cadence and retry policy is one of
twenty-five different failure behaviours an operator has to learn during an
incident.

`EXPORT_INTERVAL_SECONDS=0` is the production shape: one run, then exit with the
status the scheduler should see. The `Dockerfile` sets it to 0 by default, so
the loop is opt-in rather than something that escapes into production.

In the loop, a failed run is logged and swallowed so a stub restart does not end
the demo. In the single-run path the exception propagates and the process exits
non-zero — which is the point, because a failed export that exits zero pages
nobody and leaves the data silently stale.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env

# Needs the platform stubs on 8081/8082/8083.
set -a && source .env && set +a
python -m spend_export.main
```

Expected output, interleaved with the SDK's JSON telemetry on the same stream:

```
--- spend-export: spend summary ---
rows read       : 6
rows skipped    : 0
total spend     : 802,000.00
  Cloud Infrastructure         525,000.00
  Software Licences            180,000.00
  Travel                        97,000.00
--- end spend-export ---
```

The audit sink then holds one event per run:

```bash
curl -s 'localhost:8083/events?tenant_id=finance'
# {"principal":"service:spend-export","action":"warehouse.read","resource":"spend",...}
```

That principal is the whole demonstration. It is what an auditor reads, and it
names a service.

`pytest` needs nothing running — the suite starts its own fake identity
provider, warehouse and audit sink on an ephemeral port, and the job talks to
them over real HTTP.
