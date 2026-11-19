# reliability — Round 1

## Verdict
**SHIP-WITH-CHANGES.** The pipeline shape, fakes-at-every-port and rules-win are sound. But three things will make this job quietly go wrong on a laptop: holds caused by transient failures stick until a new version ships, the service rule doesn't see this machine's real daemons, and nothing records state across a crash or raises an alarm when runs stop happening. All three are cheap to fix before T01 defines the ports.

## Top 3 must-change

**1. Holds caused by transient failures become permanent (FR-054 vs FR-061/062/064, FR-010).**
Problem: re-triage fires only when `current_version` changes. A GREY candidate held because Ollama was down, the budget ran out, Claude was rate limited, or the join failed has the same version tomorrow, so it stays pinned and silent until upstream ships again. That can be weeks.
Scenario: Ollama isn't running at 07:00 after a reboot. awscli, grype, imagemagick and numpy all get HOLD "model unavailable" and are pinned. Ollama is up by 07:05, but the run is already stamped as successful, so they are never looked at again.
Change: split hold reasons into `decided` (a rule, a model verdict, a human skip) and `transient` (model unavailable, budget, L3 error, unjoinable). Transient holds are re-triaged every run, and they **exclude** the package from the upgrade list rather than pinning it. The stamp records `quality: full|degraded`. A degraded stamp does not suppress the second slot.

**2. The service rule reads `brew services` status, but this machine runs brew binaries from its own LaunchAgents (FR-030 rules 6 and 11, Q7).**
Evidence (read-only, observed today): `brew services list --json` reports `vector: none` and `nats-server: none`. Yet `pgrep` shows `/opt/homebrew/bin/vector --config-dir /opt/homebrew/etc/vector/conf.d` and `/opt/homebrew/opt/nats-server/bin/nats-server` running, launched by `~/Library/LaunchAgents/com.logshipping.vector.plist` and `anderson.nats-server.plist`. The DESIGN §5 hand-run assumes vector is "started" and therefore HOLD. Under the spec as written it is GREY with cap 0.85, which two agreeing local models can auto-upgrade. vector is a 0.x project where config changes in minor releases are common (belief). Also seen: `grafana` shows `status: started` with `exit_code: 1`, so brew's status label hides error states.
Change: rule 6 becomes HOLD if **any** of these is true: brew services status is not `none`; any plist in `~/Library/LaunchAgents` or `/Library/Launch{Agents,Daemons}` has a `ProgramArguments[0]` that resolves into the formula's `opt`/`Cellar`/`bin` paths; a running process's executable resolves there. The plist scan is a cheap pure function over plist files and belongs in the snapshot (FR-012).

**3. No crash journal, no reconciliation, no dead-man alarm (FR-071, §2 step 8, §10).**
Problem: a run can die between `brew pin` and the ledger write, or partway through upgrades. Causes include closing the lid, logout SIGTERM, `ExitTimeOut`, or a 30-minute per-package timeout. If the pin lands without its ledger record, every later run treats it as a **user** pin (SKIP forever, never unpinned). A run that never starts is worse: the fnox wrapper fails, `uv` can't resolve offline, or the plist was booted out. Every notifier lives inside the process that didn't run, so the owner hears nothing.
Change:
(a) Write the ledger record with `state: pending_pin` **before** pinning, then mark it `pinned`.
(b) Every run opens with a reconcile step that compares `brew list --pinned` with the ledger. Pending with pinned resolves to tool-owned. A tool-owned record whose package is no longer pinned means the user unpinned it: release the record and audit it (this answers Q2). A pin with no record is a user pin.
(c) Keep a `last_full_success` timestamp. Any invocation, including the no-op stamp path and `status`, notifies urgently when it is older than 48 h. `status` exits non-zero when stale, so it can feed a shell prompt or another monitor.
Plan: add T27 (reconcile, 3 pts) and fold the staleness check into T14 (+1).

## Positions on open questions
- **Q1.** Do not pin dependencies. Exclude them, and treat a held dependency as holding its whole dependent closure, computed deterministically. A pinned dependency makes the owner's own interactive `brew upgrade` fail on its dependents every day (`brew pin --help`: "Other packages which depend on newer versions of a pinned formula might not install"). That is daily noise. Confidence 4.
- **Q2.** The per-package ledger is enough once the reconcile step from must-change 3 exists. A human skip should persist per version, as FR-082 already does. Confidence 4.
- **Q4.** Running app → DEFER is correct, but count consecutive defers. After 5 it should show up in the summary. Otherwise an always-open app is never upgraded and never reported. pkg ⇒ HOLD is enough. Confidence 3.
- **Q5.** Deferred cleanup is right. Measure N in **days**, not successful runs, because a week of degraded runs shouldn't stretch the window. Rollback means old keg plus a hand-written README procedure, nothing more. Don't promise more. Confidence 3.
- **Q6.** Don't put `fnox exec` in `ProgramArguments`. If it fails, the whole job fails before Python starts, so nothing can notify. Run the venv binary directly and fetch the key lazily (`fnox get` via subprocess, timeout 10 s) only when L3 is needed. If that fails, L3 is disabled. Confidence 4.
- **Q7.** Hold on any service evidence, using the three sources in must-change 2. Status alone is demonstrably wrong on this machine. Confidence 5.
- **Q8.** `man launchd.plist` says RunAtLoad "should be avoided". It also fires at login, when Wi-Fi often isn't up yet, and logging in after the second slot means no retry that day. Prefer a `StartInterval` of 3600 s gated by the stamp plus an earliest-hour config. Off-schedule runs become cheap no-ops and retries become automatic. Exit 75 means nothing to launchd without KeepAlive. It is only a log marker, which is fine, but don't call it a retry mechanism. Confidence 4.
- **Q9.** Outside my lens.

## Things nobody asked about
- **Notification fatigue and ephemerality.** A `say` at 07:00 is spoken to an empty room or a meeting and leaves no trace. Default to silence on routine success. Notify on new decided holds, failures, anomalies and staleness. Use a persistent channel (Notification Center via osascript) for those, and keep `say` for `urgent`. Add quiet hours and a weekly "holds aging" digest so pins don't pile up unseen.
- **Sleep mid-run.** Wrap the apply phase in `caffeinate -i -w <pid>`, which prevents idle sleep but not lid-close (man page, belief on edge cases). The budget clock must be wall time. Whether Python's `time.monotonic` counts sleep on macOS is unverified, and the spec should pin down which one it uses.
- **Battery.** Skip or defer the run when on battery below about 30% (`pmset -g batt`). Loading the 19 GB L2 model and building from source on battery is unfriendly.
- **Bottle-less builds.** Tap formulae without an arm64 bottle compile from source, which can run longer than the 30-minute timeout and leave half-installed kegs. Add a rule: no bottle for this arch ⇒ HOLD (`bottle.stable.files` in info JSON, belief on exact key).
- **Contention with the owner's own brew.** If the owner runs brew at the same moment, brew's own lock makes our upgrade fail. Classify that as DEFER, not `failed`.
- **Tool self-dependency.** Don't use `uv run` from the plist, because it can need network to resolve. Use `uv sync --frozen` at install and point the plist at `.venv/bin/morning-brew`. When a Homebrew self-update changes the JSON shape, the run should fail loudly every day (exit 1), and the staleness alarm must cover that case.
- **Log growth.** Nothing rotates StandardOut/ErrPath or the audit JSONL. Add size-based rotation (+1 to T13).
- **Running daemons after upgrade.** The old binary keeps running until it restarts. Immediate cleanup deletes the keg under it. The verify step should note that "upgraded, still running old version" is expected and not an anomaly.

## Overbuilt
- T24 + S3, the OpenAI-compatible adapter and its probe: cut from v1, saves **6**.
- T08's bounded drop-iteration loop: replace it with a single deterministic pass ("any held member holds the closure") per Q1. Saves about **2**.
- T22: ship `say` and `osascript` only, and defer `CommandNotifier`. Saves about **1**.
- Net: about −9 pts cut, +5 added (T27 reconcile, staleness check, log rotation). Implementation goes from about 111 to about 107.
