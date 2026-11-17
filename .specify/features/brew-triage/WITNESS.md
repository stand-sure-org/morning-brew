# End-State Witness — brew-triage

**Version:** 1.0
**Date:** 2026-11-17
**Changes:** Initial witness for coordinator-driven execution of PLAN.md v1.2.

The witness is the contract between the implementing agent and the coordinator. An agent's
"done" is a claim; `witness.sh` is the evidence. The coordinator never accepts a task on the
agent's report alone — it re-runs the witness itself on the pushed commit.

## Protocol (per task)

1. Coordinator dispatches one task (T00…T25) to a fresh implementer with: PLAN.md task section,
   Global Constraints, the Interfaces of every task it consumes, and this file.
2. Implementer follows the task's TDD steps, commits on `feat/brew-triage`, then runs
   `.specify/features/brew-triage/witness.sh task Txx` and pastes the full output in its report.
   It may not report done on a WITNESS FAIL.
3. Coordinator independently re-runs `witness.sh task Txx` and reads the diff:
   - every `def test_…` the PLAN declares for the task exists, is collected, and passes;
   - the full unit suite, ruff check and ruff format pass, with zero skips;
   - Global Constraints hold statically (no `shell=True`, no pin/unpin/`--greedy`, no
     `os.environ` writes, `print` only in `cli.py`, every subprocess has `timeout=` and
     `stdin=`, `anthropic` only in the optional extra, gitleaks clean on `main..HEAD`);
   - the diff touches only the task's **Files** (judged by reading; the script prints nothing for this).
4. Then a fresh reviewer checks the task against SPEC/DESIGN (does it mean what the FR says,
   not just pass its own tests). Findings go back to the implementer; repeat 2–4.
5. Only after a coordinator-run PASS + review does the next task start.

Spikes S4 and S6 have no tests; their witness is the recorded finding in the task section
(command run, observed output) and the coordinator re-running the read-only part.

## Final end state (`witness.sh final`)

Everything above for **all** tasks, plus a read-only run against this machine:

| Check | Expected |
|---|---|
| `morning-brew run --dry-run` | exit 0 |
| brew/launchd fingerprint (versions, pins, Cellar, LaunchAgents, launchctl) | identical before and after |
| `explain redis --json` | `skip` / `R02` (owner's deliberate pin) |
| `explain jandedobbeleer/oh-my-posh/oh-my-posh` | `hold` / `R06` (untrusted tap) |
| `explain vectordotdev/brew/vector` | `hold` / `R06` or `R08` (tap; live via user LaunchAgent) |
| `explain rabbitmq` · `explain erlang` | `hold` / `R08` (live service and its dep closure) |
| `explain fnox` | `report`/`defer` / `R05` (self-dependency) |
| every outdated cask | `report` / `R03` |
| aws-c-auth, aws-c-s3, awscli | one component |
| fresh state dir (`WITNESS_FRESH_STATE=1`) | nothing eligible (soak, R10) |
| `install --dry-run` plist | lints; `StartInterval`; no `RunAtLoad`; no `fnox`; runs `.venv/bin/morning-brew`; no key material |
| `eval` | refuses while `golden-real-caveats.md` is unlabeled |

Expectations skip (not fail) for packages that are not outdated when the witness runs —
brew state moves daily. A skip is reported, so the coordinator sees what was not exercised.

## What the witness does not prove

- That Claude's judgments are good: that is the T16 ship gate on the labeled golden set,
  plus the shadow period. The witness only proves the gate exists and is enforced.
- That the launchd job fires on schedule: checked by the Phase 5 checkpoint (throwaway
  label, one kickstart, uninstall), run by the coordinator with the owner's OK because it
  touches launchd.
- Anything that needs a real upgrade. The witness never upgrades; the first real run is the
  owner's call after shadow mode.
