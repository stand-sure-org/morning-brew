# homebrew-ops — Round 1

Evidence: read-only queries on this machine (Homebrew 7.0.14-67-gb43c73f), `brew upgrade --dry-run` with `HOMEBREW_NO_AUTO_UPDATE=1`, and source under `/opt/homebrew/Library/Homebrew`. Anything not marked as observed or as source is my belief.

## Verdict
**SHIP-WITH-CHANGES.** Rules-first, the pin ledger and deferred cleanup are the right shape. But the design models packages one at a time, and brew does not upgrade that way. Three behaviours, each confirmed in source or by dry-run, will cause wrong upgrades or failed upgrades as designed: dependents upgrade with their deps, services are not restarted, and casks get force-upgraded and the running app quit.

## Top 3 must-change

**1. The closure runs both ways. Make the unit the connected component.** (FR-050, D12, DESIGN §7 "leaves first")
- Observed: `brew upgrade --dry-run aws-c-auth` → "Would upgrade 2 dependents: aws-c-s3, awscli". `usage` → also `fnox`. `awscli` → "Would upgrade 2 dependencies".
- Source: `upgrade.rb:300-307` upgrades every outdated installed dependent unless it is pinned.
- Source: `formula_installer.rb:488-512`. A pinned, outdated dependency counts as unsatisfied, because `Dependency#installed?` is false unless the latest version is installed. That makes the dependent uninstallable: "You must `brew unpin X`".
- Source: holding a dependent while upgrading its dependency leaves broken linkage. `upgrade.rb:445-455` refuses to reinstall pinned broken dependents.
- Failure today: aws-c-auth and aws-c-s3 are rule-UPGRADE. awscli is GREY (caveats). If the model holds awscli, either it gets upgraded anyway by the dependents pass (if the pin is not written first), or it is left linked against a changed aws-c-auth.
- Change: build the outdated-package graph from `runtime_dependencies` (already in the info JSON). Decide per connected component, using the most conservative member's verdict: one held member holds the whole component. Upgrade each component with a single `brew upgrade a b c` call. Write all pins before any upgrade. Keep dry-run as a cross-check only. Its text headers are inconsistent ("dependencies:" has a colon, "dependents" does not), so parsing it is brittle. This removes the bounded drop-iteration entirely.

**2. The service rule misses runtime deps and the latent restart.** (FR-030 rules 6/11, FR-057, Q7)
- Source: `upgrade.rb`, `cmd/upgrade.rb` and `formula_installer.rb` contain no service-restart logic. An upgraded service keeps running the old code, and the new code starts unattended at the next login or reboot. That is when a data-format migration would happen, out of anyone's sight.
- Observed: rabbitmq's `beam.smp` runs from `/opt/homebrew/Cellar/erlang/29.1.2/...`, a versioned keg path, not `opt`. An erlang minor bump passes rules as UPGRADE (it is a dependency with few dependents). Immediate cleanup would then delete the keg out from under a live broker.
- Change:
  - (a) HOLD when service status is anything other than `none`. The source has 7 statuses: started, scheduled, stopped, error, unknown, other, none (`services/formula_wrapper.rb:337-351`). Grafana shows `started` with `exit_code 1` right now.
  - (b) Any outdated package in the runtime-dependency closure of a non-`none` service is GREY with cap 0.5, so it effectively holds. Revision-only bumps can stay UPGRADE.
  - (c) Never clean kegs of a running service or its deps until that service has restarted.
  - Also: the DESIGN §5 hand-run lists vector as HOLD. Its status is `none`, so it is GREY. Started services today: etcd, grafana, loki, prometheus, rabbitmq, valkey.

**3. Casks: drop `--greedy`, always pass `--no-quit`, and widen the sudo rule.** (FR-010, FR-030 rules 3-4, FR-056, D14, Q4)
- Source: `cask.rb:510-513`. In 7.0.14, plain `outdated` already includes an `auto_updates` cask when its app bundle is older than the tap version (`auto_updates_bundle_outdated?`). DESIGN §1 says plain mode excludes them. That is wrong.
- Observed: under `--greedy`, ghostty shows 1.2.3 → 1.4.0, but `Ghostty.app` reports `CFBundleShortVersionString` 1.4.0. Greedy outdated entries are stale Caskroom metadata, not real staleness. All 7 REPORTs are noise every morning.
- Observed: naming a cask explicitly bypasses `auto_updates` protection. `brew upgrade --dry-run --cask ghostty` → "Would upgrade" (`cask/upgrade.rb` uses `outdated?(greedy: true)` for named casks).
- Source: brew **quits and reopens running apps by default** (`--no-quit` / `HOMEBREW_NO_UPGRADE_QUIT_CASKS`, `cmd/upgrade.rb:138`). Upgrading ghostty quits the user's terminal.
- Change:
  - Snapshot without `--greedy`. Treat a plain-mode `auto_updates` cask as a normal candidate, since it really is stale.
  - Always pass `--no-quit`, and still DEFER running apps.
  - "pkg ⇒ HOLD" is not enough. Also HOLD on an `installer` artifact (miniforge) and on `uninstall` stanzas with `pkgutil`, `launchctl`, `kext` or `delete` keys. The old version's uninstall runs during the upgrade and can need sudo. pkg casks present: dotnet-sdk, snowflake-snowsql.

## Positions on open questions

1. **Holding deps.** Pinning a dep is sound only if every outdated dependent is held with it. The connected component is the unit. No iteration is needed (see #1). Confidence 5.
2. **Pin ownership.** The ledger is enough if each run reconciles it with the snapshot's `pinned` field:
   - Ledger says tool-pinned but the package is not pinned: the user unpinned it. Release the record and treat it as an approve. Do not re-pin the same version.
   - Pinned with no ledger record: a user pin.
   - Per-version skip: yes.
   - Key on `full_name` and follow `tap_migrations` renames. Observed: Caskroom has 30 directories but info lists 28. The extras are rename leftovers (`posturr`→`dorso`, `redisinsight`→`redis-insight`).
   - Confidence 4.
4. **Casks.** See #3. Not greedy, `--no-quit` plus DEFER, wider sudo rule. Confidence 4.
5. **Cleanup and rollback.** Keep deferred cleanup (N=2), but describe rollback honestly:
   - Formulae only. Each cask keeps a single Caskroom version directory (observed: ghostty, pieces, kopiaui), so cask upgrades cannot be rolled back.
   - Today **no formula in the Cellar has more than one keg**, so the current rollback window is zero.
   - There is no `brew switch` (absent from `cmd/`). Rollback means a manual unlink and relink of the old keg, done for the whole component, or the dependents stay linked against the new dep.
   - Ship a written manual procedure in the README (T26). Do not promise an automated rollback.
   - Confidence 4.
7. **Service rule.** HOLD on status other than `none`, extend it to runtime deps, and exclude kegs of running services from cleanup (see #2). Checking for a running process is a useful extra, not a substitute: Postgres.app runs a non-brew postgres here. Confidence 4.
11. **Version heuristics.** Acceptable. Brew `_N` revision bumps are rebuilds, often for a dependency ABI change. That is safe, but only when the dependency upgrades in the same component, which #1 guarantees. Confidence 3.

## Things nobody asked about

- **Tap trust.** 55 third-party taps are installed. Tap formulae run arbitrary Ruby at install time and may have no bottle. Proposals:
  - A non-core tap goes to at least GREY, or uses an allowlist.
  - "No bottle for this macOS" (`bottle` in the info JSON) → HOLD. A 30-minute source build at 7am under launchd is a bad failure mode.
- **A pinned package named explicitly errors.** Observed: `brew upgrade redis` → "Error: Not upgrading 1 pinned package", with a non-zero exit. FR-051/063 must classify this as a pin conflict, not a retryable failure.
- **Never set `HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK`.** It suppresses the broken-linkage repair (`upgrade.rb:278`). Worth a test asserting that the environment from FR-004 does not contain it.
- **`brew update` upgrades Homebrew itself, untriaged.** Observed: the design recorded 7.0.14-44, and today it is -67. Log `brew --version` in each audit record so behaviour changes can be traced.
- **The tool's own secret broker gets upgraded mid-run.** `usage` → `fnox`. This is harmless only because triage (and L3) finishes before apply. Keep that ordering as an invariant and test it.
- **Cleanup of pinned formulae is safe.** Install-cleanup requires `latest_version_installed?` (`cleanup.rb:349-354`). Bare `brew cleanup` in immediate mode should still exclude ledger-held packages; `HOMEBREW_NO_CLEANUP_FORMULAE` is the built-in way to do that.

## Overbuilt

- **S1 + T08** (bounded drop-iteration plus dry-run parser, 8 points) → replace with component computation from `runtime_dependencies` (3 points). Saves 5.
- **S3 + T24** (OpenAI-compatible adapter): defer to v1.1. Saves 6.
- **T12 REPORT path:** with `--greedy` gone, REPORT reduces to "never name an `auto_updates` cask unless plain mode lists it". Saves about 1.
- **S5:** source already confirms that pinned casks are skipped and that a named pinned cask fails (`cask/upgrade.rb:83-95`). Shrink to a 1-point live confirmation. Saves 1.

Total about 13 points saved, with roughly 3 points of new work for the service-dependency and tap rules.
