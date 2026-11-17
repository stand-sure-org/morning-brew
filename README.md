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
morning-brew install --dry-run      # preview the LaunchAgent plist
morning-brew install
```

- Put the key in the repo's `fnox.toml` with `fnox set`. The tool fetches it
  with `fnox get --config <repo>/fnox.toml`, because a launchd job has no
  working directory (spike S4). The key is never put in the plist, the
  environment, the config or the logs.
- `install` writes `~/Library/LaunchAgents/com.morningbrew.run.plist` and
  bootstraps it. The agent runs `<repo>/.venv/bin/morning-brew run` every
  3600 seconds. A run does nothing before the earliest hour (`earliest_hour`,
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
  `say`; normal ones use a macOS notification. Urgent alerts are throttled
  and respect `quiet_hours` (22:00 to 07:00 by default).
- `trusted_taps` adds to the taps you confirmed during setup, which are kept
  in `trusted_taps.json` in the state directory.

## Shadow mode and the ship gate

`chain_mode` starts as `"shadow"`: the model chain runs and its verdicts are
recorded as "would be" outcomes, but it approves nothing. `morning-brew
status` shows `shadow runs: N/10` while in shadow mode.

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
every command (including `status`) stops with an error. Recovery: set
`chain_mode` back to `"shadow"`, run `morning-brew eval`, then switch back.

Tests: `uv run pytest -m "not integration"` is hermetic (no brew, Ollama,
network or API key). `uv run pytest -m integration` is opt-in (the marker is
not deselected by default, so a plain `uv run pytest` includes it). It touches
the real `brew` read-only (`run --dry-run`, asserting no mutating command is
issued; state goes to a temporary directory) and a real local Ollama. The
Ollama round trip requires a pinned digest in `BREW_TRIAGE_OLLAMA_DIGEST`;
without it that test fails closed.

## Daily use

- `morning-brew status` prints the last run summary. It exits 1 when the
  dead-man alarm is on (no full success within 48 hours), 0 otherwise, so it
  can sit in a prompt or a check.
- `morning-brew explain <pkg> [--json]` shows the audit trail for a package,
  or with `--json` its latest decision, rule id, component and hold kind.
- `morning-brew review` walks the decided holds one by one: `a` approve
  (upgrades the whole component with an explicit `brew upgrade -- ...`),
  `s` skip this version (it will not be auto-upgraded, and no pin is made),
  `d` defer. If a member of the component is live (a running service or
  process), approval needs you to type `yes`.

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
| `run.lock` | single-run lock |

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
