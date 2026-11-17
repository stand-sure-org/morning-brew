# PLAN: brew-triage

**Version:** 1.1 (Post-Delphi)
**Date:** 2026-11-17
**Changes:** Re-cut after the Delphi review and owner decisions. Spikes S1, S2, S3, S5 retired (answered by panel evidence or scope cuts). Added caveat diffing before the model chain, live-process set, components, tap trust and soak, golden set and shadow mode, transient vs decided holds, reconcile and dead-man, hold aging, lazy fnox get. Cut OpenAI adapter, cask apply, pin machinery.

Points are Fibonacci complexity points; no time estimates; no task at 21 or more. Ordering is TDD and top-down: the orchestrator's tests define the ports, lower layers implement them. Nothing starts until the owner approves v1.1.

## Spikes (still open)

| ID | Task | Pts | Gates |
|----|------|-----|-------|
| S4 | `fnox get` non-interactively under a throwaway launchd job (stdin closed, 10 s timeout) | 3 | T20, T21 |
| S6 | Anthropic SDK 2.x: structured output via `output_config.format`, refusal and typed-error handling, `fallbacks` form, against live docs | 3 | T20 |

## Phase 0: skeleton

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T00 | `uv` package layout, pytest, ruff, `anthropic` optional extra, sanitized fixtures (outdated/info/services JSON with tap formulae, ngrok stderr noise, panel-evidence cases: vector LaunchAgent, rabbitmq/erlang, aws trio component) | 3 | |

## Phase 1: top of the stack

| ID | Task (test first) | Pts | Depends |
|----|-------------------|-----|---------|
| T01 | Orchestrator happy path with fakes; defines all ports; asserts "triage completes before apply" (FR-005, 006) | 5 | T00 |
| T02 | Orchestrator failure matrix: update failure exit 75, partial component failure, anomaly on verify, closure mismatch, budget expiry, transient vs decided outcomes, notifier raising (FR-041..043, 066) | 5 | T01 |
| T03 | Run lifecycle: lock, earliest-hour and once-per-day gate, stamp with `quality`, dead-man 48 h, `--dry-run` zero mutations (FR-001, 002, 063, 081) | 5 | T01 |
| T04 | CLI shell: run, status, explain, review wiring with fakes, exit codes, ANSI/control stripping (FR-081) | 3 | T01 |

## Phase 2: domain core

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T05 | Version/bump classifier with the full FR-020 table | 3 | T01 |
| T06 | Facts derivation: `full_name`/token join, tap, bottle arch, dependents inversion, unjoinable -> transient (FR-010, 012) | 5 | T05 |
| T07 | Component builder from `runtime_dependencies` with name validation; one non-eligible member blocks the component (FR-004, 040) | 3 | T06 |
| T08 | Rules table: all 12 rules, tap trust, soak, property test "models never change non-GREY" (FR-030, 033) | 5 | T06, T10 |
| T09 | Live-process set: services, LaunchAgent/Daemon `ProgramArguments[0]` with symlinks, running executables, dependency closure; keg-clean exclusion (FR-031) | 5 | T06 |
| T10 | Caveat diff store and first-seen store (hash at last upgrade, seeding at setup) (FR-032, 033) | 3 | T06 |

## Phase 3: storage and plumbing

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T11 | Holds store: decided vs transient, `skipped_for_version`, re-triage on version change, 0700/0600 atomic writes (FR-061, 065) | 3 | T08 |
| T12 | Reconcile (detect user pins), hold aging counts, stale-hold escalation, weekly digest data (FR-062, 064) | 3 | T11 |
| T13 | Audit JSONL writer (brew version, models and digests, redacted stderr tails), vendored sanitizer (FR-070) | 3 | T01 |
| T14 | Config loader: tomllib, refuse group/world-writable, `trusted_taps`, digests, env allowlist and secrets-free child env test (FR-003) | 3 | T01 |
| T15 | Brew adapter: runner (no shell, `--`, timeouts, stdin devnull), parse with stderr-noise fixtures, plain `outdated` snapshot, explicit component upgrade, dry-run cross-check, per-package deferred cleanup with live-process exclusion (FR-010, 011, 040, 041, 044) | 8 | T07, T09, T14 |

## Phase 4: model chain (caveat diffing T10 is already done)

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T16 | Golden set (~25-30 real, dangerous and injection cases) plus `morning-brew eval` runner reusing the audit JSONL; ship-gate check and refusal of `chain.mode=live` without a recorded pass (FR-057) | 5 | T10, T13 |
| T17 | Prompt builder (random delimiters, caps, redaction), `suspicious_text` flag, boolean-first schemas (pydantic), coverage check (FR-051, 052, 056) | 5 | T16 |
| T18 | Chain logic with scripted fakes: suspicious flag, local second-opinion (never decides; disagreement surfaced), Claude blind, shadow vs live, Ollama-down still reaches Claude, unsure paths -> transient (FR-050..053) | 5 | T17 |
| T19 | Ollama adapter: stdlib urllib, `format` schema, `think:false`, `num_predict`, digest pin, loopback-only and off-loopback warning (FR-055) | 3 | T18 |
| T20 | Claude adapter + lazy `fnox get` (10 s, stdin devnull, key never in env), refusal and typed-error mapping to transient (FR-054) | 5 | T18, S4, S6 |

## Phase 5: surfaces

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T21 | launchd plist generator and install/uninstall (`StartInterval` 3600, direct venv binary via `uv sync --frozen`, PATH, logs, no RunAtLoad, no fnox wrapper); setup seeds `trusted_taps` and caveat hashes with confirmation (FR-001, 081) | 3 | T04, T10, S4 |
| T22 | Notifiers: silent notification via AppleScript argv handler, status file, urgent `say`/command, quiet hours, swallow failures (FR-071) | 3 | T01 |
| T23 | Review CLI: list decided holds, approve component with live-process confirmation, skip per version, untrusted-derived labeling (FR-080) | 3 | T11, T15 |

## Phase 6: verification

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T24 | Shadow-mode reporting and real read-only `run --dry-run` against the machine, compared with the DESIGN section 5 hand-run | 3 | T15, T19, T20 |
| T25 | README (install, rollback procedure with no automated rollback, recovery) and opt-in integration markers | 2 | T24 |

## Checkpoints

- After Phase 2: rules and components reproduce the DESIGN section 5 table on fixtures, including vector as live-process HOLD and the aws trio as one component.
- After T16: the golden set exists before any chain code (the gate is defined before the thing it measures).
- After Phase 4: adversarial-caveat and false-claim fixtures never approve; ship gate recorded before `live`.
- After Phase 5: install on a throwaway label, kickstart one run, uninstall.

## Totals

Implementation tasks T00-T25: 3+5+5+5+3 (=21) +3+5+3+5+5+3 (=24) +3+3+3+3+8 (=20) +5+5+5+3+5 (=23) +3+3+3 (=9) +3+2 (=5) = **102 points**. Open spikes S4 and S6: **6 points**. Total including spikes: **108 points**. No task is 21 or more.
