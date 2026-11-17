# DESIGN: brew-triage

**Version:** 1.0 (Pre-Delphi)
**Date:** 2026-11-17
**Changes:** Initial design

Authorship legend: [USER] decided by the user; [DESIGNER] open to Delphi challenge. Unless tagged [USER], everything below is a designer's call.

## 1. Evidence log (what was actually checked, 2026-11-17)

| Check | Result |
|---|---|
| `brew --version` | Homebrew 7.0.14-44-g8655ee4 |
| `brew outdated --json=v2` | 21 formulae + 1 cask. Plain mode excludes `auto_updates` casks |
| `brew outdated --json=v2 --greedy` | 21 formulae + 8 casks (7 with `auto_updates: true`) |
| `brew info --json=v2 --installed` | 533 formulae, 28 casks in info (cask count differs from the 30 in the brief; the join must tolerate mismatch) |
| `brew leaves` | 273. 5 of 21 outdated formulae are non-leaf dependencies (aws-c-auth, aws-c-s3, numpy, pkgconf, usage) |
| `brew services list --json` | entries with `name`, `status` ("none" or "started" ...), `user`, `file`, `exit_code` |
| `brew pin --help` | "Usage: brew pin [--formula] [--cask] installed_formula\|installed_cask"; note: "Pinned casks with auto_updates true may update themselves outside Homebrew" |
| `brew upgrade --help` | has `-n/--dry-run`, `--formula`, `--cask`, `--greedy-latest`, `--greedy-auto-updates`, `--greedy` |
| `man launchd.plist` | "launchd will start the job the next time the computer wakes up. If multiple intervals transpire before the computer is woken, those events will be coalesced into one event upon wake from sleep." Nothing is promised for power-off |
| Ollama | version 0.40.16 at :11434; native `/api/chat` with `format` JSON schema and `think:false` returned schema-valid JSON; cold model load 11.5 s (total 12.5 s, eval 0.9 s) |
| Ollama OpenAI-compat | `/v1/chat/completions` with `response_format: json_schema` returned valid JSON for qwen3:4b, but with thinking on by default (a `reasoning` field was returned). Verified for Ollama only; omlx and LM Studio NOT verified |
| Model quality probe | qwen3:4b given a nonexistent "leaf CLI" patch bump answered upgrade, confidence 0.95, with the false claim "leaf = no dependencies". Self-reported confidence is not a safe sole escalation signal |
| uv | 0.12.6 |
| PyPI | anthropic 2.9.0 (2026-11-16, requires `httpx2` not `httpx`, `pydantic>=1.10`); pydantic 2.14.3; httpx 0.28.1 (2024-12-06); ollama 0.6.3 |
| Claude API reference (claude-api skill) | model ids `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5` confirmed. On opus-5-5 / sonnet-5-5: forced `tool_choice` returns 400 (so use `output_config.format` structured output, not forced tool_use); thinking cannot be disabled on opus-5-5 (effort `low` is the lever; default effort is `medium`); non-default sampling params 400 on sonnet-5-5 (do not send temperature); `stop_reason == "refusal"` must be handled; server-side `fallbacks` is recommended for these models |

Phlegyas ideas reused (read at /Volumes/Repos/phlegyas/phlegyas): tier1 post-hoc re-check of AI approvals; tier2 deterministic allow patterns; tier2.5 hash-based trust (not reused; see section 12); tier3 random-delimiter armoring, system/user separation, 4000-char input cap, confidence caps by category, structured output; `sanitize.py` regex redaction (adapted, vendored, not imported); `file_queue.py` atomic per-item JSON files; `notifiers.py` fire-and-forget notifier that never raises.

## 2. Architecture overview

```
launchd --> morning-brew run
              |
              v
  Orchestrator (run_once)  <-- defines ports it consumes (ISP, consumer-defined)
     | BrewPort        update / snapshot / dry_run_closure / upgrade / pin / unpin / cleanup
     | Triage          decide(candidate_facts) -> TriageOutcome   (chain behind it)
     | HoldStore       get / put / list_tool_owned / mark
     | AuditSink       append(record)
     | Notifier[]      notify(summary)
     | Clock, Lock, RunStamp
```

Pipeline per run:

1. Acquire lock (flock). Check stamp. Preflight (brew present).
2. `brew update` (retry/backoff; failure aborts, FR-060).
3. Snapshot and join (section 3). Build `Candidate` list with facts.
4. Deferred cleanup of packages from older runs (FR-057).
5. Rules layer (section 5) -> verdicts; GREY set.
6. Triage chain over GREY (section 6), within time budget.
7. Closure check (section 7) -> final verdicts.
8. Apply: pin HOLDs (tool-owned, ledgered), unpin-and-upgrade previously held packages only if re-triaged to UPGRADE, upgrade UPGRADEs one at a time.
9. Verify via re-snapshot. Anomalies flagged.
10. Audit, summary, notify, write stamp, release lock.

## 3. Observed JSON shapes and the join

`brew outdated --json=v2 --greedy` (observed keys):
- `formulae[]`: `name`, `installed_versions[]`, `current_version`, `pinned`, `pinned_version`.
- `casks[]`: same keys.
- NOTE: for tap formulae `name` is the full name (`vectordotdev/brew/vector`, `jandedobbeleer/oh-my-posh/oh-my-posh`). Joining on short `name` loses tap and service data (this bug was made during the research probe: vector's service block was missed). **Join on `full_name`.**

`brew info --json=v2 --installed`:
- `formulae[]` (selected observed keys): `name`, `full_name`, `tap`, `desc`, `homepage`, `versions.stable`, `revision`, `service` (non-null object when the formula declares a service), `caveats`, `deprecated`, `disabled`, `deprecation_reason`, `pinned`, `outdated`, `keg_only`, `installed[]` with `version`, `runtime_dependencies`, `installed_on_request`, `time`; `dependencies`, `build_dependencies`.
- `casks[]`: `token`, `full_token`, `tap`, `desc`, `homepage`, `version`, `installed`, `outdated`, `pinned`, `pinned_version`, `auto_updates`, `caveats`, `artifacts`, `depends_on`, `deprecated`, `disabled`.
- Installed dependents are computed locally by inverting `installed[].runtime_dependencies` (no extra `brew uses` subprocess).

`brew leaves`: newline-separated short names (leaf = not required by another installed formula).
`brew services list --json`: `[{name, status, user, file, exit_code}]`. Presence of a `service` block in info means the formula can run as a service; `status != "none"` means it is registered/started. Both are consulted (FR-030 rule 6 vs 11). Observed: caddy/cockroach/dnsmasq status "none"; etcd "started"; 30 formulae have a `service` block.

Join key summary: formula = `full_name`; cask = `token` (outdated cask `name` equals token). A candidate that cannot be joined becomes HOLD "unjoinable", never dropped.

Artifacts shape for casks (pkg vs app, process name for running-app check) was NOT inspected in the research pass; Plan task S2 is a read-only spike to confirm before FR-030 rule 4 and FR-056 are implemented.

## 4. Domain model

```
Candidate(kind: formula|cask, full_name, installed: str, current: str,
          bump: Bump, is_leaf, dependents: int, service_defined, service_status,
          caveats: str|None, deprecated, disabled, tap, pin: PinState(pinned, owner),
          auto_updates, artifact_kinds, version_is_latest)
Verdict = UPGRADE | HOLD | SKIP | DEFER | REPORT | GREY
Decision(candidate, verdict, rule_id, stage, reasons[], confidence_raw, confidence_clamped, models[])
HoldRecord(full_name, kind, held_at_version, reason, owner="tool", pinned_by_tool: bool,
           advisor_reasoning, state: held|skipped_for_version|released, created, updated)
```

Bump classifier (`versions.py`): normalize `1.5.9,4515810895593472` by taking the part before the comma; strip a trailing `_N` (brew revision) and `-N` (build) into `revision`; split dotted numerics; if any non-numeric segment remains (for example `stable_02`) return `unparseable`. Same numeric tuple with different revision -> `revision`. Differing last component -> `patch`; second -> `minor`; first -> `major`. If major == 0 and minor differs -> `zero_major_minor`. Calver strings that happen to be dotted numerics (ballerina `2201.14.1`) classify like semver; this is a known imprecision, the safe direction is preserved because the first component rarely changes. Fixture table in SPEC FR-020.

## 5. Rules layer

Rule table (ordered) lives in `rules.py` as data: `Rule(id, predicate, verdict, cap)`. See SPEC FR-030/031 for the order. Rationales:

- User pin first: respects the user's explicit hold (redis is user-pinned in observed data).
- Major bump HOLD: breaking changes and licence/data-format risk (redis 7.2.7 -> 8.10.3 is exactly this).
- Service rule: hold when `service_status != none`. A formula that declares a service but is not registered (caddy: none) is only GREY, because upgrading it cannot restart or reformat a live data store. Delphi question 7.
- Caveats GREY: caveats are the maintainers' own "read this" signal (observed on awscli, imagemagick, numpy, rabbitmq, antigravity-cli, miniforge).
- 0.x minor GREY: semver says anything may change at 0.y; this keeps noise low versus HOLD (grype, vector).
- Deprecated GREY, disabled HOLD (cannot be upgraded meaningfully; report).

Hand-run on the 21 observed formulae (illustrative): UPGRADE 13 (aws-c-auth, aws-c-s3, ballerina, calm-cli, docker, fnox, pkgconf, skopeo, specify, summarize, syft, usage, werf), GREY 4 (awscli, grype, imagemagick, numpy), HOLD 3 (oh-my-posh major; rabbitmq and vector service, assuming started), SKIP 1 (redis user-pinned). Casks: 7 auto-updating REPORT; antigravity-cli GREY (caveats).

## 6. Triage chain

Port (defined by the orchestrator, ISP):

```python
class Triage(Protocol):
    def decide(self, request: TriageRequest) -> TriageOutcome: ...
```

`TriageOutcome(verdict: upgrade|hold, confidence, risk_flags, facts_used, rationale, stage, model_id, unsure: bool)`. The chain (L1 -> L2 -> L3) is an internal implementation of `Triage`, composed from `ModelClient` ports each defined by the chain: `ModelClient.ask(system, user, schema) -> dict`.

Stages [USER]: L1 small local (default `qwen3:4b`), L2 local advisor (default `glm-4.7-flash`, 19 GB, fits in 64 GB), L3 Claude (default `claude-sonnet-5-5`; `claude-opus-5-5` or `claude-haiku-5-5` selectable). Unsure -> next stage; terminal unsure -> HOLD.

Escalation signal [DESIGNER] (because self-reported confidence is poorly calibrated):
1. confidence below 0.8 after cap clamp, or
2. any `risk_flags` (closed enum: `breaking_change_possible`, `data_format_change`, `runtime_restart_needed`, `many_dependents`, `unclear`, `other`), or
3. claim-check failure: `facts_used` is a closed enum (`leaf`, `no_dependents`, `patch_bump`, ...); each is verified against the real facts, mismatch -> unsure, or
4. L1 and L2 verdicts disagree (L2 is shown L1's verdict and rationale as untrusted context; L3 sees both), or
5. invalid schema after one retry, or timeout.
Agreement of two independent stages with both above threshold is required to auto-approve a GREY candidate when L3 is not consulted: L1 `upgrade` alone is never enough in categories with cap < 1.0 (all GREY categories have caps, so a GREY upgrade always needs at least two stages agreeing, or L3 alone deciding). Delphi question 3.

Prompt armoring (phlegyas tier3 pattern): system prompt contains rules and schema description; user message contains only a fixed-format facts block plus the untrusted fields wrapped in `<untrusted-{secrets.token_hex(8)}>` tags; any occurrence of the delimiter in input is stripped; each untrusted field is capped (2000 chars) and passed through the redactor first. The model is told the wrapped text is data. Output is schema-constrained, so there is no free-form parse path.

Ollama adapter (v1): POST `/api/chat`, `stream:false`, `think:false`, `format: <JSON schema from pydantic>`, `options:{temperature:0}`, `keep_alive:"5m"` and `0` on the final call to release memory. Per-call timeout 120 s (covers 11.5 s cold load); preflight GET `/api/version` 2 s. Transport: stdlib `urllib.request`.

Claude adapter (v1): `anthropic` 2.9.0, `client.messages.parse(...)` / `output_config.format` JSON schema (no forced tool_choice: it 400s on opus-5-5/sonnet-5-5), no sampling parameters, `output_config.effort="low"` (opus-5-5 cannot disable thinking), small `max_tokens`, handle `stop_reason=="refusal"` and typed errors (most-specific first). Include the server-side `fallbacks` beta per the API reference recommendation (open item: confirm exact form against live docs at implementation, the research pass did not run the SDK). Key via `fnox exec -- morning-brew run`; no key -> L3 disabled.

OpenAI-compatible adapter (stretch): one more `ModelClient` implementation for omlx and LM Studio using `/v1/chat/completions` + `response_format: json_schema`. Verified only against Ollama; thinking control differs (Ollama native uses `think:false`). Ship only after verifying omlx/LM Studio endpoints (Plan task S3).

Why not make the OpenAI-compatible endpoint the single interface: it works on Ollama but thinking suppression and structured-output behavior for omlx/LM Studio are unverified, and the consumer-defined `ModelClient` port makes it a one-file adapter later.

## 7. Dependency closure and the post-hoc check (Tier-1 analogue)

Decision unit: the set of packages `brew` will actually change. Procedure:

1. Approved set A = UPGRADE verdicts (rules + model).
2. `brew upgrade --dry-run --formula <A...>` (and `--cask` separately) -> parse the closure C (plan task S1 spikes the output format; fall back to computing the closure from `runtime_dependencies` if parsing proves brittle).
3. For each member of C, re-run the rules (without models). If any member is HOLD/SKIP/REPORT or unjoinable, drop the A members that pull it in, record reason, and repeat (bounded loop).
4. Only the final A is upgraded, individually, leaves first.

Holding a dependency: pinning a dependency can break its dependents' upgrades, so the closure check also runs the other way: a dependent whose newer version requires a held dependency is held with reason "blocked by pinned dependency" (detected when the dry-run fails or reports a pin conflict). Delphi question 1.

## 8. Pins, holds store, casks

Holds store directory `~/.local/state/morning-brew/holds/<safe-name>.json`, written atomically (temp file + rename, phlegyas file_queue pattern). One file doubles as review queue and pin ledger. `pinned_by_tool` is true only if the package was unpinned when the tool pinned it; if `pinned` was already true, the record is SKIP and tool never writes a pin or ledger claim.

Lifecycle: absent -> held (pin) -> {approved -> released (unpin + upgrade), skipped_for_version(v) , re-triaged when current_version != held_at_version}.

Never-touch rule: `unpin` is only issued for records with `pinned_by_tool: true`.

Casks:
- `brew pin --cask` exists in 7.0.14 (help text; `pinned`/`pinned_version` fields exist on both outdated and info cask JSON). Pin semantics were NOT exercised live (research pass was read-only).
- `auto_updates: true` casks (7 of 8 outdated): pinning is documented as ineffective against self-update, and they only appear with `--greedy`. v1 policy: REPORT, no pin, no upgrade ([DESIGNER]; Delphi question 4 asks whether to upgrade them with `--greedy-auto-updates`).
- `version :latest` casks: `--greedy-latest` off in v1.
- pkg-installer casks may require sudo and would hang or fail under launchd: HOLD by rule (needs artifact check, S2).
- Running apps: DEFER (FR-056). Terminal apps (ghostty, warp) are auto-updating and so are REPORT anyway.

## 9. Apply and verify

- Never `brew upgrade` without arguments (FR-051). Commands run with an explicit env, `stdin=DEVNULL`, timeouts (default 30 min per package), `shell=False`, names validated against `^[A-Za-z0-9@._+\-/]+$`.
- Per-package result recorded; failure keeps going; failed package is not auto-pinned, retried next run.
- Verify via a second snapshot (FR-055).
- `HOMEBREW_NO_INSTALL_CLEANUP=1` stops `brew upgrade` from cleaning implicitly, so cleanup policy is ours alone (see 11).

## 10. Failure-mode matrix

| Failure | Detection | Behavior | Exit | Notification |
|---|---|---|---|---|
| `brew update` fails / no network / DNS down on wake | non-zero exit or timeout | retry with backoff (3 attempts), then abort before any mutation | 75 | yes: "update failed, will retry on next schedule" |
| brew present but JSON unparseable | JSONDecodeError | abort run, log stdout/stderr tails | 1 | yes |
| Third-party tap stderr noise (ngrok `postflight`) | stderr non-empty | ignored for control flow; logged debug | n/a | no |
| Candidate not joinable across outputs | missing key | HOLD "unjoinable" | 0 | in summary |
| Ollama not running | preflight GET fails | rules-only: GREY -> HOLD "model unavailable"; Claude only if `claude_when_local_down` | 0 | summary flags "degraded" |
| Ollama cold load slow | call duration | per-call timeout 120 s; run budget 20 min | 0 | no |
| Model returns invalid schema | pydantic validation | one retry, then unsure -> next stage | 0 | no |
| Model contradicts facts | claim-check | unsure -> escalate | 0 | audit only |
| Claude key missing | fnox/env absent | L3 disabled, logged | 0 | summary flags |
| Claude unavailable / rate limited / refusal / timeout | typed SDK errors, `stop_reason` | unsure -> HOLD | 0 | summary |
| Partial upgrade failure | non-zero exit per package | record failed, continue, retry next run | 0 (1 if all failed) | yes, names packages |
| Held package changed as side effect | post-verify | anomaly record, error log | 0 | urgent |
| Concurrent run | flock fails | exit immediately | 75 | no |
| Run already succeeded this slot | stamp | no-op | 0 | no |
| Notifier raises | exception in adapter | swallowed and logged | unchanged | n/a |
| Machine asleep at schedule | launchd | coalesced run on wake (man page); stamp + RunAtLoad cover power-off | n/a | n/a |
| Network not yet up at wake | update failure | covered by retry + second schedule fire | 75 | yes |
| sudo prompt (pkg cask) | pre-empted by rule | HOLD; subprocess has no stdin and times out otherwise | 0 | summary |
| Disk full / state dir unwritable | OSError | abort before mutation if audit/holds cannot be written | 1 | say fallback |

## 11. Cleanup decision (D16)

`brew cleanup` removes the previous keg versions of formulae, deleting the rollback path for today's upgrades. User asked for cleanup after upgrades; the designer proposes the deferred form: per-package `brew cleanup <pkg>` for packages upgraded at least N successful runs ago (default N=2), while `HOMEBREW_NO_INSTALL_CLEANUP=1` prevents implicit cleanup. `cleanup.mode = "immediate"` gives the literal behavior. Alternative: also bare `brew cleanup -s`/`--prune` for download cache only (cache pruning does not remove kegs). Delphi question 5.

## 12. launchd

- Plist `~/Library/LaunchAgents/<label>.plist` generated by `morning-brew install`: `Label`, `ProgramArguments` = [fnox-or-direct wrapper, uv-run command], `StartCalendarInterval` as an array of two dicts (morning slot, later fallback slot), `RunAtLoad` true, `StandardOutPath`/`StandardErrorPath` = `~/Library/Logs/morning-brew/`, `EnvironmentVariables` with explicit PATH (`/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin`), `ProcessType` Background, `LowPriorityIO` true.
- Semantics (verified from man page): missed fire while asleep runs on wake, multiple missed intervals coalesce into one. Not verified and not claimed: catch-up after full power-off (RunAtLoad + stamp guard cover it at next login). StartCalendarInterval and StartInterval are independent.
- A LaunchAgent only runs while the user is logged in (general launchd behavior, not tested here); `say` also needs the user session.
- Retry on early-wake no-network is handled in-process (backoff) and by the later schedule fire; the stamp prevents duplicates.
- Install/uninstall via `launchctl bootstrap|bootout gui/<uid>`.
- Open: `fnox exec` non-interactive behavior under launchd (keychain/age unlock) is unverified. If it fails the tool runs without L3 and says so.

## 13. Notifications

`Notifier` port: `notify(Summary) -> None`, never raises. Adapters: `SayNotifier` (`say` via argv; phrase built from counts, not raw text), `OsaScriptNotifier` (phlegyas pattern, escaped), `CommandNotifier` (user-supplied argv, summary JSON on stdin). The Hue/home-notify path is reachable from Claude Code through an MCP bridge; whether a plain launchd Python process can reach it is unverified, so it is only expressible via `CommandNotifier` pointing at a user script. Fan-out wrapper swallows exceptions.

## 14. Configuration

`~/.config/morning-brew/config.toml` (tomllib): stages and model ids, thresholds, caps, budgets and timeouts, `claude_when_local_down`, cleanup mode and N, notifiers, schedule times, state dir. Defaults live in code. Secrets are never in config.

## 15. Package layout (proposed)

```
src/morning_brew/
  cli.py                  # argparse: run | review | status | explain | install | uninstall
  orchestrator.py ports.py domain.py versions.py rules.py closure.py
  brew/{runner.py,parse.py,snapshot.py}
  triage/{chain.py,prompt.py,schemas.py,claims.py,ollama.py,claude.py}
  store/{holds.py,audit.py,stamp.py,lock.py}
  notify.py sanitize.py config.py launchd.py
tests/ (fixtures: sanitized captures of the real JSON shapes above)
```

## 16. Security notes

- Subprocess: argv lists only, validated names, no shell, no inherited secrets other than what the stage needs.
- Untrusted text never flows into subprocess args, `say`, or logs unsanitized.
- API key never logged; audit redaction applies to rationale text.
- Models cannot approve anything rules held; a compromised/misled model's worst case is a GREY-category upgrade of a patch/minor non-service package.

## 17. Open items needing a spike (not resolved by research)

S1 `brew upgrade --dry-run` output format; S2 cask `artifacts` shape (pkg vs app, process names); S3 omlx / LM Studio structured output behavior; S4 `fnox exec` under launchd; S5 live `brew pin --cask` behavior on a throwaway cask (needs user consent); S6 Anthropic SDK 2.x structured-output call and `fallbacks` form against live docs.
