# pragmatist — Round 1

## Verdict
**SHIP-WITH-CHANGES.** The rules-first core is right and settles about 80% of candidates. The design assumes a human will review holds, and the evidence says that human does not. Redis was pinned by hand on 2025-04-30 (pin symlink mtime) and still holds 7.2.7 against 8.10.3, about 19 months later. The design needs a plan for a queue nobody reviews, and it can ship at about 93 points instead of 127.

(Evidence note: `brew outdated --json=v2` shows 22 formulae today, not 21.)

## Top 3 must-change

**1. The holds queue will rot silently (FR-052, FR-054, DESIGN §7).**
- **Problem.** If `review` is never run, three things pile up.
  - A major bump that is re-triaged gets HOLD again, so it stays pinned forever.
  - FR-054 says the hold is "not re-reported as new", so the rot is silent.
  - Each pinned dependency also holds its dependents.
- **Failure scenario.** Six months in there are about 15 tool pins, several of them shared deps, so leaves are "blocked by pinned dependency" every day. The machine is stale but looks managed. When the owner finally runs a manual `brew upgrade foo`, it trips over pins they never set.
- **Change.**
  - Every summary and `status` shows the hold count, the oldest hold's age, and the blocked-by-dependency count.
  - Holds older than 30 days (configurable) escalate as "stale" in the notification.
  - `status --short` is built so it can run from a shell-startup hook.
- **On D1 [USER] (HOLD means pin).** FR-051 already upgrades explicit lists only, so a pin on a held *dependency* does not protect the tool's own runs. Its only effects are blocking dependents and surprising the owner. I suggest asking the user whether dependency holds should be excluded without a pin. Risk of that change: a manual `brew upgrade` would pull the dependency in.

**2. Caps below the threshold create dead GREY branches that still cost three model calls (FR-041, FR-046, DESIGN §6).**
- **Problem.** These caps sit below the 0.8 approve threshold: unparseable 0.7, deprecated 0.6, high-dependent 0.7, service 0.5. FR-041 clamps confidence before comparing it, and SPEC does not exempt L3. So no stage can ever be "sure" in those categories.
- **Failure scenario.** The warp cask's unparseable version walks qwen, then glm (a 19 GB load), then Claude, every run, and always ends in HOLD. That is a guaranteed cost for a guaranteed answer, and it adds to #1.
- **Change.** Either make these categories rule HOLDs, or explicitly exempt L3 from caps so that Claude can say yes. FR-046's wording ("on a model's word alone") suggests two agreeing models could approve, but clamping prevents it.

**3. Cleanup: do what the user asked; cut deferred mode (FR-057, D16).**
- **Problem.** No formula in the Cellar has more than one keg today (counted). `brew commands` has no switch, downgrade or rollback command (checked). Rolling back to an old keg means manual `brew link` surgery that nobody will do at 7 a.m. The deferred window protects a path nobody uses, and it costs a per-package age ledger and a run counter.
- **Change.** Default to `immediate` (the user's literal D10), still with an explicit package list.
- **Risk.** A bad upgrade loses its previous keg the same morning. Recovery means reinstalling from a versioned formula, if one exists.

## Positions on open questions

| # | Position | Conf. |
|---|---|---|
| 1 | Use one dry-run pass and drop held members, with no iteration loop. FR-055 verify catches leaks afterwards. "A held dep holds everything needing it" is fine as long as #1 makes it visible. | 3 |
| 2 | The ledger is enough. If the ledger says tool-pinned and brew says unpinned, treat that as the human releasing the hold: delete the record and do not re-pin that version. | 4 |
| 3 | Two agreeing stages is strict enough. Fix #2 first, then tune the caps from about 4 weeks of JSONL. Today they are guesses. | 3 |
| 4 | Casks REPORT-only in v1. 7 of 8 greedy-outdated casks are auto_updates, and plain mode shows 1. Pkg/DEFER/cask-pin machinery for one cask is not proportionate. | 4 |
| 5 | Immediate cleanup (#3). | 4 |
| 6 | Stage Claude to v1.1. Add it once S4 proves fnox works under launchd and the audit log shows real terminal-unsure volume. D2 is [USER], so this is sequencing, not cutting. Keep Claude off when Ollama is down. | 3 |
| 7 | HOLD when status ≠ none. A process check adds plumbing for little gain. | 3 |
| 8 | Adequate as designed. | 4 |
| 9 | Brew metadata is enough for v1. | 3 |
| 12 | Python is fine. Vendor the sanitizer. | 4 |

## Things nobody asked about

- **The tool upgrades its own toolchain.**
  - `fnox` 1.48.0 is a brew formula and is in today's UPGRADE set. So are `python@3.13` and `python@3.14`.
  - The plan runs `fnox exec -- morning-brew run`, so fnox can be replaced while it is the parent process. I believe (untested) the running binary survives, but the next run's L3 key could break.
  - Ollama (`/usr/local/bin`) and uv (`~/.local/bin`) are not brew-managed (checked).
  - **Change:** upgrade the tool's own dependencies last, or set them to REPORT in config.
- **Notifications are the only UI, and `say` is the wrong default.**
  - A run triggered on wake speaks aloud in a meeting or a café.
  - If the owner isn't in the room, the summary is lost.
  - **Change:** default to a silent macOS notification plus a persistent `status` file, and use `say` only for anomalies.
- **Pin surprise.** Brew pins carry no comment, so "foo is pinned" gives no clue that morning-brew set it. Have `status` map `brew list --pinned` to the ledger, and document this in the README.
- **No success metric.** Define it before building: the share of outdated packages that are current within 7 days, plus the hold-count trend. Without it, the evidence gate for the deferred features in the next section never fires.

## Overbuilt

| Cut / defer | PLAN | Pts | Risk of the cut |
|---|---|---|---|
| OpenAI-compatible adapter | T24, S3 | 6 | None for v1 |
| Casks REPORT-only | T12, S2, S5 | 7 | The one non-self-updating cask goes stale (reported) |
| Single-pass closure | T08 5→2 | 3 | A second-order leak is flagged after the fact instead of prevented |
| Review CLI → `status` + one-shot `release`/`skip <pkg>` | T23 5→2 | 3 | Less guided approval; minor if review is rarely run |
| Immediate cleanup only | part of T11 | 2 | See #3 |
| Notifiers: notification + `say` on anomaly, no CommandNotifier | T22 3→1 | 2 | No Hue lights until added later |
| Drop `explain` (use `jq` on the JSONL) | part of T04 | 1 | Slightly worse forensics |
| Dead GREY branches → rules | part of T18 | 1 | None; they could never upgrade anyway |
| **Stage L3 to v1.1** | T19, S6, S4 | 11 deferred | Packages L2 is unsure about become pins, which feeds #1 |
| **Add:** hold aging + stale-hold summary | new | +2 | — |

**Net:** about 25 points cut, 11 deferred and 2 added, so v1 is about 93 points, with Claude about +11 once the evidence justifies it. What stays is the part that runs every morning without anyone watching. What goes is mostly the interactive part, which the owner has shown they don't use.
