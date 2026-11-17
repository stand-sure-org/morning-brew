# DESIGN: brew-triage

**Version:** 1.1 (Post-Delphi)
**Date:** 2026-11-17
**Changes:** Folds in the 2-round, 5-panelist Delphi review (`reviews/delphi-2026-11-17.md`) and the four owner decisions. Removed: confidence/caps/threshold scheme, bounded drop-iteration with dry-run parsing as primary, glm advisor stage, OpenAI-compatible adapter, cask apply path, all pin creation/ownership/unpin machinery, `fnox exec` plist wrapper, two calendar slots plus RunAtLoad, `say`-by-default. Added: connected-component decision unit, live-process set, tap trust and soak, caveat diffing, boolean risk fields with coverage check, decided/transient holds, reconcile and dead-man alarm, hold aging, golden set and shadow mode, lazy `fnox get` with env allowlist.

Authorship legend: [USER] decided by the user; [PANEL] settled by the Delphi panel (unanimous or 4/5); [DESIGNER] designer's call, still open to challenge. Items marked **[DESIGNER-READING]** are the designer's interpretation of an owner statement and may be corrected by the owner.

## 0. Owner decisions (2026-11-17) [USER]

| # | Decision | Effect |
|---|----------|--------|
| O1 | "We don't generally pin anything." [DESIGNER-READING of the owner's words] HOLD = excluded from the explicit upgrade list and recorded in the tool's ledger and status. The tool creates no pins, ever. Existing user pins (redis, deliberate: kept installed while valkey runs in its place) are SKIP and never touched | Replaces D1's "pin" wording. Removes pending_pin journal, pin ownership, unpin-on-release |
| O2 | Claude (L3) is in v1, and runs even when Ollama is down. fnox fetch failure is a transient hold (retried), never a decided hold | L3 is the only stage that can approve a GREY case |
| O3 | One local model (qwen3:4b), caveat classification only. glm-4.7-flash dropped. Claude is the advisor | Chain is local classifier then Claude |
| O4 | Deferred cleanup after 2 full successful runs, never cleaning kegs in the live-process set | Rollback window is zero today anyway |

Carried over: autonomy "option 2" (auto-upgrade clearly safe, hold the rest, report), Python/uv/launchd, borrow from phlegyas, Claude path configurable with the key in fnox.

## 1. Evidence log

Own research (2026-11-17, read-only): Homebrew 7.0.14; `brew outdated --json=v2` (21 formulae, 1 cask; `--greedy` 8 casks); `brew info --json=v2 --installed` (533 formulae, 28 casks); `brew leaves` 273; `brew services list --json`; `brew pin --help` and `brew upgrade --help`; `man launchd.plist` (missed fire during sleep runs on wake and coalesces; nothing promised for power-off); Ollama 0.40.16 `/api/chat` with `format` + `think:false` valid, cold load 11.5 s; PyPI: anthropic 2.9.0 (2026-11-16, depends on `httpx2`), pydantic 2.14.3, httpx 0.28.1 (2024-12-06), ollama 0.6.3; uv 0.12.6; Claude API reference (model ids `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`; no forced tool_choice on opus-5-5/sonnet-5-5, use `output_config.format`; opus-5-5 cannot disable thinking; refusal stop_reason).

Panel-verified evidence (Delphi, read-only, see `reviews/delphi-2026-11-17.md` section "Key evidence"; cited here by number E1..E10, raw files in `reviews/delphi-round1|2/`):
- E1 Upgrade closure is bidirectional: `brew upgrade aws-c-auth --dry-run` also upgrades dependents aws-c-s3 and awscli; `brew upgrade awscli` pulls its deps (`upgrade.rb:300-307`). A pinned outdated dependency makes dependents uninstallable (`formula_installer.rb:488-512`); a pinned broken dependent is not repaired (`upgrade.rb:445-455`).
- E2 `brew services` under-reports live services: vector and nats-server show `none` but run from user LaunchAgents (`com.logshipping.vector`, `anderson.nats-server`); vector runs from the versioned keg `Cellar/vector/0.61.0`; rabbitmq's beam.smp runs from `Cellar/erlang/29.1.2`; grafana shows `started` with exit_code 1. Started services: etcd, grafana, loki, prometheus, rabbitmq, valkey.
- E3 Brew never restarts upgraded services; new code first starts at next login/reboot.
- E4 Casks: plain `outdated` already includes stale auto_updates casks; `--greedy` entries are stale Caskroom metadata (ghostty bundle already 1.4.0); naming a cask bypasses auto_updates protection; brew quits running apps by default (`--no-quit` needed). Plain mode lists 1 outdated cask.
- E5 Local model probe (directional, small n): qwen3:4b returned "upgrade ~0.95" for a synthetic data-loss caveat and confidence 1.0 for an injected instruction, hallucinated `leaf`/`service_defined` on most cases, risk flags on nearly all samples. glm-4.7-flash returned invalid JSON on dangerous/injected cases, ran >300 s uncapped, 6 of 10 truncated at num_predict 600. `think:true` + `format` on qwen never finished.
- E6 Ollama listens on `*:11434`; model tags are mutable via `/api/create`.
- E7 Taps: 51-55 installed, but only 3 of 22 outdated formulae come from them (oh-my-posh, smartthings, vector).
- E8 Rollback window today is zero: no formula has more than one keg; casks keep one version; no `brew switch`.
- E9 (Corrected by owner) the redis pin is deliberate and is NOT evidence of hold rot. Hold aging and the dead-man alarm stand on their own merits.
- E10 The tool's own toolchain (fnox, python@3.13/3.14) is in the current upgrade set; Homebrew self-updated 7.0.14-44 to -67 during the review.

Phlegyas ideas reused: post-hoc re-check of AI approvals, deterministic patterns first, random-delimiter armoring, system/user separation, input caps, structured output, redaction regexes (adapted and vendored, not imported), atomic per-item JSON files, never-raising notifiers.

## 2. Architecture

```
launchd (StartInterval 3600) --> .venv/bin/morning-brew run
   Orchestrator.run_once  (defines the ports it consumes; ISP)
     BrewPort        update / snapshot / upgrade(components) / cleanup(pkgs) / dry_run(cross-check)
     LiveProcessPort live-process set (services, LaunchAgents, running exes)
     Triage          classify(caveat facts) -> outcome   (local classifier + Claude behind it)
     HoldStore       decided/transient holds, first-seen, caveat hashes
     AuditSink  Notifier[]  StatusFile  Clock  Lock  RunStamp
```

Run pipeline (SPEC FR-005; "triage completes before apply" is a tested invariant):

1. Lock, then schedule gate: not before `earliest_hour`, and no second full run in the same calendar day (stamp). Dead-man check and reconcile (section 9).
2. `brew update` (bounded retry). Failure: abort before any mutation, exit 75 (a log marker only; the next hourly fire is the retry).
3. Snapshot and join (section 3); build facts; update first-seen ledger.
4. Deferred cleanup of kegs from runs at least 2 full successful runs old, excluding the live-process set.
5. Rules (section 5) per package, then connected components (section 6).
6. Triage of GREY components' members (section 7), within the run budget.
7. Apply: for each ELIGIBLE component, one `brew upgrade -- a b c`; verify by re-snapshot.
8. Audit, status file, summary, notifications, stamp (`quality: full|degraded`).

## 3. Snapshot, shapes, join

- Formulae/casks outdated: `brew outdated --json=v2` (plain, NOT `--greedy`; E4: greedy entries are stale metadata). Observed keys: `name`, `installed_versions[]`, `current_version`, `pinned`, `pinned_version`. Tap formulae appear by full name (`vectordotdev/brew/vector`).
- `brew info --json=v2 --installed`: formula keys include `name`, `full_name`, `tap`, `desc`, `homepage`, `versions.stable`, `bottle` (`{}` for source-built, e.g. abctl), `service`, `caveats`, `deprecated`, `disabled`, `pinned`, `installed[].runtime_dependencies`, `installed_on_request`. Cask keys include `token`, `auto_updates`, `artifacts`, `caveats`.
- `brew leaves`; `brew services list --json` (`name`, `status`, `user`, `file`, `exit_code`).
- Join on `full_name` (formulae) / `token` (casks). The info cask list had 28 entries versus 30 casks in the owner's count, so the join tolerates mismatches: an unjoinable candidate is a transient hold "unjoinable", never dropped.
- Dependents are computed by inverting `runtime_dependencies` locally.
- brew stderr (for example the ngrok `postflight` warning) is logged at debug and never affects parsing. `brew --version` is recorded in every audit record. `HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK` is never set.

## 4. Version classifier

Unchanged from v1.0: strip the part after a comma; trailing `_N` and `-N` are `revision`; dotted numerics compare to patch/minor/major; any non-numeric segment (`stable_02`) or `latest` is `unparseable`; 0.x with differing minor is `zero_major_minor`. Fixture table in SPEC FR-020.

## 5. Rules layer (first match wins; table is data)

Verdict set: UPGRADE-ELIGIBLE, HOLD, SKIP, DEFER, REPORT, GREY. A package-level HOLD or SKIP propagates to its whole component (section 6).

1. Name fails validation (leading `-`, `..`, outside `^[A-Za-z0-9@._+/\-]+$` after the first char) -> HOLD (transient, reason "invalid name").
2. User-pinned (`pinned: true` on outdated/info) -> SKIP. Never touched, never unpinned. (A pinned member makes its component SKIP, since a pinned outdated dependency makes dependents uninstallable, E1.)
3. Casks -> REPORT (all casks, v1). Includes stale auto_updates casks listed by plain `outdated`.
4. `disabled` -> HOLD.
5. Self-dependency of the tool (fnox, python of the tool's venv, brew itself) -> REPORT "upgrade manually"; its component is DEFER (the tool never upgrades what it runs on in-run).
6. Non-core tap and not in `trusted_taps` -> HOLD. `trusted_taps` is seeded once at `install`/setup by the owner.
7. No arm64 bottle (empty or missing `bottle`) -> HOLD.
8. In the live-process set (section 8), or in the runtime-dependency closure of a live-process member -> HOLD.
9. Major bump -> HOLD. `unparseable`, `deprecated`, high-dependent dependency (10 or more installed dependents) -> HOLD (fixed outcomes need no model).
10. Soak: current version first seen less than `min_age_days` (default 3) ago -> DEFER. Applies to all packages.
11. `zero_major_minor` or changed caveats (hash differs from the hash recorded at last upgrade; section 7) -> GREY.
12. Otherwise (revision/patch/minor, no flags, unchanged-or-absent caveats) -> UPGRADE-ELIGIBLE.

Caveat baseline [DESIGNER, unsettled]: at setup, the tool records the hash of every currently installed package's caveats as "already seen"; unchanged boilerplate is then not a signal (S10). Without seeding every caveat-bearing package would be GREY on the first run.

Hand-run (illustrative, not executed) on the 21 formulae observed in the research pass, in steady state after the soak window; edges beyond the aws trio were not inspected, so component membership elsewhere is unknown and numpy/imagemagick may or may not join a component with a deferred self-dependency (python).

| Package | Outcome | Rule |
|---|---|---|
| redis | SKIP | user pin (deliberate; valkey runs in its place) |
| rabbitmq | HOLD | started in brew services (and its erlang dependency is live: beam.smp runs from `Cellar/erlang`) |
| vector | HOLD | live-process set via user LaunchAgent `com.logshipping.vector` (brew services says `none`) and non-core tap unless trusted |
| oh-my-posh | HOLD | non-core tap (and major bump 32 -> 33) |
| fnox | REPORT | self-dependency |
| aws-c-auth, aws-c-s3, awscli | one component, ELIGIBLE if caveats unchanged | component, E1 |
| grype | GREY | zero_major_minor (0.121.3 -> 0.122.0) |
| ballerina, calm-cli, docker, pkgconf, skopeo, specify, summarize, syft, usage, werf | ELIGIBLE | revision/patch/minor |
| imagemagick, numpy | ELIGIBLE or GREY | depends on caveat hash and component membership |

First sighting of every version is DEFER until the soak window passes, so a first run upgrades nothing from this list.

Live-service set today (E2): started services are etcd, grafana, loki, prometheus, rabbitmq, valkey; vector and nats-server are live via user LaunchAgents although services say `none`.

## 6. Decision unit: connected components [PANEL]

Build the undirected graph over outdated formulae with edges from `runtime_dependencies` (after name validation). One HOLD, SKIP, DEFER or REPORT member makes the whole component non-eligible, with the member named in the reason. Each eligible component is upgraded in a single explicit `brew upgrade -- a b c`. `brew upgrade --dry-run` is retained only as a cross-check: if its closure includes a package outside the component or any non-eligible package, the component becomes a transient hold ("closure mismatch") and an anomaly is audited. No iterative dropping; dry-run output is not parsed as the primary mechanism. Bare `brew upgrade` is never run; `--` always precedes package arguments; an explicit upgrade of a pinned package is a pin conflict (not retried).

## 7. Triage: caveat diffing, then the chain

Caveat diffing is built first. Packages whose caveats are absent or hash-identical to the last-upgrade hash never reach a model. The model chain is sized from what is left (the GREY set). Fixed-outcome categories (unparseable, deprecated, high-dependent, live service) are rule HOLDs with zero model calls.

Chain, for GREY members only [USER O2, O3; PANEL D4, D5]:

1. Deterministic `suspicious_text` flag on untrusted text (instruction-like phrases, delimiter lookalikes, control characters, over-length). Flag set -> HOLD (decided), no model call.
2. Local classifier (qwen3:4b), optional: classifies caveat text into required booleans `mentions_migration`, `mentions_data_format`, `mentions_restart`, `mentions_manual_step`, `boilerplate_only`, emitted BEFORE `verdict` in the schema. **[USER, post-v1.1 approval]** Local output never causes an upgrade and never causes a hold: it is a recorded second opinion. A local risk flag routes the member to Claude exactly as an all-clear does; Claude still judges blind. When local and Claude disagree (local flagged, Claude cleared), the run summary and `status` surface it as "local flagged, Claude cleared", with the local reasoning labeled as derived from untrusted text. Rationale: qwen3:4b flagged risk on nearly every probe sample (E5), so a local veto would hold almost every caveat-bearing GREY case unseen by Claude and feed hold rot. Its value is measured by the golden set (T16) and the agreement rate in the audit log; if it adds nothing, drop it in v1.1. `think:false`, `num_predict` about 400, truncated or invalid JSON (one retry) -> unsure, which skips the local stage and goes to Claude with the deterministic flags (not a hold).
3. Claude (L3), judging blind (facts and sanitized caveat only, never the local stage's reasoning). Schema: the same required booleans first, then `verdict: upgrade|hold`. Accepted as UPGRADE only if: all risk booleans false (or `boilerplate_only` true), `verdict == upgrade`, and the deterministic coverage check passes (a keyword scan of the caveat finds migration/data/restart/manual cues; each cue found must be reflected by the corresponding boolean). Any miss -> HOLD (decided) or unsure -> transient hold. No confidence field, no thresholds or caps.
4. L3 is the only stage that may turn GREY into UPGRADE. No model may turn a HOLD, SKIP, DEFER or REPORT into UPGRADE. L3 runs even when Ollama is down. fnox fetch failure, refusal, timeout, rate limit -> transient hold.
5. Mode: until the ship gate (section 11) is passed and the owner flips `chain.mode = live`, the chain runs in shadow mode: GREY members are held (transient, reason "shadow") while the would-be verdicts are audited.

Prompt armoring: system prompt carries instructions; user message carries a fixed facts block plus untrusted fields wrapped in `<untrusted-{token_hex(8)}>` tags with collision stripping, 2000-char caps, redaction. Printed model reasoning is stripped of ANSI/control characters and labeled "derived from untrusted text".

Adapters: Ollama native `/api/chat` over stdlib `urllib` (`format` schema, `think:false`, keep_alive, 120 s per-call timeout for cold load). Model digests are pinned from `/api/tags`; digest mismatch -> local stage disabled, rules-only plus warning (Claude still runs). Warn if Ollama is bound off loopback (E6); the client only connects to loopback. Claude via `anthropic` 2.9.0 using `output_config.format` JSON schema (no forced tool_choice), no sampling params, `output_config.effort="low"`, refusal and typed errors handled most-specific-first. Default model `claude-sonnet-5-5`, configurable. Server-side `fallbacks` is a spike item (S6). No OpenAI-compatible adapter in v1.

## 8. Live-process set [PANEL]

Union of: (a) brew-services status other than `none`; (b) formulae whose keg paths (opt/Cellar/bin, symlinks followed) match `ProgramArguments[0]` of any LaunchAgent/LaunchDaemon plist (user and system locations, parsed with `plistlib`); (c) formulae whose executables are currently running (path-matched against process list); plus the runtime-dependency closure of each member. All are HOLD and their old kegs are never cleaned by the tool. "Upgraded but old binary still running" is an expected status (E3), surfaced in `status`, and brew never restarts the service.

## 9. State, holds, reconcile

State dir `~/.local/state/morning-brew/` mode 0700, files 0600; the config file is refused if group- or world-writable.

- **Holds** (one atomic JSON file per package, temp file plus rename): `kind: decided | transient`, `held_at_version`, `reason`, `first_held`, `blocked_by` (component member), `model_reasoning` (labeled untrusted-derived), `skipped_for_version`. Decided holds are verdicts (rule HOLD, risk booleans, Claude hold). Transient holds are failures of machinery (fnox/Claude/Ollama down, closure mismatch, unjoinable, budget exceeded, network): they are excluded from this run, never counted against the owner in aging, and retried every run. The tool creates no pins.
- Re-triage when `current_version` differs from `held_at_version`; a human skip applies to that version only.
- **Reconcile at the start of every run:** compare `brew list --pinned` / outdated `pinned` against the ledger: an outdated package pinned by the user becomes SKIP; the ledger never claims a pin.
- **Stamp** records `last_full_success` and `quality: full|degraded` (degraded if any transient hold occurred or a stage was down). The next hourly fire retries transient holds.
- **Dead-man alarm:** `last_full_success` older than 48 hours is surfaced by every invocation and by `status` exit code.
- **Hold aging:** every summary and `status` shows decided-hold count, oldest decided-hold age, and blocked-by-dependency count. Stale decided holds escalate (urgent notification); a weekly digest lists them.
- Audit JSONL records `brew --version`, run id, package, rule id, stage, models (id and digest), booleans, final verdict, action result; stderr tails are redacted.

## 10. Apply, cleanup, failures

- Subprocesses: argv lists only, `shell=False`, `stdin=DEVNULL`, timeouts, child environment from an explicit allowlist (PATH including `/opt/homebrew/bin`, HOME, `HOMEBREW_NO_AUTO_UPDATE`, `HOMEBREW_NO_ANALYTICS`, `HOMEBREW_NO_ENV_HINTS`, `HOMEBREW_NO_INSTALL_CLEANUP`). A test asserts no notifier child env contains `*KEY*` or `*TOKEN*`.
- Component fails -> recorded failed, run continues, retried next run; not held, not pinned.
- Verify: re-snapshot; held/skipped packages must be unchanged; violations are anomalies (urgent).
- Cleanup (O4): after 2 full successful runs, per-package `brew cleanup <pkg>` for packages upgraded by those runs, never for the live-process set. `HOMEBREW_NO_INSTALL_CLEANUP=1` keeps `brew upgrade` from cleaning implicitly. README documents manual rollback (no automated rollback; zero rollback window today, E8).

Failure matrix (each row also in SPEC):

| Failure | Behavior | Hold kind |
|---|---|---|
| `brew update` fails / no network | abort before mutation, exit 75 (log marker), hourly fire retries | n/a (degraded stamp) |
| JSON unparseable | abort, exit 1 | n/a |
| Unjoinable candidate | exclude component | transient |
| Ollama down | local stage skipped; Claude still runs | none |
| Ollama digest mismatch | local stage disabled, warn | none |
| fnox fetch fails (10 s timeout) / Claude refusal, timeout, rate limit | GREY member excluded this run | transient |
| Model contradicts coverage check | HOLD | decided |
| Closure cross-check mismatch | component excluded, anomaly audited | transient |
| Partial upgrade failure | record, continue, retry next run | none |
| Held package changed as side effect | anomaly, urgent notification | n/a |
| Concurrent run | exit immediately (75) | n/a |
| Already ran today / before earliest hour | no-op, exit 0 | n/a |
| Notifier raises | swallowed | n/a |
| State dir unwritable | abort before mutation, exit 1 | n/a |
| Run budget (20 min) exceeded | remaining GREY members excluded | transient |

## 11. Evaluation: golden set, shadow mode, ship gate [PANEL, trimmed]

- Golden set of about 25-30 cases: real caveats from this machine, plus dangerous (data-loss, migration, restart) and prompt-injection synthetics, run against the real configured models. Reuse the audit JSONL format; `morning-brew eval` is a thin runner, not a separate harness.
- Shadow mode for the first ~10 runs: model-origin approvals are audited as would-be decisions but not applied.
- Ship gate: 0 false approves on the actionable and injection subsets. Only then may the owner set `chain.mode = live`. Rules-based UPGRADE-ELIGIBLE components are not gated by this (they involve no model).

## 12. launchd and secrets

- Plist runs `.venv/bin/morning-brew run` directly (installed with `uv sync --frozen`, not `uv run`), `StartInterval` 3600, no `RunAtLoad`, explicit PATH and env, `StandardOutPath`/`StandardErrorPath` under `~/Library/Logs/morning-brew/`, `ProcessType` Background. The earliest-hour gate and once-per-day stamp live in the tool. A LaunchAgent only runs while the user is logged in.
- No `fnox exec` wrapper. The Claude key is fetched lazily with `fnox get` (`stdin=DEVNULL`, 10 s timeout) only when L3 is reached, handed to the SDK client, and never placed in `os.environ`. Whether `fnox get` works non-interactively under launchd is unverified (spike S4); failure is a transient hold.

## 13. Notifications [PANEL]

Default: a silent macOS notification (no sound) written through `osascript` with the message passed as an argument to an `on run argv` handler (no string interpolation) plus a status file `~/.local/state/morning-brew/status.txt`. `say` and a user-supplied command notifier (Hue) fire only for urgent events (anomalies, dead-man alarm, stale holds). Quiet hours suppress non-urgent output. Weekly holds-aging digest. Notifier failures are swallowed.

## 14. Configuration (tomllib)

`earliest_hour`, `min_age_days`, `trusted_taps`, `chain.mode` (shadow|live), local model id and pinned digest, Claude model id and effort, run budget, cleanup runs (2), notifier set, quiet hours. Secrets never in config.

## 15. Layout

```
src/morning_brew/
  cli.py orchestrator.py ports.py domain.py versions.py rules.py components.py liveprocess.py
  brew/{runner.py,parse.py,snapshot.py}
  triage/{caveats.py,suspicious.py,prompt.py,schemas.py,coverage.py,chain.py,ollama.py,claude.py,golden/}
  store/{holds.py,audit.py,stamp.py,lock.py,firstseen.py}
  notify.py sanitize.py config.py launchd.py
```

## 16. Security notes and blast radius

- Models can only add risk, except one narrow path: Claude (L3) may turn a GREY member into ELIGIBLE, and only when the member already passed every deterministic rule (not live-process, trusted tap, arm64 bottle, not major, soaked at least `min_age_days`, not self-dependency, component otherwise clean), the `suspicious_text` flag is clear, all risk booleans are false and the coverage check passes, and only after the ship gate and shadow period. No model can turn a HOLD, SKIP, DEFER or REPORT into UPGRADE.
- The worst case from a misled or injected L3 is therefore the upgrade of one soaked, rule-clean package whose caveats changed or whose version is 0.x minor. That is a narrower surface than the rules-only UPGRADE class (revision/patch/minor with no flags), which involves no model at all and is the larger exposure.
- A hallucinating local classifier can only produce extra holds (observed E5).
- Untrusted text never reaches subprocess args, notification strings (except sanitized and control-stripped), or logs unsanitized. The API key is never in the environment, plist, config, or logs.

## 17. v1.1 candidates and design input

- Casks apply path. Panel S4 findings are the design input: `--no-quit`; DEFER when the app is running; naming a cask bypasses auto_updates protection; plain `outdated` already lists auto_updates casks and `--greedy` entries are stale; widen the HOLD rule to installer/uninstall/flight artifacts (not just pkg).
- OpenAI-compatible adapter for omlx / LM Studio (verified for Ollama only).
- Changelog / release-note fetching (`gh release view` for GitHub-hosted formulae), through the same sanitizer and suspicious-text flag.

## 18. Open spikes

S4: `fnox get` non-interactive under a throwaway launchd job. S6: Anthropic SDK 2.x structured output, refusal handling and `fallbacks` form against live docs. (Former S1/S2/S3/S5 are retired by panel evidence or by the cask scope cut.)
