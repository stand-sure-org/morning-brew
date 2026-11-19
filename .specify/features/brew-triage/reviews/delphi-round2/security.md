# security — Round 2

Read-only check run this round: `brew outdated --json=v2` grouped by tap gives 23 outdated: 19 core formulae, 1 core cask, and 3 third-party (jandedobbeleer/oh-my-posh, smartthingscommunity/smartthings, vectordotdev/brew). `lsof -iTCP:11434 -sTCP:LISTEN` still shows Ollama on `*:11434`.

## Changed my mind
- **Casks: report-only → (b) plain-mode with guards.** S4 showed two things I didn't know. Plain `outdated` already includes stale auto_updates casks. Brew quits running apps by default. Together those make `--no-quit`, DEFER-running and the widened installer/uninstall/flight HOLD rule a tighter boundary than I'd assumed. I still prefer report-only for the first release (see D6), but it is no longer a security objection.
- **Q3 caps: "caps do the real work" → drop confidence (D4a).** C5 and S5 together convinced me. Under attack, qwen returns confidence 1.0, and caps below threshold just pay for model calls that always HOLD. Deterministic boolean risk fields plus rule HOLDs give the same protection without the incoherence.
- **Rollback: my "retained kegs = incident response" → honest-manual (C8).** There is no `brew switch`, zero formulae have more than one keg, and casks keep only one version. Retained kegs are a weak defense, so cleanup timing (D2) is about convenience, not security.
- **D9 cost: I'd assumed the tap HOLD might be broad. Measured: it is 3 of 23 (13%).** One of those three (vector) is a live service under S2 anyway, so the tap rule costs little.

## Convergence items I object to
None. C3 and C4 are my round-1 positions. One addition to C2: compute the closure from `runtime_dependencies` *after* name validation (S11), because dependency names are tap-supplied too.

## Strong findings S1–S13
- S1 accept (my finding). Soak is the cheaper half and protects core too, so keep it even if the tap rule is softened.
- S2 accept. Add: a LaunchAgent pointing into a Cellar path is also a persistence signal worth listing in `status`.
- S3 accept.
- S4 accept.
- S5 accept. This is the decisive evidence: local models' approvals carry no signal, and injection raised qwen's confidence to 1.0.
- S6 accept (my finding). Downgrade from blocker to preflight warning plus digest pin, because D5b removes approval power anyway.
- S7 accept. Also stamp degraded records so a later full run re-triages them.
- S8 accept. `pending_pin` must be written to a 0600 file.
- S9 accept the golden set and injection set. Shadow mode becomes moot if D5b is adopted.
- S10 accept. Persist caveat hashes in the state dir and validate on load.
- S11 accept (partly mine).
- S12 accept. Report self-deps (fnox/python) rather than upgrading them in-run; a tool that upgrades its own interpreter mid-run is an integrity hazard.
- S13 accept. It strengthens C6 and argues against pinning anything that doesn't strictly need it (D1).

## Divergence D1–D10
- **D1: (b) exclude-without-pin, except user-intent holds.** The tool upgrades explicit lists, so pins buy almost nothing for the tool. They also leak into the owner's manual workflow and break dependents' installs, and S13 shows pins rot. Pin only leaves held for *decided* reasons, and ask the owner to revisit the [USER] "HOLD means pin" decision with that evidence. Conf 4.
- **D2: (b) deferred, N=2 successful runs, never service kegs.** Not a security control (C8). Deferral is cheap and lets a bad release be noticed before the old keg disappears. Conf 3.
- **D3: (c) v1.1.** Under C3, fnox access is lazy and key-scoped, but proving fnox under launchd is the risky part, and D5b makes L3 the only approver. Ship rules plus HOLD first, then add L3 once the unsure volume is measured. Conf 3.
- **D4: (a) boolean risk fields plus coverage check; categories with fixed outcomes become rule HOLDs.** A deterministic `suspicious_text` flag sits beside it. Conf 4.
- **D5: (b) classify-only.** S5 shows local approval carries no signal, and S6 shows the models are replaceable by a LAN peer. A local model may only *add* risk (actionable ⇒ HOLD), never remove it. Conf 5.
- **D6: (a) report-only in v1, (b) in v1.1.** Pkg installers and flight scripts run arbitrary code as the user, sometimes with sudo. The guard list in S4 is new and untested. Conf 3.
- **D7: (b) StartInterval plus stamp plus hour gate.** No security difference; it is more robust to sleep. Conf 2.
- **D8: (b)** silent notification plus status file, `say` for anomalies. It is also the security-relevant channel: dead-man (S8) and digest-mismatch (S6) alerts need to stand out from daily noise. Conf 3.
- **D9: (a) HOLD unless `trusted_taps`, plus soak for everything.** Measured cost is 3 of 23 packages, and the owner can trust oh-my-posh in one config line. Conf 4.
- **D10: about 105 pts.** Start from the pragmatist's ~93, which already cuts L3 and casks. Swap immediate cleanup for deferred cleanup (about 0). Add S1 tap+soak (2), S2 service detection (3), S7 transient holds (2), S8 pending_pin+dead-man (2) and the S9 golden/injection set without harness-heavy shadow mode (5), which comes to about 107. D5b then removes most of the confidence/escalation plumbing (about -2). Conf 3.

## Final verdict
SHIP-WITH-CHANGES. The change I'd fight hardest for is **D5b: no model (local or otherwise) can turn a rule HOLD/GREY into UPGRADE in v1.** Models may only add risk. Upgrades happen only through deterministic rules, which include tap trust and the soak window. That single invariant makes prompt injection, the replaceable Ollama models and fake model confidence non-issues by construction.
