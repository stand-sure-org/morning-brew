# llm-judge — Round 2

## Changed my mind
- **Verdict: REWORK → SHIP-WITH-CHANGES.** My REWORK covered only the model chain. If D4(a), D5(b) and S9 are adopted as convergence changes, the chain becomes safe enough to build. The rest of the design already had four SHIP votes, and C2/C3/C7 fix the remaining structural problems without a redesign.
- **D3 / L3 when Ollama is down: ON → staged (v1.1).**
  - I argued "ON" because L3 was the only stage that earned trust. That still holds for quality.
  - The C3 evidence changes the sequencing. Getting the key via fnox under launchd is unproven. S7 shows a failed L3 call becomes a permanent hold.
  - Shipping L3 before both are solved would turn my "trusted stage" into a hold generator.
- **Model gate is no longer my top lever.** S2 changed my view: vector and nats-server show `none` but run from user-authored LaunchAgents. That is a rules-layer false-negative, and it sits *upstream* of any model. Better models cannot fix a fact the tool never gathers. I now rank S2 alongside my own S9.
- **D9 premise corrected (new check).** I ran `HOMEBREW_NO_AUTO_UPDATE=1 brew outdated --formula --json=v2` and `brew info --json=v2 --installed`, both read-only.
  - Result: 22 outdated formulae. 19 are from homebrew/core and 3 from third-party taps (oh-my-posh, smartthings, vector).
  - So "trusted_taps default empty holds a large share" is false: it holds about 14%, and vector is already held by S2.
  - This makes D9(a) cheap, and I moved toward it.

## Convergence items I object to
None.
- **C5:** I'd go further and drop the cap scheme entirely (D4a). C5 as worded is compatible with that.
- **C4:** blind L2 is necessary but not sufficient. qwen and glm share a failure mode ("upgrade ~0.95" regardless of input), so blind agreement between them is still weak evidence. This is why I pick D5(b).

## Strong findings S1–S13
- **S1 accept**, both the tap rule and soak. Soak (`min_age_days` 3) is the cheapest defence against a compromised core bump too.
- **S2 accept.** Also include the runtime-dep closure of live services (erlang/beam.smp).
- **S3 accept.**
- **S4 accept** — but see D6.
- **S5 accept.** It's my evidence. Small n, but the direction is unambiguous: the signal is inverted.
- **S6 accept.** Pinning model digests is a few lines and closes LAN model substitution.
- **S7 accept, high priority.** Without it every model or L3 failure silently becomes a decided HOLD.
- **S8 accept.**
- **S9 accept as the ship gate for any model approval.** Shadow mode is non-negotiable.
- **S10 accept, and promote.** It's deterministic, needs no model, and probably removes most GREY. Build it *before* the chain, then size the chain from what's left.
- **S11 accept.**
- **S12 accept.**
- **S13 accept.** This is the strongest argument against defaulting to "HOLD everything uncertain". False holds are a real failure, so the hold-aging in C6 is required.

## Divergence D1–D10
- **D1: (b) exclude-without-pin, and ask the owner to revisit.**
  - The tool upgrades explicit lists only, so pins mainly break the owner's manual upgrades and dependents' installs.
  - The ledger is the hold record. Pin held leaves only.
  - Confidence 4.
- **D2: (b) deferred N=2 successful runs, excluding live-service kegs.**
  - Deferral is the only rollback C8 leaves us.
  - Confidence 3.
- **D3: (c) stage L3 to v1.1.** Keep the interface stubbed so v1.1 is additive. Confidence 3.
- **D4: (a).**
  - Booleans plus a coverage check, with the booleans emitted before `verdict` in the schema.
  - Categories with a fixed outcome become rule HOLDs, costing zero calls.
  - (b) still relies on a number that doesn't vary.
  - Confidence 5.
- **D5: (b), combined with (c) as v1's initial state.**
  - Local models only classify caveats, and the S10 caveat diff runs first.
  - A "boilerplate" label from them can permit UPGRADE only after the S9 gate passes, and only for otherwise rule-UPGRADE candidates (patch/minor, non-service, core or trusted tap, soaked).
  - Confidence 4.
- **D6: (a) report-only.**
  - S4 shows the cask semantics are subtle enough to need a separate design: auto_updates bypass, stale greedy metadata, quit-by-default.
  - Confidence 4.
- **D7: (b) StartInterval + stamp + earliest-hour gate.**
  - It is robust to sleep and avoids the RunAtLoad caveat in the man page.
  - Confidence 3.
- **D8: (b).**
  - Daily `say` gets habituated within a week. S13 shows the owner already ignores holds.
  - The fix is the weekly aging digest plus anomaly-only audio.
  - Confidence 3.
- **D9: (a) HOLD unless trusted, plus the soak rule.**
  - Measured cost is 3 of 22 outdated formulae, and one of those is already S2-held.
  - Seed `trusted_taps` during setup by asking once.
  - Confidence 4.
- **D10: about 100 pts.**
  - Start from the pragmatist's ~93, then add:
    - S2 service detection (~3)
    - S7 transient holds (~2)
    - S9 evaluation, golden set plus harness plus shadow (~10, trimmed to ~7 by a 30-case set)
    - S10 caveat diff (~2)
  - Pay for these with the T18 simplification (−3) and the coverage-boolean swap (0).
  - L3, casks, OpenAI adapter and the review UI all go to v1.1.
  - Confidence 3.

## Final verdict
**SHIP-WITH-CHANGES.**

The change I'd fight hardest for: **no local-model output may cause an unattended upgrade until a measured gate passes** (D5(b) with S9 shadow mode: 0 false approves on the actionable and injection sets). Every probe showed the small models approving a data-loss caveat and an injected instruction with near-maximum confidence. Without this gate, the GREY path is an auto-approve path with extra steps.
