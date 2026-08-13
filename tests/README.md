# Edge-Core pytest + testinfra harness (no Molecule)

Run role-level tests by calling your CLI to apply config, then asserting with testinfra.
This version supports the flow you described:
1) replace YAML
2) run the **role** via `/opt/edgestream-core/bin/edgestream_task --module <role>`
3) verify results (e.g., DNS actually changed)

## Quick start
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r tests/requirements.txt

# Run only dns-settings tests
pytest -q tests/roles/dns-settings
```

## Env vars
- `EDGESTREAM_TASK_BIN` (default: `/opt/edgestream-core/bin/edgestream_task`)
- `SKIP_TASK_STEP` (default: `0`) — if `1`, skip the `--task <file>` ingestion step and only run `--module <role>`
- `TEST_DNS_APPLY_TIMEOUT` (default: `30`) — seconds to wait for nameservers to update
- `DNS_RESOLVE_DOMAIN_SUCCESS` (default: `www.google.com`)
- `DNS_RESOLVE_DOMAIN_FAIL` (default: same as success)

> By default, the fixture **does** run `--task <tmp configure.yaml>` *and then* `--module <role>`.
> Toggle `SKIP_TASK_STEP=1` if your role pulls config from a known path and doesn't need `--task`.
