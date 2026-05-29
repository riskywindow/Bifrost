# Phase 2 Fault Injection

Last verified: 2026-05-28

ContextStorm supports small optional local fault profiles for Phase 2 transport
experiments. This is not production chaos engineering, and CI must not require
root, Docker, `tc`, netem, GPU hardware, internet access, LMCache, or vLLM.

## Profile Types

Fault profile fields:

1. `type`: `none`, `tc_netem`, `process_kill`, or `artificial_delay`
2. `interface`: required for `tc_netem`
3. `delay_ms`
4. `jitter_ms`
5. `loss_percent`
6. `rate_mbit`
7. `apply_at_ms`
8. `remove_at_ms`
9. `target_path`

Safe in CI:

1. `clean.yaml`
2. `path_death.yaml`, if the process-level test has built Rust binaries and
   loopback is available. It kills a ContextStorm-managed daemon process and
   does not require root.

Root-required, opt-in only:

1. `delay_50ms.yaml`
2. `loss_1pct.yaml`
3. `loss_5pct.yaml`
4. `bandwidth_50mbit.yaml`

## Running Locally

Build the Rust binaries first:

```text
cd bifrostd
cargo build
```

Run a non-root daemon-kill scenario:

```text
cd contextstorm
PYTHONPATH=. python -m contextstorm.cli run scenarios/dead_path.yaml
```

Run a root-required loopback netem scenario only on a local machine where qdisc
mutation is acceptable:

```text
cd contextstorm
sudo PYTHONPATH=. python -m contextstorm.cli run scenarios/lossy_two_path.yaml --allow-root-faults
```

ContextStorm refuses `tc_netem` profiles unless `--allow-root-faults` is passed.
It also checks root privileges and the `tc` command before applying the fault,
prints the exact `tc` commands, records events in `fault_events.jsonl`, and
always attempts cleanup.

## Manual Recovery

If cleanup fails after a `tc_netem` run, remove the qdisc manually. For the
provided profiles, the interface is `lo`:

```text
sudo tc qdisc del dev lo root
```

Check current qdisc state with:

```text
tc qdisc show dev lo
```

If you changed `interface` in a profile, replace `lo` with that interface name.

## Artifacts

Fault-enabled runs write `fault_events.jsonl` next to `run.json`. Events include
profile loading, apply attempts, skips, cleanup attempts, and reasons such as
missing opt-in, missing root privileges, unavailable `tc`, or killed process.
