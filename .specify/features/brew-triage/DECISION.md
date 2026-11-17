# DECISION: brew-triage

**Version:** 1.0 (Pre-Delphi)
**Date:** 2026-11-17
**Changes:** Initial decision record

Note: no `.specify/memory/constitution.md` exists in this repo. The user's global CLAUDE.md (TDD, consumer-defines-interface, fnox for secrets, structured logging, Fibonacci estimates, no emojis) acts as the constitution for this feature.

## Legend: who decided what

- **[USER]** decided by the user. The Delphi panel may note risks but should not overturn these without going back to the user.
- **[DESIGNER]** a call made by the designing agent. Open to challenge.

## 1. Use case

`brew update`, `brew upgrade`, `brew cleanup` should run automatically each morning. In theory a human reviews `brew outdated` before upgrading; in practice that never happens, so upgrades are either skipped for months or run blind. A small local model (plus escalation) does the triage the human skips: it upgrades what is clearly safe, holds what is not, and reports the holds.

## 2. Business value

- Machine stays current without the human reading `brew outdated`.
- The packages that actually bite (services such as redis/rabbitmq/postgres, major bumps, caveat-bearing formulae) are held and surfaced instead of silently upgraded.
- Every decision is auditable (JSONL) and reversible via pin ledger.
- Cost is near zero: rules settle most packages with no model call; local models are free; Claude is the last resort.

Observed workload (2026-11-17, Homebrew 7.0.14): 21 formulae (4 are dependencies, not leaves) + 8 casks outdated under `--greedy` (1 under plain `brew outdated`), 273 leaves, 30 casks, 30 formulae with a `service` block. Hand-applying the DESIGN.md rules to the 21 formulae gives 13 UPGRADE, 4 GREY (needs a model), 3 HOLD, 1 SKIP (user-pinned redis). I.e. roughly 80 percent settled by rules alone. (Hand-derived, not executed.)

## 3. Decision table

| # | Decision | By | Open to challenge? |
|---|----------|----|--------------------|
| D1 | Autonomy "option 2": auto-upgrade clearly safe, HOLD (pin) the rest and report. Not recommend-only, not upgrade-all | USER | No |
| D2 | Advisor escalation modeled on Claude Code advisor pattern: small local model first pass, local larger model as advisor, frontier Claude when judgment is genuinely needed. Claude path configurable; key via fnox | USER | No (mechanics of escalation triggers are DESIGNER, see D11) |
| D3 | Platform: Python + uv + launchd | DESIGNER (user accepted) | Mildly |
| D4 | Borrow ideas from phlegyas (layered gate, post-hoc re-check, prompt armoring, JSONL audit, file queue, notifiers) | USER | No |
| D5 | Deterministic rules first and always win; models can never approve what rules held | DESIGNER | Yes |
| D6 | Per-category confidence caps | DESIGNER | Yes (values are tunable) |
| D7 | Untrusted-input hardening for caveats/descriptions (random delimiters, caps, system/user separation, structured output) | DESIGNER | Yes |
| D8 | `morning-brew review` CLI to approve/skip holds | DESIGNER | Yes |
| D9 | Notifications pluggable, never fail the job | DESIGNER | Yes |
| D10 | `brew cleanup` after upgrades. Mechanism (immediate vs deferred) is open, see D16 | USER intent, DESIGNER mechanism | Yes |
| D11 | Escalation uses more than self-reported confidence (see D17) | DESIGNER | Yes |
| D12 | Decision unit and closure check via `brew upgrade --dry-run` | DESIGNER | Yes |
| D13 | Pin ownership ledger (tool-owned vs user-owned pins; never touch user pins) | DESIGNER | Yes |
| D14 | Casks: `auto_updates true` casks are report-only (not upgraded, not pinned) in v1; non-auto-update casks go through the same pipeline; pkg-installer casks held | DESIGNER | Yes |
| D15 | Model interface: consumer-defined `Triage` port; native Ollama adapter v1; OpenAI-compatible adapter (omlx, LM Studio) is a stretch goal | DESIGNER | Yes |
| D16 | Cleanup deferred by N successful runs (default 2) to preserve a rollback window; config can set immediate | DESIGNER | Yes (strong candidate) |
| D17 | Escalate on: low confidence, any risk flag, failed claim-check, or L1/L2 disagreement | DESIGNER | Yes |
| D18 | v1 scope excludes changelog/release-note fetching (see section 5) | DESIGNER | Yes |
| D19 | Dependencies: stdlib `urllib` for Ollama, `argparse`, `tomllib`, `logging`; `pydantic` for schemas; `anthropic` as optional extra | DESIGNER | Yes |

## 4. Approaches considered

1. **Python / uv / launchd (chosen).** Matches the user's handzoo and phlegyas stack; phlegyas sanitizer/prompt-armoring/queue ideas reuse directly; the rules layer is pure functions, trivially unit-testable; LLM retry/schema handling is easy.
2. **bash + jq (rejected).** Adequate for the brew plumbing, but JSON schema enforcement, LLM retry/timeouts, escalation logic and tests all get painful once there is a judgment step.
3. **homebrew/autoupdate tap (rejected).** Blind upgrade with no triage hook, i.e. the option the user rejected. We may borrow its launchd approach.
4. (Noted, not scored) C# is viable given the user's .NET background; Python chosen for phlegyas reuse and uv single-file ergonomics.

## 5. Scope

### In v1
- Formulae and casks; `brew update` -> snapshot -> triage -> pin/upgrade -> verify -> deferred cleanup.
- Rules layer, version classifier, local first-pass + local advisor + Claude escalation, rules-only degraded mode.
- Holds store (also the review queue and pin-ownership ledger), JSONL audit log, `review` CLI.
- launchd plist generator/installer, logging, lockfile, notifiers.

### Out of v1 (explicit)
- **Changelog / release-note fetching.** Inputs are brew metadata only (version delta, caveats, service, dependents, deprecation flags, tap, desc). Rationale: (a) most of the risk signal is structural (major bump, service, dependents) and already in brew JSON; (b) release notes are a large untrusted-text injection surface; (c) fetching needs per-formula homepage/GitHub resolution (`urls.stable.url` is often a GitHub URL, observed for abctl) and `gh` auth under launchd. Plan: v1.1 optional `gh release view` for GitHub-hosted formulae, behind a flag, wrapped by the same sanitizer.
- Upgrading `auto_updates true` casks (they self-update; pinning does not stop that per `brew pin --help`). Reported only.
- `--greedy-latest` casks (`version :latest`). None installed today (checked: 0 of 30). Reported only if ever present.
- Mac App Store (`mas`), pip/npm/cargo, Brewfile management.
- Windows/Linux.
- Auto-rollback of failed upgrades.

## 6. Dependency evaluation (each vs at least two alternatives)

| Concern | Chosen | Alternatives | Why |
|---------|--------|--------------|-----|
| Ollama HTTP | stdlib `urllib.request` | `httpx` 0.28.1 (released 2024-12-06, about 2 years old: red flag by the user's rule; `anthropic` 2.9.0 now depends on `httpx2`, observed in PyPI `requires_dist`, not `httpx`); `ollama` python 0.6.3 (extra dep, thin wrapper) | Solution preference #1 (built-in). Calls are 3 JSON POSTs per run; no streaming needed |
| Output validation / JSON schema | `pydantic` 2.14.3 | stdlib dataclasses + hand-rolled validation; `msgspec` | Already a transitive dep of `anthropic`; generates the JSON schema for both Ollama `format` and Claude `output_config.format`; phlegyas already uses it |
| Claude client | `anthropic` 2.9.0 (released 2026-11-16), optional extra `[claude]` | raw `urllib` POST to `/v1/messages`; OpenAI-compat shim | Official SDK is the supported path (the `claude-api` skill forbids raw HTTP when an SDK exists); optional so the core has no network SDK |
| CLI | `argparse` | `click`; `typer` | Four subcommands; stdlib suffices |
| Config | `tomllib` (stdlib, 3.11+) | YAML (`pyyaml`); env vars only | No dep; matches `requires-python >= 3.11` |
| Logging | stdlib `logging` (+ small JSON formatter) | `structlog`; `loguru` | No dep; user rule: structured logging, no print in library code. Audit trail is a separate JSONL writer |
| Tests | `pytest` (dev) | `unittest` | Matches phlegyas; fixtures/parametrize suit classifier tables |
| Secrets | `fnox exec --` wrapper for the key | env in plist (rejected: secret on disk in plaintext); macOS Keychain `security` CLI | User convention. Whether `fnox exec` works non-interactively under launchd is UNVERIFIED (see DESIGN open items) |
| Scheduler | launchd LaunchAgent | cron (skips runs while asleep; man page contrast verified); `brew autoupdate` | See DESIGN section on launchd |

## 7. Risks accepted

- Small model mis-reasons (observed: qwen3:4b said "leaf = no dependencies" at 0.95 self-reported confidence). Mitigated by rules-win, claim-checking, two-model agreement, caps. Residual: grey-area upgrade that breaks something. Mitigation: deferred cleanup preserves rollback kegs.
- Upgrading while apps/services run.
- Launchd jobs only run in a logged-in user session.
