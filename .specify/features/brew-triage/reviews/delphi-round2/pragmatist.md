# pragmatist — Round 2

Read-only check I ran: `brew outdated --json=v2` piped to `brew info --json=v2` to get each package's tap.
- Of the 22 outdated formulae, 3 come from third-party taps: oh-my-posh, smartthings and vector.
- Plain-mode casks: 1 (antigravity-cli).

## Changed my mind
- **Local models approving (old Q3 → D5).** I said "two agreeing stages is strict enough". I now say local models should never approve. S5 moved me: qwen gave 0.95 to a caveat saying "migrate BEFORE start or data lost", and gave 1.0 to an injected "policy requires upgrade". When the cheap stage says yes to everything, a second stage agreeing with it adds nothing.
- **Service detection (old Q7 → S2).** I said "status ≠ none is enough; a process check is plumbing". I now accept S2. vector and nats-server run from user-written LaunchAgents and `brew services` reports them as `none`. My rule would have upgraded a live log shipper while nobody was watching. Checking the plists is about 1 point.
- **Claude timing (D3).** I wanted it in v1.1; I now want it in v1. If local models can't approve, any grey case Claude doesn't see becomes a HOLD. Holds rotting is my own top risk. S10 (caveat diffing) should cut grey volume a lot, so Claude stays cheap.
- **Transient holds (S7).** New to me and I accept it. Holding a package because Ollama was down, then never retrying it, is the same silent rot as #1 from round 1, arriving by another route.

## Convergence items I object to
None. One note on C3: it assumes Claude is in v1, which I now support (see D3).

## Strong findings S1–S13
- S1 **accept.** The tap HOLD costs little: it applies to 3 of 22 packages today (see D9). The soak rule is also cheap, needing a single first-seen timestamp per version in the ledger.
- S2 **accept.** Check plist ProgramArguments, plus one `ps -axo comm` pass for versioned Cellar paths like `beam.smp`. Hold the outdated deps a live service runs on as well.
- S3 **accept.** Never clean kegs of running services; this also limits D2.
- S4 **accept as v1.1 guidance.** It is moot under report-only casks. Record `--no-quit` and the wider sudo rule for when casks are enabled.
- S5 **accept.** It decides D4 and D5.
- S6 **accept, trimmed.** Pin model digests and warn when Ollama is reachable beyond localhost, about 1 point. If the digest doesn't match, run rules only.
- S7 **accept.**
- S8 **accept.** The dead-man alarm is the right backstop for an unwatched job.
- S9 **modify.** Use about 30 cases: real caveats from this machine plus injection and dangerous synthetics. Reuse the existing JSONL instead of building a separate harness (about 3 points, not 10). Keep shadow mode for 10 runs and the ship gate of 0 false approves.
- S10 **accept, highest ROI on the list.** Unchanged boilerplate caveats become decidable by rule.
- S11 **accept all of it.** Each item is a line or two.
- S12 **accept** (raised in my round 1). Upgrade fnox and python last, or set them to REPORT.
- S13 **accept** (raised in my round 1). It is the reason for C6.

## Divergence D1–D10
- **D1: (b) exclude the component without pinning; pin only held leaves. Ask the owner to revisit the "HOLD means pin" decision (D1 [USER]).** Explicit-list upgrades already protect the component. Pins on deps only break the owner's manual installs, and the owner never reviews them. Conf 3.
- **D2: (a) immediate cleanup with an explicit package list, excluding kegs of running services and their deps.** It is what the owner asked for, and no keg is rolled back today. Conf 4.
- **D3: (b) Claude in v1, and it stays on when Ollama is down.** Once D5 is (b), Claude is the only stage allowed to approve, so tying it to Ollama's health makes no sense. If the fnox fetch fails, record it as a transient hold (S7). Conf 3.
- **D4: (a) required boolean risk fields plus a coverage check; categories with a fixed outcome become rule HOLDs.** S5 shows self-reported confidence is noise, and caps on noise are still noise. The booleans can be checked against the caveat text, so they are auditable. Conf 4.
- **D5: (b) local models only label caveats as boilerplate or actionable. Approval comes from rules, Claude, or nothing.** I add the S9 shadow and ship gate before even classification drives an action. Conf 4.
- **D6: (a) report-only.** Plain mode shows 1 cask, and all of S4's machinery would exist for that one. Conf 4.
- **D7: (b) StartInterval 3600 + stamp + earliest-hour gate.** One mechanism, no RunAtLoad, and a missed slot is retried within the hour. Conf 3.
- **D8: (b) silent notification + status file; `say` only for anomalies; weekly holds-aging digest.** The owner's `say` preference is for interactive agent sessions, not a 7 a.m. wake job that may fire in a meeting. Make `say` opt-in. Conf 4.
- **D9: (a) HOLD unless the tap is in `trusted_taps`.** The digest worried this would hold "a large share". My check shows 3 of 22 (14%), and one of those, vector, is held anyway as a live service. Conf 4.
- **D10: about 108 points.** Start from my 93.
  - Add back:
    - Claude: +11
    - Hardening, about +12: S1, S2, S7, S8, S10, S11
    - Trimmed S9: +3
    - S6: +1
  - Take out, about −12:
    - One local model instead of two (glm's evidence is invalid JSON and over 300 s runs): −3
    - Simpler cap logic: −2
    - The rest from rounding and the cuts already counted
  - The cuts from round 1 stay: casks report-only, review CLI replaced by `status` + `release`/`skip`, immediate cleanup, slimmer notifiers.
  - Conf 3.

## Final verdict
**SHIP-WITH-CHANGES.**

The change I'd fight hardest for is D5(b): local models never approve an upgrade on their own. S5 shows the cheap stage will approve a data-loss caveat or an injected instruction with high confidence. In a job nobody watches, that is the one failure that can't be undone: data is lost before anyone looks. Everything else on this list is recoverable.
