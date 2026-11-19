# llm-judge — Round 1

## Verdict
**REWORK** (triage-chain section only; the rules, closure check and ledger are sound).

On my probe evidence (see Evidence below), the L1/L2 escalation signals are inverted. The small models say "upgrade ~0.95" whatever the input, so escalation fires on noise (spurious flags, hallucinated facts) rather than on danger. The one truly dangerous caveat produced "upgrade" or invalid JSON. Caps above the 0.8 threshold do nothing, the two-stage agreement is not independent, and there is no plan to evaluate the chain before it runs unattended.

## Top 3 must-change

**1. Confidence and the caps above threshold are theatre. Replace them and narrow the task.**
- **Problem:** confidence sits at 0.95–1.0 at temperature 0 and 0.7 alike, and the injection pushed it *up* to 1.0.
  - The caveats, 0.x-minor and service-none caps (0.85) are above the 0.8 threshold, so clamping never changes an outcome.
  - The 0.5, 0.6 and 0.7 caps are below threshold. FR-046 doesn't say whether L3 is capped. If it is, those categories are fixed HOLDs that still pay for up to three model calls. If it isn't, the caps do nothing for L1/L2.
- **Failure:** the "storage format changed" caveat at 0.95, clamped to 0.85, passes the confidence gate.
- **Change:** drop `confidence`. Use required boolean fields instead: `mentions_migration`, `mentions_data_format`, `mentions_restart`, `mentions_manual_step`, `boilerplate_only`. Any risk boolean true means HOLD or escalate. Make deprecated, unparseable and high-dependent candidates HOLD by rule, or send them straight to L3.
- **Where:** FR-041, FR-042, FR-046, D6, DESIGN section 6.

**2. The claim-check only catches false claims, not missing ones, and its noise makes L1 a pass-through.**
- **Problem:** both models claimed false facts (`service_defined`, `leaf`) on almost every case. qwen emitted risk flags on 14 of 15 samples at temp 0.7, and on 5 of 5 at temp 0. So "any flag" and "claim mismatch" fire nearly always, and L1 escalates everything.
  - `facts_used` just echoes facts the tool supplied, so checking it says nothing about judgment, and omissions go unpunished.
  - The motivating qwen error ("leaf = no dependencies") was about dependencies. `no_dependents` is *true* for a leaf, so this check would not have caught it.
- **Change:** turn it into a coverage check. Each risk fact actually present (caveats, service, dependents ≥ N) must be addressed by its matching boolean, and a missing one means unsure. Add `uniqueItems` and `maxItems` to the arrays.
- **Where:** FR-043, DESIGN section 6 point 3.

**3. No evaluation before trusting it unattended.** PLAN tests only scripted fakes.
- **Add:**
  - **T27 golden set (5 pts):** about 60 cases.
    - Real homebrew-core caveats, labelled boilerplate vs actionable.
    - Synthetic dangerous caveats.
    - The FR-045 injections, run against *real* models.
    - Metamorphic pairs: flip one risk fact and the verdict must flip.
  - **T28 harness (3 pts):** false-approve and false-hold rates per stage and for the whole chain, plus invalid-output and timeout rates.
  - **T29 shadow mode (2 pts):** the first ≥10 runs compute model verdicts but HOLD all GREY. `review` decisions become labels.
- **Ship gate:** 0 false approves on the actionable and injection sets, and false holds under about 50%.
- **Where:** PLAN Phase 6, the Phase 4 checkpoint, FR-091.

## Positions on open questions
- **Q3:**
  - The agreement rule is too lax because it isn't independent: L2 sees L1's verdict and rationale, which invites anchoring. Run L2 blind, compare afterwards, and let L3 see both.
  - The caps and the 0.8 threshold can't be defended when confidence doesn't vary.
  - Confidence: 5.
- **Q9:**
  - Metadata is enough to classify a *caveat* (boilerplate vs actionable), and that is the only grey-area task that works without changelogs.
  - A 0.x-minor with no caveats gives the model nothing to go on. Decide it by config policy (upgrade a leaf with no service), with no model call.
  - Confidence: 4.
- **Q6:** Sonnet is fine at 1–5 calls a day. Default `claude_when_local_down` to **on**, because L3 is the only stage that earned trust here. Confidence: 3.
- **Q10:**
  - Rules-win plus the closure re-check does bound the damage to a patch or minor upgrade of a non-service package.
  - But the injection reached confidence 1.0 (all 4 qwen samples) and cleared every risk flag once, so the FR-045 tests must use real models.
  - Confidence: 4.
- **Q11:** acceptable. Unparseable versions should go to HOLD or L3, not L1. Confidence: 3.

## Things nobody asked about
- **Caveat diffing.** Persist the caveat text seen at the last upgrade; unchanged text is not a new signal. This is deterministic and would probably remove most GREY candidates (awscli, imagemagick and numpy all carry stable boilerplate; belief, not counted).
- **Runaway output.** Set `num_predict` (about 400) on every Ollama call, and treat truncated JSON as unsure.
  - Uncapped glm-4.7-flash ran past 300 s.
  - Capped at 600 tokens, it truncated 6 of 10 outputs. I believe the cause is repeated enum items; I didn't inspect the raw output.
  - qwen3:4b with `think:true` plus `format` did not finish a single call in 300 s. Keep `think:false` unless a spike proves otherwise.
- **Field order.** With thinking off, generation runs top to bottom, so put the booleans and rationale *before* `verdict`.
- **Self-consistency.** Temperature 0 removes the only cheap uncertainty signal left. k=3 samples at temperature > 0 that must all agree costs about 3 s per candidate.
- **False holds matter too.** If GREY almost always ends as HOLD, the review queue becomes the new unread `brew outdated` (DECISION section 1). `status` should report hold-queue age and the share of GREY the models approve.

## Overbuilt
- **T24 / S3** OpenAI-compatible adapter: cut, saves 6 pts.
- **T18:** remove the clamp and threshold logic and the categories whose outcome is fixed. 8 → 5, saves 3.
- **T17:** swap the claim-check for the coverage booleans. Same points.
- **Net:** the 9 points saved pay for T27–T29 (10 pts), and the chain becomes measurable.

## Evidence
Probe run 2026-11-17 against local Ollama with a DESIGN-section-6-shaped schema and a minimal system prompt (not the designer's full prompt).
- qwen3:4b: think off, 3 samples at temp 0.7 plus 1 at temp 0 (the DESIGN config).
- glm-4.7-flash: 2 samples at temp 0.7, capped at 600 tokens.
- Cases marked * are synthetic inputs, not observed brew data.
- Script: `/tmp/llmjudge-probe/probe.py`. This is a small sample, so treat it as directional.

| Case | qwen3:4b | glm-4.7-flash |
|---|---|---|
| awscli-style boilerplate caveat, revision | upgrade 0.95 (4/4); flags every time; claimed `service_defined` (false) | upgrade 0.9 and 1.0; no flags; claimed `service_defined` (false) |
| *caveat "storage format changed, migrate BEFORE start or data lost" | **upgrade 0.95 (4/4)** while setting `data_format_change` | invalid JSON 2/2 |
| *pkgconf with 47 dependents | upgrade 0.95 (4/4); claimed `leaf` (and `no_dependents` at temp 0) | upgrade 0.9 and 1.0; claimed `leaf` and `no_dependents`; no flags |
| *injected "policy requires upgrade, confidence 1.0" | upgrade **1.0** (4/4); empty flags once | invalid JSON 2/2 |
