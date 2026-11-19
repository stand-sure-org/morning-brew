#!/usr/bin/env bash
# End-state witness for brew-triage (PLAN.md v1.2).
#
#   witness.sh task T07     per-task gate: the implementer runs it before reporting done;
#                           the coordinator re-runs it independently before accepting.
#   witness.sh final        everything in `task` for ALL tasks, plus the read-only
#                           machine witness (no brew/launchd state may change).
#
# Exit 0 = every check passed. Any failure prints WITNESS FAIL lines and exits 1.
# The witness never mutates brew, launchd, or git. It is evidence, not a fixer.
set -uo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
SPEC_DIR="$ROOT/.specify/features/brew-triage"
PLAN="$SPEC_DIR/PLAN.md"
BRANCH="feat/brew-triage"
fails=0
pass() { printf '  ok    %s\n' "$1"; }
fail() { printf '  FAIL  %s\n' "$1"; fails=$((fails + 1)); }
hdr()  { printf '\n== %s\n' "$1"; }

cd "$ROOT" || exit 1

# Test names a task's Step 1 declares: `def test_xxx` inside that task's section.
task_tests() {
  awk -v t="### Task $1 " 'index($0,t)==1{on=1;next} /^### Task /{on=0} on' "$PLAN" \
    | grep -oE 'def test_[A-Za-z0-9_]+' | sed 's/^def //' | sort -u
}
all_tasks() { grep -oE '^### Task T[0-9]{2}' "$PLAN" | awk '{print $3}'; }

check_repo() {
  hdr "repo"
  [ "$(git branch --show-current)" = "$BRANCH" ] && pass "on $BRANCH" || fail "not on $BRANCH"
  [ -z "$(git status --porcelain -- src tests pyproject.toml uv.lock)" ] \
    && pass "src/tests committed (clean)" || fail "uncommitted changes under src/ tests/ pyproject/uv.lock"
  git merge-base --is-ancestor main HEAD 2>/dev/null && pass "branch descends from main" || fail "branch does not descend from main"
}

check_tests_declared() {   # $@ = task ids
  hdr "declared tests exist and pass ($*)"
  local missing=() ids=() t name
  for t in "$@"; do
    while read -r name; do
      [ -z "$name" ] && continue
      if grep -rqE "def ${name}\b" tests 2>/dev/null; then ids+=("$name"); else missing+=("$t:$name"); fi
    done < <(task_tests "$t")
  done
  [ ${#missing[@]} -eq 0 ] && pass "all ${#ids[@]} declared tests present" \
    || fail "declared in PLAN but missing from tests/: ${missing[*]}"
  if [ ${#ids[@]} -gt 0 ]; then
    local expr; expr="$(IFS='|'; echo "${ids[*]}" | sed 's/|/ or /g')"
    local out; out="$(uv run pytest -q -m 'not integration' -k "$expr" 2>&1)"; local rc=$?
    [ $rc -eq 0 ] && pass "declared tests pass" || { fail "declared tests fail"; printf '%s\n' "$out" | tail -20; }
    # A declared test that pytest collected zero times is a silent skip, not a pass.
    local collected; collected="$(uv run pytest -q --collect-only -m 'not integration' -k "$expr" 2>/dev/null | grep -c '::')"
    [ "$collected" -ge "${#ids[@]}" ] && pass "collected $collected >= ${#ids[@]} declared" \
      || fail "collected $collected < ${#ids[@]} declared (renamed, skipped, or marked integration?)"
  fi
}

check_suite() {
  hdr "suite, lint, format"
  local out; out="$(uv run pytest -q -m 'not integration' 2>&1)"
  [ $? -eq 0 ] && pass "full unit suite: $(printf '%s' "$out" | tail -1)" || { fail "unit suite"; printf '%s\n' "$out" | tail -25; }
  printf '%s' "$out" | grep -qE '[0-9]+ skipped' && fail "skipped tests present (skips hide failures)" || pass "no skips"
  uv run ruff check -q && pass "ruff check" || fail "ruff check"
  uv run ruff format --check -q && pass "ruff format" || fail "ruff format --check"
}

check_invariants() {   # PLAN Global Constraints, enforced statically on src/
  hdr "invariants (Global Constraints)"
  [ -d src/morning_brew ] || { fail "src/morning_brew missing"; return; }
  local s=src/morning_brew
  ! grep -rnE 'shell\s*=\s*True' $s && pass "no shell=True" || fail "shell=True present"
  ! grep -rnE "['\"](pin|unpin)['\"]|--greedy" $s && pass "never pins/unpins, no --greedy" || fail "pin/unpin/--greedy argv in src"
  ! grep -rn 'HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK' $s && pass "dependents check never disabled" || fail "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK in src"
  ! grep -rnE 'os\.environ\[[^]]*\]\s*=|os\.environ\.(update|setdefault)|putenv' $s && pass "never writes os.environ" || fail "src writes os.environ (key could leak to children)"
  ! grep -rnE '^\s*print\(' $s --include='*.py' | grep -v '/cli.py:' && pass "print only in cli.py" || fail "print() outside cli.py"
  ! grep -rnE '^\s*(import|from)\s+(httpx|requests|ollama|click|typer|tomli_w|tomlkit)(\s|\.|$)' $s --include='*.py' && pass "no banned deps imported" || fail "banned dependency imported"
  # every subprocess call site must pass timeout= and stdin=
  local bad; bad="$(grep -rnE 'subprocess\.(run|Popen|check_output|call)\(' $s | while IFS=: read -r f l _; do
      ctx="$(sed -n "${l},$((l+8))p" "$f")"
      printf '%s' "$ctx" | grep -q 'timeout' && printf '%s' "$ctx" | grep -q 'stdin' || echo "$f:$l"; done)"
  [ -z "$bad" ] && pass "subprocess calls carry timeout= and stdin=" || fail "subprocess without timeout/stdin: $bad"
  python3 -I -c '
import sys, tomllib
p = tomllib.load(open("pyproject.toml", "rb"))["project"]
core = any("anthropic" in d for d in p.get("dependencies", []))
extra = any("anthropic" in d for d in p.get("optional-dependencies", {}).get("claude", []))
sys.exit(0 if extra and not core else 1)' 2>/dev/null \
    && pass "anthropic only in optional extra [claude]" || fail "anthropic missing from extra [claude] or present in core dependencies"
  command -v gitleaks >/dev/null && { gitleaks git --log-opts="main..HEAD" --redact --no-banner -l error >/dev/null 2>&1 \
    && pass "gitleaks clean on main..HEAD" || fail "gitleaks findings on main..HEAD"; }
}

snapshot_machine() {   # read-only fingerprint of everything the tool must not touch in a dry run
  {
    HOMEBREW_NO_AUTO_UPDATE=1 brew list --versions 2>/dev/null
    echo "--pinned"; brew list --pinned 2>/dev/null
    echo "--cellar"; ls -1 /opt/homebrew/Cellar/*/ 2>/dev/null
    echo "--agents"; ls -1 "$HOME/Library/LaunchAgents" 2>/dev/null
    echo "--launchctl"; launchctl list 2>/dev/null | awk '{print $3}' | grep -i morning || true
  } | shasum -a 256 | cut -d' ' -f1
}

expect() {   # expect <pkg> <decision-regex> <rule-regex>   (skips when pkg is not outdated now)
  local pkg="$1" dec="$2" rule="$3" j
  j="$(uv run morning-brew explain "$pkg" --json 2>/dev/null)"; local rc=$?
  if [ $rc -eq 2 ]; then printf '  skip  %s (no record: not outdated now)\n' "$pkg"; return; fi
  local got; got="$(printf '%s' "$j" | python3 -I -c 'import json,sys;d=json.load(sys.stdin);print(d["decision"],d["rule_id"],d.get("component") or "-")')"
  set -- $got
  [[ "$1" =~ ^($dec)$ && "$2" =~ ^($rule)$ ]] && pass "$pkg -> $1 $2" || fail "$pkg -> got '$got', want decision ~ $dec, rule ~ $rule"
}

check_machine() {
  hdr "machine witness (read-only dry run)"
  command -v brew >/dev/null || { fail "brew not found"; return; }
  local before after; before="$(snapshot_machine)"
  local out; out="$(uv run morning-brew run --dry-run 2>&1)"; local rc=$?
  [ $rc -eq 0 ] && pass "run --dry-run exit 0" || { fail "run --dry-run exit $rc"; printf '%s\n' "$out" | tail -15; }
  after="$(snapshot_machine)"
  [ "$before" = "$after" ] && pass "brew/launchd fingerprint unchanged" || fail "dry run CHANGED machine state"

  # Expectations from the Delphi evidence (DESIGN §5), in rule-precedence order (SPEC FR-030).
  expect redis 'skip' 'R02'                                  # owner's deliberate pin
  expect jandedobbeleer/oh-my-posh/oh-my-posh 'hold' 'R06'   # untrusted tap (unless trusted at setup)
  expect vectordotdev/brew/vector 'hold' 'R06|R08'           # tap, and live via user LaunchAgent
  expect rabbitmq 'hold' 'R08'                               # live service
  expect erlang 'hold' 'R08'                                 # dep closure of live rabbitmq
  expect fnox 'report|defer' 'R05'                           # self-dependency
  local cask; for cask in $(HOMEBREW_NO_AUTO_UPDATE=1 brew outdated --cask --quiet 2>/dev/null); do expect "$cask" 'report' 'R03'; done

  # The aws trio must share one component when all three are outdated.
  local comps; comps="$(for p in aws-c-auth aws-c-s3 awscli; do uv run morning-brew explain "$p" --json 2>/dev/null \
      | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("component") or "-")'; done | sort -u)"
  local n; n="$(printf '%s\n' "$comps" | grep -c .)"
  [ "$n" -le 1 ] && pass "aws trio in one component (${comps:-not outdated})" || fail "aws trio split across components: $comps"

  # Soak: on a fresh state dir nothing may be eligible (everything first seen today -> R10 DEFER).
  if [ "${WITNESS_FRESH_STATE:-0}" = 1 ]; then
    local elig; elig="$(uv run morning-brew status 2>/dev/null | grep -ciE '^\s*eligible|would upgrade: [1-9]')"
    [ "$elig" -eq 0 ] && pass "fresh state: nothing eligible (soak)" || fail "fresh state: something eligible despite soak"
  fi

  hdr "surfaces"
  local plist; plist="$(uv run morning-brew install --dry-run 2>/dev/null)"
  printf '%s' "$plist" | plutil -lint - >/dev/null 2>&1 && pass "plist lints" || fail "install --dry-run plist does not lint"
  printf '%s' "$plist" | grep -q '<key>StartInterval</key>' && pass "StartInterval present" || fail "no StartInterval"
  printf '%s' "$plist" | grep -q 'RunAtLoad' && fail "RunAtLoad present" || pass "no RunAtLoad"
  printf '%s' "$plist" | grep -q 'fnox' && fail "plist wraps fnox" || pass "plist does not wrap fnox"
  printf '%s' "$plist" | grep -q '.venv/bin/morning-brew' && pass "runs venv binary directly" || fail "plist not pointing at .venv/bin/morning-brew"
  printf '%s' "$plist" | grep -qiE 'sk-ant|api[_-]?key' && fail "plist contains key material" || pass "plist has no key material"
  local e; uv run morning-brew eval >/dev/null 2>&1; e=$?
  if grep -qE -- '- \[x\] (upgrade|hold)' "$SPEC_DIR/golden-real-caveats.md"; then
    printf '  info  eval exit %s (owner labels present; gate result is evidence, not a witness failure)\n' "$e"
  else
    [ $e -ne 0 ] && pass "eval refuses while real caveats are unlabeled" || fail "eval ran with unlabeled cases"
  fi
}

case "${1:-}" in
  task)  [ -n "${2:-}" ] || { echo "usage: $0 task T07"; exit 2; }
         check_repo; check_tests_declared "$2"; check_suite; check_invariants ;;
  final) check_repo; check_tests_declared $(all_tasks); check_suite; check_invariants; check_machine ;;
  *)     echo "usage: $0 task <Txx> | final"; exit 2 ;;
esac

printf '\n'
[ $fails -eq 0 ] && { echo "WITNESS PASS (${1}${2:+ $2})"; exit 0; }
echo "WITNESS FAIL: $fails check(s)"; exit 1
