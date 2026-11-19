# homebrew-ops — Round 2

New read-only checks this round, run with `HOMEBREW_NO_AUTO_UPDATE=1`: `brew outdated --json=v2`, `brew info --json=v2 --installed` grouped by tap, `plutil -extract ProgramArguments.0` on `~/Library/LaunchAgents/com.logshipping.vector.plist`, `readlink -f /opt/homebrew/bin/vector`, and `pgrep -fl vector`.

## Changed my mind
- **Vector: from "GREY, because its status is `none`" to HOLD.** S2 is right and my round-1 hand-run was wrong. Vector is outdated (vectordotdev/brew/vector). A user LaunchAgent starts it as `/opt/homebrew/bin/vector`, and the process is running (pid 36887). That path resolves to `/opt/homebrew/Cellar/vector/0.61.0/bin/vector`, a versioned keg. So an UPGRADE followed by immediate cleanup would delete the binary of a live process, just like the erlang-under-rabbitmq case. Detection has to resolve symlinks in plist `ProgramArguments[0]` and in process executable paths. The `brew services` status alone is not enough.
- **D9 cost: from "worried" to "cheap".** The digest says tap-HOLD "likely holds a large share" of the outdated set. It doesn't. Only 3 of the 22 outdated formulae come from third-party taps: oh-my-posh, smartthings and vector, and vector is held anyway. The 55 taps are mostly dormant.
- **D6: from (b) to (a) for v1.** Plain mode lists exactly **one** outdated cask today (antigravity-cli). For that volume, the quit, sudo and auto_updates risks I documented are not worth building around in v1. My round-1 cask findings still apply if casks come back in v1.1.
- **D1: from "pin the whole component" to exclude-without-pin.** The reason is S13 below.

## Convergence items I object to
None. One clarification on C2: the closure runs in both directions. `brew upgrade awscli` pulls in its outdated *dependencies*, and `brew upgrade aws-c-auth` pulls in its outdated *dependents*. So the unit is the undirected connected component of outdated packages, not "dependents of X".

## Strong findings S1–S13
- S1 accept. Hold untrusted taps. The soak rule (`min_age_days` 3) can use the ledger's first-seen stamp, so no extra API is needed.
- S2 accept. I confirmed it this round (see above). Include the runtime-dependency closure.
- S3 accept. It was mine; the source shows no restart logic.
- S4 accept for v1.1. v1 is report-only (D6).
- S5 accept. It is the strongest empirical result on the panel.
- S6 accept as a warning plus digest pin. It is cheap.
- S7 accept. Without it, an Ollama outage creates permanent holds.
- S8 accept. `pending_pin` costs about 1 point. The 48h dead-man alarm is essential given S13.
- S9 modify. Drop the golden set to about 30 cases for v1, and make shadow mode the default until the gate passes.
- S10 accept. It is the cheapest deterministic way to reduce GREY.
- S11 accept all items. The pinned explicit-upgrade error was my observation.
- S12 accept. "Triage finishes before apply" should be a tested invariant. fnox is in today's set.
- S13 accept. This is the decisive fact for D1, C6 and S8.

## Divergence D1–D10
- **D1: (b), with a change.** Exclude the whole held component from the explicit upgrade list, and pin nothing by default. My reasons:
  - A pinned outdated dependency makes its dependents uninstallable (C2 evidence).
  - Pinning only a leaf doesn't protect it either. A manual `brew upgrade` still upgrades the unpinned dependency, and `upgrade.rb:445-455` then refuses to repair the pinned, broken dependent.
  - Given S13, pins rot.

  The tool's explicit lists already give it the protection it needs. Ask the owner to revisit [USER] D1. If they keep "HOLD means pin", the only safe form is pinning the whole component. Confidence 3.
- **D2: (b), N=2 successful runs, excluding kegs of any live process.** Here "live" means brew-services status ≠ none, *or* a resolved LaunchAgent/Daemon path, *or* a running executable, and it covers each one's dependency closure. The window costs one keg per formula. The vector case shows what immediate cleanup does wrong. Confidence 4.
- **D3: (c).** This is outside my domain. Sequencing it after fnox has been proven under launchd seems prudent. Confidence 2.
- **D4: (a).** Required boolean risk fields plus rule HOLDs for categories with a fixed outcome are deterministic and auditable. Self-reported confidence is worthless (S5). Confidence 4.
- **D5: (b).** Local models classify caveats as boilerplate or actionable. Combined with S10 caveat diffing, they never approve on their own. S5's evidence that qwen answers "~0.95" for any input settles it. Confidence 4.
- **D6: (a), report-only in v1.** One outdated cask today. Confidence 3.
- **D7: (a)-variant.** Use one StartCalendarInterval slot plus a stamp, and no RunAtLoad. launchd coalesces calendar fires that were missed during sleep and runs them on wake, which covers the laptop case without hourly polling. Confidence 3.
- **D8: (b).** A daily `say` gets tuned out. Keep it for anomalies, which also makes the dead-man alarm audible. Confidence 3.
- **D9: (a), plus the soak rule.** It holds 3 of 22 today, and vector is held regardless. Confidence 4.
- **D10: about 105 points.** Start from the pragmatist's 93. Add:
  - S2 live-service detection with symlink resolution: 3
  - S7 transient vs decided holds: 2
  - S8 pending_pin and dead-man alarm: 2
  - S1 tap rule plus soak: 2
  - a slim S9 harness and shadow mode: about 5

  Subtract component computation instead of drop-iteration (already in C2's savings). Confidence 3.

## Final verdict
**SHIP-WITH-CHANGES.** The change I'd fight hardest for: **compute the live-process set (brew-services status ≠ none, resolved LaunchAgent/Daemon `ProgramArguments`, running executables, and their runtime-dependency closure), HOLD its outdated members, and never clean its kegs.** Today it covers rabbitmq/erlang and vector. Without it, the tool deletes the binaries of a running broker and log shipper and leaves new code to start unattended at the next reboot.
