# reliability — Round 2

## Changed my mind
- **Local-model authority: from "trust after agreement" (I listed no objection to L1+L2 auto-approve) to "classify only" (D5b).** S5's probe shows qwen3:4b gives ~0.95 "upgrade" for a synthetic data-loss caveat and for an injected instruction, and glm truncates or fails JSON. My lens is *silent wrong outcomes*. A model that approves by default turns an auto-upgrade into one of those. I don't think two agreeing models are independent evidence when one of them says yes to everything.
- **Third-party taps: from not considered to D9(a).** At first I worried that "HOLD unless trusted" would bury the machine in holds. I measured it instead (read-only, `brew outdated --json=v2` plus `brew info --json=v2 --installed`, today). Of the **22 outdated formulae, 19 are homebrew/core and 3 are taps** (oh-my-posh, smartthings, vector), and vector is already held under S2. The digest's worry about "a large share" doesn't hold up on today's data, so the hold costs little.
- **Cleanup: from deferred-N-days to (a) immediate plus exclusions.** C8 shows that zero formulae have more than one keg today and that rollback is manual anyway. A deferred-cleanup window only protects a rollback path nobody automates. What reliability actually needs is S3's exclusion: never clean the kegs of running services or of their dependency closure. Immediate cleanup with that exclusion is simpler and has fewer states to get wrong.
- **S2 extension accepted.** Holding the *runtime-dep closure* of a live service (for example erlang under rabbitmq, where beam.smp runs from a versioned Cellar path) is a gap in my round-1 rule 6. I now treat the closure as part of the service rule.

## Convergence items I object to
None. One amendment to C6: holds must be keyed by `decided` vs `transient` (S7). Otherwise "oldest hold age" counts a held package that is really just Ollama being down after a reboot, and the escalation loses its meaning.

## Strong findings S1–S13
- S1: accept the tap HOLD. Accept the soak rule (`min_age_days=3` ⇒ DEFER) for **all** packages, not only taps. It's cheap and also catches botched core releases.
- S2: accept in full, including the dep-closure extension. Confidence 5. This is my own evidence, and vector is the live counterexample.
- S3: accept. Add "upgraded, old binary still running" as an expected status, not an anomaly.
- S4: accept. `--no-quit` is non-negotiable.
- S5: accept as directional. It's the reason I moved on D5.
- S6: accept the digest pin. A mismatch ⇒ rules-only, which degrades to a *transient* hold (S7), not a decided one.
- S7: accept. I authored it, and I'd fight for it.
- S8: accept. Pair it with C7: reconcile runs first, then the dead-man check, both before triage.
- S9: modify. Shadow mode is worth having. A golden set of about 60 cases plus a harness (8 pts) is too much for v1 if D5b holds, since local models only classify caveats. Cut it to about 25 caveat-classification cases (3 pts).
- S10: accept. Deterministic caveat diffing is the best reliability lever on this list because it removes model calls entirely.
- S11: accept all of it.
- S12: accept. Report self-deps rather than upgrading them unattended (fnox, the python the venv links against).
- S13: accept. It confirms C6 and D8: escalation must reach a persistent channel.

## Divergence D1–D10
- **D1: (b) exclude without pin, and ask the owner to revisit D1.** A pinned dependency makes the owner's own `brew upgrade` fail on its dependents (formula_installer.rb:488-512), and the tool only upgrades explicit lists anyway. Keep pins for held leaves only, with the pending_pin journal. Confidence 4.
- **D2: (a) immediate, explicit list, excluding running-service kegs and their closure.** The rollback window is fictional (C8), and fewer states means fewer failure modes. Confidence 3.
- **D3: (c) stage L3 to v1.1.** fnox under launchd is the least-proven dependency here. With D5b + S10 + S1, the volume of "unsure" cases is unknown and probably small. Collect logs first. Confidence 3.
- **D4: (a) boolean risk fields plus a coverage check.** Self-reported confidence fails silently. Booleans are auditable, and "any risk true ⇒ HOLD" fails toward safety. Confidence 4.
- **D5: (b) local models classify caveats only.** S5's evidence is decisive for my lens. A pure-patch upgrade with boilerplate or no caveats on a trusted core formula goes through rules. Anything needing judgment ⇒ HOLD in v1 (L3 in v1.1). Confidence 4.
- **D6: (a) report-only for v1.** S4 shows brew's cask semantics are full of traps: stale greedy metadata, quitting running apps, pkg scripts. There's 1 outdated cask today, so the value is near zero and the risk isn't. Confidence 4.
- **D7: (b) StartInterval 3600 + stamp + earliest-hour gate.** Retries come for free, it avoids RunAtLoad firing at login before the network is up, and it pairs with S7. A degraded stamp lets the next hourly run retry transient holds. Confidence 4.
- **D8: (b) silent notification + status file, `say` only for urgent.** S13 shows the owner doesn't read holds. A spoken success at 07:00 trains them to ignore the channel, and it leaves no trace. Use a persistent Notification Center alert plus a weekly holds-aging digest. Hue stays optional for urgent. Confidence 4.
- **D9: (a) HOLD unless `trusted_taps`.** Measured cost is 3 of 22 today. Suggest the owner seed `trusted_taps` with vendor taps they chose deliberately. Confidence 4.
- **D10: about 95 pts.** Start from the pragmatist's ~93 (no casks, L3 → v1.1, immediate cleanup, slim review CLI and notifier), remove golden-set overreach (S9 → 3 pts), and add the hardening that prevents silent failure: S2 service evidence and closure (+3), S7 transient holds (+2), S8/C7 journal, reconcile and dead-man (+3), S1 taps and soak (+1), S10 caveat diff (+2). Net about 95–100. Confidence 3.

## Final verdict
**SHIP-WITH-CHANGES.** The change I'd fight hardest for is **S7 + S8 together: transient holds are retried every run and never pinned, a pending_pin journal plus reconcile runs first, and a 48 h dead-man alarm is surfaced by every invocation and by `status`'s exit code.** Every other finding fixes a wrong decision. This one fixes the case where the tool stops making decisions and nobody notices.
