# morning-brew

A launchd-driven Python tool that upgrades only the clearly safe Homebrew
formulae and tells you about the rest.

Rules decide first. A formula upgrades only when it has soaked for 3 days
(`min_age_days`) since the tool first saw the new version and the rules find
nothing wrong. Live services, untrusted taps and major bumps are held. Holds
are recorded in the state directory and are never applied as `brew pin`; the
tool never runs `brew pin`, `brew unpin`, bare `brew upgrade` or `--greedy`.
Casks are report-only: they are listed and never upgraded. Only after a case
passes the rule gate may Claude judge the grey ones (a changed caveat on an
otherwise safe patch or minor bump). A local `qwen3:4b` model gives a
recorded second opinion and never decides anything.

## Install

```sh
uv sync --frozen --extra claude
fnox set ANTHROPIC_API_KEY          # run inside the repo
morning-brew setup                  # confirm trusted taps, seed caveat hashes
morning-brew install --dry-run      # preview the LaunchAgent plist
morning-brew install
```

- `setup` takes a fresh read-only snapshot and shows what it will do: trust
  the non-core taps of your currently outdated formulae, and record the caveat
  hash of every installed formula (existing hashes are never overwritten),
  so only caveats that change later count as news. It writes nothing unless
  you answer `y` (anything else, or end of input, cancels). If a local Ollama
  is up it also prints the current `qwen3:4b` digest and the exact
  `local_digest = "sha256:..."` line to pin it.

- Put the key in the repo's `fnox.toml` with `fnox set`. The tool fetches it
  with `fnox get --config <repo>/fnox.toml`, because a launchd job has no
  working directory (spike S4). The key is never put in the plist, the
  environment, the config or the logs.
- `install` writes `~/Library/LaunchAgents/com.morningbrew.run.plist` and
  bootstraps it. The agent runs `<repo>/.venv/bin/morning-brew run` every
  `start_interval` seconds (default 3600). A run does nothing before the earliest hour (`earliest_hour`,
  default 7) and completes at most once per local day.
- Logs go to `~/Library/Logs/morning-brew/` (`stdout.log`, `stderr.log`).
- `morning-brew run --dry-run` decides and records nothing mutating.

## Config

Optional file: `~/.config/morning-brew/config.toml`. It is refused if it is
group- or world-writable or owned by someone else, and unknown keys are
rejected (`state_dir` cannot be set here). A missing file means defaults.

| Key | Default |
|-----|---------|
| `min_age_days` | `3` |
| `earliest_hour` | `7` |
| `start_interval` | `3600` |
| `dead_man_hours` | `48` |
| `run_budget_minutes` | `20` |
| `cleanup_runs` | `2` |
| `untrusted_text_cap` | `2000` |
| `local_num_predict` | `400` |
| `fnox_timeout_s` | `10` |
| `ollama_timeout_s` | `120` |
| `shadow_runs` | `10` |
| `trusted_taps` | `[]` (list of tap names; `homebrew/core` is always trusted) |
| `chain_mode` | `"shadow"` (or `"live"`, see the ship gate) |
| `local_model` | `"qwen3:4b"` |
| `local_digest` | unset (no local second opinion) |
| `claude_model` | `"claude-sonnet-5-5"` |
| `claude_effort` | `"low"` |
| `claude_key_name` | `"ANTHROPIC_API_KEY"` |
| `stale_hold_days` | `14` |
| `quiet_hours` | `[22, 7]` |
| `fnox_config` | unset (defaults to `<repo>/fnox.toml`) |
| `notify_command` | unset (a non-empty argv list, run for urgent alerts) |

Notes:

- `local_digest` must be set for the local second opinion to run. Read it
  from `ollama list` or from `curl -s http://127.0.0.1:11434/api/tags` (the
  `digest` of the `qwen3:4b` entry) and paste it in. If the tag later points
  at a different digest, the local stage turns itself off with a warning and
  Claude still runs.
- `notify_command` is an extra urgent channel. Urgent alerts always use
  `say` plus the macOS notification; normal ones use the notification only.
  Only normal notifications are silenced by `quiet_hours` (22:00 to 07:00 by
  default); urgent alerts (dead-man, anomalies, a Claude key failure, live
  mode locked) are NOT silenced by quiet hours. They are throttled instead:
  the same alert is not repeated within 6 hours.
- Once a week (the first run at least 7 days after the last one) a hold
  digest goes out through the normal notification: counts of decided,
  blocked-by-dependency and transient holds, and the names and versions of
  stale holds (never hold reasons). Quiet hours delay it to a later run. The
  last send time is `digest.json` in the state directory.
- `start_interval`, `untrusted_text_cap` (the caveat length above which a
  caveat is held as suspicious, and the prompt truncation),
  `local_num_predict`, `ollama_timeout_s` and `fnox_timeout_s` are passed to
  the plist, the triage chain, the Ollama client and `fnox get`.
- `trusted_taps` adds to the taps you confirmed during setup, which are kept
  in `trusted_taps.json` in the state directory.

## Shadow mode and the ship gate

`chain_mode` starts as `"shadow"`: the model chain runs and its verdicts are
recorded as "would be" outcomes, but it approves nothing. `morning-brew
status` shows `shadow runs: N/10` while in shadow mode. A shadow hold is the
chain working as designed, not a failure: it does not make the run degraded,
so the once-per-day gate applies and shadow holds are re-triaged once per
day (on the next day's run), not every hour. Real transient holds (Claude
down, run budget, brew busy, a formula brew lists without info) still make
the run degraded and are retried hourly.

To earn live mode:

1. Label the 10 real caveats in
   `.specify/features/brew-triage/golden-real-caveats.md` (tick `upgrade` or
   `hold` for each).
2. Copy those labels into `expected` in
   `src/morning_brew/triage/golden/cases.jsonl`. `morning-brew eval` refuses
   to run while any case is unlabeled.
3. Run `morning-brew eval` (with `chain_mode = "shadow"`). It runs the whole
   shipped corpus and writes `gate.json`. A pass is recorded only for the
   full shipped corpus with zero false approves (nothing you labelled `hold`
   was approved) and zero inconclusive cases (the model was unavailable).
   Exit code 0 means a pass, 1 means it did not.
4. Set `chain_mode = "live"`.

Any change to the corpus or its labels changes the corpus hash and
invalidates the recorded pass. With `chain_mode = "live"` and no valid pass,
the tool fails closed without stopping: the chain is built in shadow mode (no
model approval is applied), every command still works, and each `run` reports
the urgent anomaly `live locked: gate not passed`. Recovery: run
`morning-brew eval` until it passes (it uses the shadow chain meanwhile), or
set `chain_mode` back to `"shadow"`.

Tests: a plain `uv run pytest` is hermetic (no brew, Ollama, network or API
key): `pyproject.toml` sets `addopts = '-m "not integration"'`, and
`uv run pytest -m "not integration"` is the same thing spelled out.
`uv run pytest -m integration` is opt-in. It touches the real `brew`
read-only (`run --dry-run` with a temporary state directory and an explicit
temporary config file, so your real config and state are never read; only
an allowlist of read-only commands is executed, anything else is blocked and
fails the test) and a real local Ollama. The Ollama round trip requires a
pinned digest in `BREW_TRIAGE_OLLAMA_DIGEST`; without it that test fails
closed.

## Daily use

- `morning-brew status` prints the last run summary. It recomputes the
  dead-man alarm from the run stamp at the moment you ask (no full success
  within `dead_man_hours`, 48 by default, even if no run summary exists yet)
  and exits 1 when it is on, 0 otherwise, so it can sit in a prompt or a
  check.
- `morning-brew explain <pkg> [--json]` shows the audit trail for a package,
  or with `--json` its latest decision, rule id, component and hold kind.
- `morning-brew review` walks the decided holds one by one: `a` approve
  (upgrades the whole component with an explicit `brew upgrade -- ...`),
  `s` skip this version (it will not be auto-upgraded, and no pin is made),
  `d` defer. When the component has more than the held package, approval
  always lists every member, flags each one the rules would not upgrade on
  their own (with the rule id, for example a soaking or untrusted-tap
  member) or that has a hold or a skip of yours, and needs an exact `y`
  (anything else aborts and keeps the hold); a component with a pinned
  member is refused outright; brew's
  own `upgrade --dry-run` plan must match the component exactly. If a member
  is live (a running service or process), approval also needs you to type
  `yes`. `review` holds the run lock for its whole duration: while a
  scheduled run is in progress it says so and exits 75.

## Self-dependencies

`fnox` and the Python the tool runs on are never upgraded by the tool in a
run: it cannot safely replace what it is running on. When one is due, `status`
prints it under "Run these yourself" as the exact command, for example:

```sh
brew upgrade -- fnox python@3.13
```

Run it yourself, then `uv sync --frozen --extra claude` if the Python changed.

## Rollback

There is no automated rollback. Homebrew keeps no old version by default and
has no `brew switch`, so the rollback window is zero unless an old keg is
still on disk.

- The tool sets `HOMEBREW_NO_INSTALL_CLEANUP=1` for brew, and runs
  `brew cleanup -- <pkg>` itself only after 2 full successful runs
  (`cleanup_runs`), and never for anything live. Until then the previous keg
  usually stays in `$(brew --cellar)/<formula>/`.
- Recovery is manual and best effort. Check what is on disk with
  `ls "$(brew --cellar <formula>)"`. Where a versioned formula exists
  (`<formula>@<n>`), `brew install <formula>@<n>` is the supported way back.
  Otherwise reinstall from a backup. This tool does not
  attempt any of this for you.
- Services are not restarted by an upgrade; the new code runs at the next
  restart or login.
- The Claude key may be a 7-day key. When it expires the run raises an
  urgent anomaly ("claude auth failure"). Replace it with
  `fnox set ANTHROPIC_API_KEY` inside the repo. Until then grey cases stay
  held for a later run.

## Recovery

State lives in `~/.local/state/morning-brew/` (directory mode 0700, files
0600):

| Path | Contents |
|------|----------|
| `holds/` | one `<package>.json` per hold |
| `seen.json` | first-seen dates, caveat hashes and cleanup counters |
| `stamp.json` | last attempt and last full success (dead-man input) |
| `status.json` | last run summary shown by `status` |
| `audit.jsonl` | append-only decision log behind `explain` |
| `gate.json` | the recorded eval pass |
| `urgent_throttle.json` | urgent notification throttle |
| `trusted_taps.json` | taps you confirmed during setup |
| `digest.json` | when the weekly hold digest was last sent (`last_digest_ts`) |
| `run.lock` | single-run lock (also held by `review`) |

- Clear one hold: delete its file, e.g. `rm ~/.local/state/morning-brew/holds/<package>.json`.
  A package whose name contains `/` is stored with `%2F`. The next run
  re-decides it from scratch. Do not edit `audit.jsonl`.
- Start clean: stop the agent (`morning-brew uninstall`) and remove the state
  directory. Everything is rebuilt; every version counts as first seen again
  and soaks 3 days.
- Uninstall: `morning-brew uninstall` boots out the LaunchAgent and deletes
  the plist. It leaves state and logs in place.

## Built by Claude Code

🤖 This repo was built entirely by Claude Code.
