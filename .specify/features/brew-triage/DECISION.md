# DECISION: brew-triage

**Version:** 1.1 (Post-Delphi)
**Date:** 2026-11-17
**Changes:** Decision table updated for Delphi outcomes and owner decisions O1-O4; scope tightened (casks report-only); added "Post-Delphi changes" and v1.1 candidates.

Note: no `.specify/memory/constitution.md` exists in this repo. The user's global CLAUDE.md (TDD, consumer-defines-interface, fnox for secrets, structured logging, Fibonacci estimates, no emojis) acts as the constitution.

## Legend

- **[USER]** decided by the owner. **[PANEL]** settled by the Delphi review (unanimous or 4/5). **[DESIGNER]** designer's call, still open to challenge. **[DESIGNER-READING]** the designer's interpretation of an owner statement.

## 1. Use case

`brew update`, `brew upgrade`, `brew cleanup` should run automatically each morning. A human is supposed to review `brew outdated` first; in practice that never happens. Deterministic rules plus a small local model and Claude do the triage the human skips: upgrade what is clearly safe, hold what is not, report the holds.

## 2. Business value

- The machine stays current without reading `brew outdated`.
- Packages that bite (running services, major bumps, non-core taps, changed caveats) are held and surfaced.
- Every decision is audited (JSONL). The tool creates no pins; holds are an exclusion list the owner can see in `status`.
- Cost is near zero: rules settle most packages with no model call.

Observed workload (2026-11-17, Homebrew 7.0.14): 21-22 formulae and 1 cask outdated in plain mode (8 casks under `--greedy`, mostly stale metadata), 273 leaves, 30 formulae with a `service` block.

## 3. Decision table

| # | Decision | By | Status after Delphi |
|---|----------|----|---------------------|
| D1 | Autonomy "option 2": upgrade clearly safe, hold the rest, report. | USER | CHANGED by owner O1: HOLD = exclude from the explicit upgrade list and record in the ledger; the tool creates no pins. Existing user pins (redis, deliberate) are SKIP and never touched. [DESIGNER-READING of "we don't generally pin anything"] |
| D2 | Advisor escalation: small local model first, frontier Claude when judgment is needed; Claude path configurable; key via fnox | USER | REFINED by O2/O3: one local model (qwen3:4b) classifies caveats only and may add risk, never remove it; Claude (L3) is in v1, runs even when Ollama is down, and is the only stage that can approve a GREY case. glm-4.7-flash dropped |
| D3 | Python + uv + launchd | DESIGNER (accepted) | Stands. Plist runs the venv binary directly, `StartInterval` 3600, no `RunAtLoad` [PANEL] |
| D4 | Borrow phlegyas ideas | USER | Stands |
| D5 | Rules first and always win | DESIGNER | Hardened [PANEL]: models may only add risk, except L3 narrowly approving GREY |
| D6 | Per-category confidence caps | DESIGNER | REMOVED [PANEL]: models emit required risk booleans before the verdict plus a coverage check; fixed-outcome categories became rule HOLDs |
| D7 | Untrusted-input hardening | DESIGNER | Stands, plus deterministic `suspicious_text` flag, control-char stripping |
| D8 | `review` CLI | DESIGNER | Stands, slimmed: approve = upgrade component now; skip = per version |
| D9 | Pluggable notifications | DESIGNER | CHANGED [PANEL]: silent notification plus status file by default; `say`/Hue for urgent only; quiet hours; weekly digest |
| D10 | `brew cleanup` after upgrades | USER intent | Decided by owner O4: deferred after 2 full successful runs, never cleaning kegs in the live-process set |
| D11 | Escalation signals | DESIGNER | Replaced: no self-reported confidence; booleans + coverage + suspicious flag |
| D12 | Decision unit and closure | DESIGNER | CHANGED [PANEL]: undirected connected components; dry-run only a cross-check; no bounded drop-iteration |
| D13 | Pin ownership ledger | DESIGNER | REMOVED by O1 (no pins). Reconcile reduces to detecting user pins |
| D14 | Cask policy | DESIGNER | CHANGED [PANEL 5/5]: all casks report-only in v1 |
| D15 | Model interface | DESIGNER | Consumer-defined port; native Ollama adapter; OpenAI-compatible adapter CUT to v1.1 |
| D16 | Deferred cleanup | DESIGNER | Confirmed by owner O4 |
| D17 | Self-deps reported, not upgraded in-run | PANEL | New |
| D18 | No changelog fetching in v1 | DESIGNER | Stands; v1.1 candidate |
| D19 | Dependencies: urllib, argparse, tomllib, logging, pydantic, anthropic extra | DESIGNER | Stands. `fnox exec` wrapper removed; lazy `fnox get` |
| D20 | Live-process set (services + LaunchAgents/Daemons + running exes + dep closure) = HOLD | PANEL | New |
| D21 | Non-core tap = HOLD unless `trusted_taps`; soak `min_age_days` 3 for all packages | PANEL | New |
| D22 | Caveat diffing before any model | PANEL | New (baseline seeded at setup is DESIGNER) |
| D23 | Decided vs transient holds; reconcile; 48 h dead-man; hold aging | PANEL | New |
| D24 | Golden set (~25-30), shadow mode ~10 runs, ship gate 0 false approves | PANEL | New |

## 4. Approaches considered

1. **Python / uv / launchd (chosen).** Matches the user's handzoo and phlegyas stack; sanitizer and prompt-armoring ideas reuse directly; the rules layer is pure and testable; schema and retry handling are easy.
2. **bash + jq (rejected).** JSON schema enforcement, LLM retry/timeouts, escalation logic, and tests get painful once there is a judgment step.
3. **homebrew/autoupdate tap (rejected).** Blind upgrade with no triage hook, the option the user rejected; may borrow its launchd approach.
4. (Noted) C# is viable given the user's .NET background; Python chosen for phlegyas reuse.

## 5. Scope

### In v1
Formulae: update, snapshot, rules, components, caveat-diff triage, local classifier and Claude, apply, verify, deferred cleanup. Holds ledger, audit JSONL, `review`, `status`, `explain`, `eval`, `install`. Casks are reported only.

### Out of v1
Cask upgrades; OpenAI-compatible adapter; changelog fetching; automated rollback (README rollback procedure only); `mas`/pip/npm; non-macOS.

## 6. Post-Delphi changes (summary)

Five panelists (homebrew-ops, llm-judge, security, reliability, pragmatist), two rounds; verdict SHIP-WITH-CHANGES. Estimated v1 about 95-108 points versus 127 designed.

- Dropped: confidence/caps/threshold scheme; bounded drop-iteration and dry-run parsing as primary; glm advisor; OpenAI-compatible adapter; cask apply path; pin creation/ownership/unpin; `fnox exec` plist wrapper; two calendar slots plus `RunAtLoad`; `say` by default.
- Added: connected-component decision unit; live-process set; tap trust and soak; caveat diffing; boolean risk schema with coverage check; decided vs transient holds; reconcile and dead-man alarm; hold aging; golden set and shadow mode; env allowlist and lazy `fnox get`.
- Owner decisions: O1 exclude-never-pin; O2 Claude in v1 even when Ollama is down, fnox failure is transient; O3 one local model; O4 deferred cleanup after 2 full successful runs.
- Corrected: the redis pin is deliberate (redis stays installed while valkey runs in its place) and is not evidence of hold rot.

## 7. v1.1 candidates (design input recorded)

- **Cask apply path.** Panel S4 findings: pass `--no-quit` and DEFER when the app is running (brew quits running apps by default); naming a cask bypasses auto_updates protection; plain `outdated` already lists auto_updates casks while `--greedy` entries are stale Caskroom metadata; widen the HOLD rule from pkg-installer to installer/uninstall/flight artifacts.
- **OpenAI-compatible adapter** for omlx / LM Studio (verified for Ollama only; thinking control differs).
- **Changelog / release-note fetching** via `gh release view` for GitHub-hosted formulae, behind a flag, through the same sanitizer and suspicious-text flag.

## 8. Dependency evaluation

| Concern | Chosen | Alternatives | Why |
|---------|--------|--------------|-----|
| Ollama HTTP | stdlib `urllib.request` | `httpx` 0.28.1 (2024-12-06, red flag; `anthropic` 2.9.0 uses `httpx2`); `ollama` 0.6.3 | Built-in first; a handful of JSON POSTs |
| Validation / schema | `pydantic` 2.14.3 | dataclasses + manual; `msgspec` | Transitive dep of `anthropic`; emits JSON schema for both providers |
| Claude client | `anthropic` 2.9.0, optional extra | raw HTTP; OpenAI shim | Official SDK; optional so the core stays dependency-light |
| CLI | `argparse` | `click`; `typer` | A handful of subcommands |
| Config | `tomllib` | YAML; env only | stdlib |
| Logging | stdlib `logging` | `structlog`; `loguru` | no dep |
| Tests | `pytest` | `unittest` | phlegyas parity |
| Secrets | lazy `fnox get` | `fnox exec` wrapper in plist (removed, secret would sit in environment); Keychain CLI | Key never in `os.environ`; non-interactive behavior under launchd unverified (spike S4) |
| Scheduler | launchd `StartInterval` | cron; `brew autoupdate` | Retries come free; no RunAtLoad network race |

## 9. Risks accepted

- L3 approval of a GREY case that passed every rule remains a (narrow) model-dependent upgrade; mitigated by shadow mode and the ship gate.
- Rollback window is zero today; deferred cleanup helps only for packages that do not run live.
- Launchd jobs run only while the user is logged in.
