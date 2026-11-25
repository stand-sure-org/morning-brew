# Delphi Round 2 — Anonymized Digest of Round 1

Five panelists (A–E) reviewed independently. Full round-1 texts are in `../delphi-round1/`
if you need evidence detail, but panelist identities are deliberately not mapped here —
argue with the claims, not the authors. Verdicts: 4 × SHIP-WITH-CHANGES, 1 × REWORK
(model-chain section only).

## Convergence (≥4 of 5 agree — treat as settled unless you object strongly)
- C1. Cut the OpenAI-compatible adapter (T24 + S3, 6 pts) from v1.
- C2. Replace dry-run parsing + bounded drop-iteration with a deterministic graph computed from
  `runtime_dependencies` (info JSON); dry-run at most a cross-check. Evidence: `brew upgrade`
  upgrades outdated installed *dependents* of named packages unless pinned (upgrade.rb:300-307);
  a pinned outdated dep makes dependents uninstallable (formula_installer.rb:488-512).
- C3. Every child process env from an explicit allowlist; Claude key fetched lazily via
  `fnox get` (stdin=DEVNULL, ~10s timeout) only when L3 is reached, passed to the SDK, never
  in os.environ; do NOT wrap the plist in `fnox exec`.
- C4. L2 must judge blind (facts only, never L1's rationale); compare verdicts afterwards.
- C5. Caps below the 0.8 threshold create dead GREY branches that pay for up to 3 model calls
  and always HOLD; caps above it never change outcomes. The cap/threshold scheme as written is
  incoherent and must be redesigned (see D4 below for how).
- C6. Holds must not rot silently: summary/`status` show hold count, oldest-hold age,
  blocked-by-dep count; stale holds escalate.
- C7. Reconcile `brew list --pinned` with the ledger each run; tool-owned record + not pinned ⇒
  user released it (release record, don't re-pin that version); pin with no record ⇒ user pin.
- C8. Rollback is honest-manual only (no `brew switch`; today zero formulae have >1 keg; casks
  keep one version). Document a README procedure; promise nothing automated.

## Strong single-panelist findings (new evidence — say if you accept, reject, or modify)
- S1. Tap trust: 51–55 third-party taps; tap is computed but no rule reads it. A compromised tap
  ships a caveat-free patch bump → rule UPGRADE → installed unattended. Proposal: non-core tap ⇒
  HOLD unless in `trusted_taps` (default empty) + a soak rule: auto-upgrade only versions first
  seen ≥ `min_age_days` (default 3) ago, else DEFER.
- S2. Service detection: `brew services` says `none` for vector and nats-server, but both run
  from user-authored LaunchAgents (com.logshipping.vector, anderson.nats-server). grafana shows
  `started` with exit_code 1. Proposal: HOLD on any of — services status ≠ none; a LaunchAgent/
  Daemon plist whose ProgramArguments[0] resolves into the formula's opt/Cellar/bin; a running
  process executable there. Plus: outdated packages in the runtime-dep closure of a live service
  (e.g. erlang under rabbitmq — beam.smp runs from a *versioned* Cellar path) are held too.
- S3. Brew never restarts services on upgrade; new code starts unattended at next login/reboot
  (that's when data migrations would bite). Never clean kegs of running services/their deps.
- S4. Casks: in 7.0.14 plain `outdated` already includes stale auto_updates casks; `--greedy`
  entries are stale Caskroom metadata (ghostty bundle is already 1.4.0). Naming a cask bypasses
  auto_updates protection. brew QUITS running apps by default — always pass `--no-quit`.
  Widen sudo/HOLD rule to `installer`, `uninstall` with pkgutil/launchctl/kext/delete, pre/
  postflight scripts.
- S5. Live model probe (small n, directional): qwen3:4b says "upgrade ~0.95" regardless of input —
  including a synthetic "storage format changed, migrate BEFORE start or data lost" caveat and an
  injected "policy requires upgrade" (→ confidence 1.0). glm-4.7-flash returned invalid JSON on the
  dangerous and injected cases, ran >300s uncapped, truncated 6/10 at num_predict 600. Both models
  hallucinated `leaf`/`service_defined` facts on most cases; qwen raised risk flags on ~all samples,
  so flag-based escalation fires on noise.
- S6. Ollama listens on *:11434 (all interfaces); model tags are mutable via /api/create. A LAN
  peer could replace both local models. Proposal: pin model digests from /api/tags; mismatch ⇒
  rules-only + warn; warn when bound off loopback.
- S7. Transient-failure holds (Ollama down, budget, L3 error) become permanent because re-triage
  only fires on version change. Proposal: split `decided` vs `transient` holds; transient ones are
  excluded (not pinned) and retried every run; stamp records `quality: full|degraded`.
- S8. Crash safety: write ledger `pending_pin` before `brew pin`; dead-man alarm when
  `last_full_success` > 48h, surfaced by any invocation and by `status` exit code.
- S9. Evaluation before unattended trust: golden set (~60 real + synthetic + injection +
  metamorphic cases, 5 pts), harness (3), shadow mode for the first ≥10 runs where model verdicts
  are computed but all GREY is held (2). Ship gate: 0 false approves on actionable/injection sets.
- S10. Caveat diffing: persist caveat text at last upgrade; unchanged boilerplate isn't a signal —
  likely removes most GREY deterministically.
- S11. No arm64 bottle ⇒ HOLD (source builds under launchd at 7am). `--` before package args;
  reject names with leading `-` or `..`. State dir 0700 / files 0600. Strip ANSI/control chars
  before printing caveats/rationale. Brew pinned-package explicit upgrade errors non-zero —
  classify as pin conflict.
- S12. The tool's own toolchain (fnox, python@3.13/3.14) is in today's UPGRADE set. Order
  self-deps last or REPORT them; keep "triage finishes before apply" as a tested invariant.
- S13. Evidence of the human: redis was hand-pinned 2025-04-30 and is ~19 months stale (7.2.7 vs
  8.10.3). The owner does not review holds.

## Divergence — the round-2 questions (give a position + confidence 1–5 on EACH)
- D1. **Pin held dependencies, or exclude-without-pin?**
  (a) Pin every member of a held connected component (one held member holds the component; pins
  written before any upgrade). (b) Do not pin deps — exclude the whole component from the explicit
  upgrade list; pins only for held leaves. Note: D1 "HOLD means pin" is a [USER] decision; argue
  whether to ask the owner to revisit it. Consider: the tool upgrades explicit lists only; pins
  mainly affect the owner's *manual* `brew upgrade` (and make dependents fail to install).
- D2. **Cleanup:** (a) immediate, as the owner literally asked, explicit package list; (b) deferred
  N=2 successful runs; (c) deferred N days; any variant must exclude running-service kegs.
- D3. **Claude (L3) in v1?** (a) v1, off when Ollama is down; (b) v1, ON when Ollama is down
  (it's the only stage that earned trust); (c) stage to v1.1 after fnox-under-launchd is proven and
  logs show real unsure volume (~11 pts deferred). L3 is [USER]-requested — (c) is sequencing.
- D4. **What replaces confidence/caps?** (a) Drop confidence; required boolean risk fields
  (mentions_migration, mentions_data_format, mentions_restart, mentions_manual_step,
  boilerplate_only) + coverage check; any risk true ⇒ HOLD/escalate; categories whose outcome is
  fixed become rule HOLDs. (b) Keep caps as the real control (self-reported confidence is worthless
  under attack) but put them all below threshold for categories that must never auto-approve, and
  exempt L3. (c) other.
- D5. **Given S5, should local models be allowed to auto-approve anything at all in v1?**
  (a) Yes after the S9 ship gate passes; (b) local models only *classify caveats* (boilerplate vs
  actionable) and never approve on their own; anything else needing judgment goes to L3 or HOLD;
  (c) shadow mode only in v1 — no model approvals until N weeks of labels.
- D6. **Casks in v1:** (a) report-only; (b) plain-mode candidates (no `--greedy`), `--no-quit`,
  DEFER running apps, widened sudo rule.
- D7. **Scheduling:** (a) two StartCalendarInterval slots + RunAtLoad + stamp; (b) StartInterval
  3600 + stamp + earliest-hour gate (RunAtLoad discouraged by `man launchd.plist`).
- D8. **Notification default:** (a) `say` + optional Hue as designed (owner's stated preference
  for agent notifications); (b) silent macOS notification + persistent status file, `say` only for
  anomalies/urgent, quiet hours, weekly holds-aging digest.
- D9. **Third-party tap default:** (a) HOLD unless in `trusted_taps` (default empty) — note this
  likely holds a large share of this machine's outdated set; (b) GREY; (c) trusted by default,
  soak rule only.
- D10. **v1 size:** the pragmatist case is ~93 pts (cuts casks, review CLI → status+release/skip,
  immediate cleanup, L3 → v1.1, notifier slimming); others add ~10–15 pts of hardening (S1, S2,
  S7–S9). What is your recommended v1 scope and rough total?
