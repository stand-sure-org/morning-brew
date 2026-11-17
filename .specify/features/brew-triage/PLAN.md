# PLAN: brew-triage

**Version:** 1.0 (Pre-Delphi)
**Date:** 2026-11-17
**Changes:** Initial implementation plan

Nothing here is to be started until the Delphi review completes. Points are Fibonacci complexity points (1, 2, 3, 5, 8, 13). No time estimates. All items are [DESIGNER] sequencing; the TDD and top-down (consumer-defines-interface) ordering is [USER] policy.

## Ordering principle

Work from the top: the CLI and orchestrator are written first against fakes; the orchestrator's tests define the ports (BrewPort, Triage, HoldStore, AuditSink, Notifier, Clock, Lock). Each lower layer is then implemented to satisfy a port that already exists. Every task: write failing test, implement, refactor. Real brew/Ollama/Claude appear only in the final adapter layer and opt-in integration tests.

## Spikes (read-only, gate later tasks)

| ID | Task | Pts | Gates |
|----|------|-----|-------|
| S1 | Capture `brew upgrade --dry-run` output for formula and cask; decide parse vs compute-from-deps closure | 3 | T11 |
| S2 | Inspect cask `artifacts` JSON (pkg vs app, process names) across the 28 installed casks | 2 | T07, T12 |
| S3 | Probe omlx and LM Studio structured output endpoints and thinking control | 3 | T24 (stretch) |
| S4 | Test `fnox exec` non-interactively under a throwaway launchd job | 3 | T21 |
| S5 | `brew pin --cask` / unpin on a throwaway cask, with the user's consent | 2 | T10 |
| S6 | Anthropic SDK 2.x: `messages.parse` / `output_config.format`, `refusal`, `fallbacks` form, verified against live docs | 3 | T19 |

## Phase 0: project skeleton (after Delphi)

| ID | Task | Pts |
|----|------|-----|
| T00 | `uv init` package layout, pytest config, ruff, `anthropic` as optional extra, sanitized JSON fixtures captured from real brew output (names kept, no secrets) | 3 |

## Phase 1: top of the stack (defines the ports)

| ID | Task (test first) | Pts | Depends |
|----|-------------------|-----|---------|
| T01 | Orchestrator happy path with fakes: ordering update -> snapshot -> triage -> apply -> verify -> cleanup -> notify -> stamp (FR-005). Defines all ports | 5 | T00 |
| T02 | Orchestrator failure matrix: update failure (exit 75), partial upgrade failure, notifier raising, budget expiry, verify anomaly (FR-055, 060, 063, 064, 065) | 5 | T01 |
| T03 | Run lifecycle: lock, stamp guard, dry-run flag makes zero mutations (FR-001..003, 083) | 3 | T01 |
| T04 | CLI shell (argparse): run, status, explain, review wiring with fakes; exit codes | 3 | T01 |

## Phase 2: domain core (pure, fast tests)

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T05 | Version/bump classifier with the full SPEC FR-020 fixture table | 3 | T01 |
| T06 | Candidate facts derivation from snapshot incl. `full_name` join, tap formulae, dependents inversion, unjoinable handling (FR-010, 012) | 5 | T05 |
| T07 | Rules table + all branches + dependents threshold + property test "rules always win" (FR-030..032) | 5 | T06, S2 |
| T08 | Closure check loop with fake dry-run closures (FR-050) | 5 | T07, S1 |
| T09 | Hold ledger semantics: pin ownership, re-triage on version change, skipped-for-version, user pin never touched (FR-053, 054, 082) | 5 | T07 |

## Phase 3: storage and plumbing

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T10 | Holds store (atomic JSON files), crash-safety test (FR-071) | 3 | T09 |
| T11 | Brew adapter: subprocess runner (no shell, env, timeout, stdin devnull, name validation), parse with stderr-noise fixtures, snapshot, dry-run, pin/unpin (formula and cask), upgrade, deferred per-package cleanup (FR-004, 011, 051, 052, 057) | 8 | T01, S1, S5 |
| T12 | Cask rules inputs: auto_updates REPORT, pkg HOLD, running-app DEFER (FR-056) | 3 | T07, S2 |
| T13 | Audit JSONL writer + schema test + redaction (FR-070); vendored/adapted sanitizer | 3 | T01 |
| T14 | Run stamp and flock lock implementations (FR-002, 003) | 2 | T03 |
| T15 | Config loader (tomllib) with defaults and validation | 2 | T01 |

## Phase 4: triage chain

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T16 | Prompt builder: system/user separation, random delimiters, collision stripping, caps, redaction, injection fixtures (FR-045) | 5 | T13 |
| T17 | Output schemas (pydantic) + closed enums + claim-check (FR-043) | 3 | T07 |
| T18 | Chain logic with scripted `ModelClient` fakes: all four paths, thresholds, caps, disagreement, retry-once, degraded mode (FR-040..042, 046, 061, 062) | 8 | T16, T17 |
| T19 | Claude `ModelClient` adapter (structured output, refusal and error mapping, key from env) with SDK stubbed; one opt-in live test (FR-044) | 5 | T18, S6 |
| T20 | Ollama `ModelClient` adapter on stdlib urllib: preflight, `format` schema, `think:false`, keep_alive, timeouts; opt-in live test | 5 | T18 |

## Phase 5: surfaces and install

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T21 | launchd plist generator + install/uninstall (`launchctl bootstrap/bootout`), plist snapshot test, env and PATH assertions (FR-001, 004) | 5 | T04, S4 |
| T22 | Notifiers: say, osascript, command; fan-out swallow; sanitized phrases (FR-065, 072) | 3 | T01 |
| T23 | Review CLI interactive flow incl. closure display and service warning (FR-080, 081) | 5 | T09, T11 |
| T24 | (Stretch) OpenAI-compatible `ModelClient` for omlx / LM Studio | 3 | T18, S3 |

## Phase 6: verification

| ID | Task | Pts | Depends |
|----|------|-----|---------|
| T25 | End-to-end `run --dry-run` against the real machine (read-only calls only), compare predicted verdicts with the DESIGN section 5 hand-run | 3 | T11, T20 |
| T26 | Opt-in integration marker suite (real read-only brew, Ollama), docs: README with install and recovery steps (unpin, remove ledger, rollback kegs) | 3 | T25 |

## Checkpoints

- After Phase 2: rules plus ledger reproduce the hand-derived 13/4/3/1 split on the fixture snapshot.
- After Phase 4: adversarial-caveat fixtures and the "false leaf claim" fixture escalate rather than approve.
- After Phase 5: install on a throwaway label, verify a run via `launchctl kickstart`, then uninstall.

Totals: spikes 16 points, implementation 111 points (no task at or above 21).
