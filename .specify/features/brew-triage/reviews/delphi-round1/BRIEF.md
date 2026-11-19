# Delphi Panel — brew-triage — Round 1 Brief

You are one independent panelist in a Delphi review of a design that has NOT been built yet.
Other panelists review the same material through different lenses. You will not see their
answers in this round. Be candid: your job is to find what is wrong, missing, or overbuilt —
not to approve.

## Material (read all four)
`/Volumes/Repos/morning-brew/.specify/features/brew-triage/`
- DECISION.md — scope, decision table (D1–D19, tagged USER vs DESIGNER), approaches
- SPEC.md — FR-001..FR-093 with acceptance criteria
- DESIGN.md — technical design, failure matrix, spikes S1–S6
- PLAN.md — TDD plan, 111 pts implementation + 16 pts spikes

Prior art the design borrows from: `/Volumes/Repos/phlegyas` (AI approval gate). Read as needed.

## What the system is (one paragraph)
A personal macOS tool. launchd runs it each morning: `brew update`, snapshot outdated
packages, deterministic rules classify each (UPGRADE / HOLD / GREY / SKIP), GREY goes through
a model chain (qwen3:4b → glm-4.7-flash via local Ollama → Claude when judgment is needed),
safe things upgrade, the rest get pinned and queued for a human `morning-brew review` command.
One user, one laptop (Apple M5 Max, 64 GB), ~273 leaf formulae, ~30 casks, ~11 brew services
(redis, rabbitmq, valkey, etcd, zookeeper, prometheus, grafana, loki, …).

## Ground rules
- Decisions tagged **[USER]** are the owner's: challenge them only if you see a concrete
  failure they cause, and say so explicitly. **[DESIGNER]** decisions are fully open.
- READ-ONLY. Do not edit the spec docs. Do not run any brew command that mutates state
  (no upgrade/pin/unpin/cleanup/update/services start|stop). Read-only brew queries
  (`brew info --json=v2`, `brew outdated --json=v2`, `brew deps`, `brew uses --installed`,
  `brew services list`) are fine if you want evidence. Do not commit.
- Evidence beats opinion. When you claim something about Homebrew, Ollama, launchd or the
  Anthropic API, say how you know (doc, man page, observed output) or mark it as belief.

## The designer's open questions (answer those relevant to your lens; skip the rest)
1. Holding deps: is pinning a dependency sound when it can block dependents? Unit of decision
   = leaf + upgrade closure? Bounded drop-iteration vs "a held dep holds everything needing it"?
2. Pin ownership / re-triage: is the per-package ledger enough? Should a human "skip" persist
   per-version? How to detect the user pinning/unpinning behind the tool's back?
3. Escalation: is "two agreeing stages to auto-approve GREY" too strict/lax? Are facts_used
   claim-check + risk flags + caps right given miscalibrated small-model confidence? Are caps
   (service 0.5 … caveats 0.85) and the 0.8 threshold defensible?
4. Casks: auto_updates casks report-only vs `--greedy-auto-updates`? Running apps → DEFER or
   HOLD? Is "pkg installer ⇒ HOLD" enough for sudo-needing casks?
5. Cleanup: deferred (after 2 good runs) vs immediate as the user literally asked? What's the
   real rollback story given Homebrew has no clean downgrade?
6. Claude path: sonnet-5-5 default? fnox exec under launchd if it can't unlock
   non-interactively? Claude allowed when Ollama is down (default off)?
7. Service rule: HOLD on status≠none, on any service block, or on a running process?
8. Scheduling: 2 fire times + RunAtLoad + stamp enough for an often-asleep laptop? Exit 75 +
   second slot adequate retry?
9. Scope: no changelog fetching in v1 — is brew metadata alone enough signal, or is a
   grey-area decision without release notes guessing?
10. Prompt injection: beyond caveats, is desc / deprecation reason / L1 rationale shown to
    L2/L3 covered? Does rules-win + post-hoc closure re-check bound the blast radius?
11. Version heuristics: 0.x-minor → GREY and dotted calver treated as semver acceptable, or
    should unknown schemes always be GREY?
12. Stack: Python+uv vs C# for a .NET-background owner? Vendoring phlegyas's sanitizer vs
    depending on it?

## Output — write EXACTLY one file
`/Volumes/Repos/morning-brew/.specify/features/brew-triage/reviews/delphi-round1/<your-panelist-name>.md`

Structure:
```
# <panelist name> — Round 1
## Verdict
One of: SHIP-AS-DESIGNED / SHIP-WITH-CHANGES / REWORK — and 2–3 sentences why.
## Top 3 must-change
Each: the problem, the concrete failure scenario, the proposed change, and which FR/D/section.
## Positions on open questions
For each question you answer: your position (one or two sentences) + confidence 1–5.
## Things nobody asked about
Anything missing from the 12 questions that matters through your lens.
## Overbuilt
What you would cut or defer from v1, with points saved (reference PLAN.md tasks).
```
Keep it under ~1,200 words. Then stop.
