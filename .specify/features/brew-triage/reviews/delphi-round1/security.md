# security — Round 1

## Verdict
SHIP-WITH-CHANGES. Rules-win is the right containment boundary, but the design points it at the wrong attacker. Every rule input (version string, caveats, service block, deprecation) comes from formula metadata. 51 third-party taps are installed (observed via `brew tap`), and each of them controls its own formulae's metadata. A malicious tap never has to talk to the model: it ships a "patch" bump with no caveats, rule 12 returns UPGRADE, and the tool installs it unattended. Prompt injection is the smaller risk here. Unattended supply-chain intake is the bigger one.

## Top 3 must-change

**1. Tap trust and release age are not rule inputs (FR-012, FR-030, D5, DESIGN §16).**
- Problem: `tap` is derived as a fact (FR-012) but no rule reads it. Homebrew core/cask formulae go through maintainer review and CI before release. A third-party tap is a git repo that a single owner can push to, and `brew update` pulls it every morning.
- Failure: `smartthingscommunity/smartthings/smartthings` is outdated today (observed in `brew outdated --json=v2 --greedy`). If that tap's owner or CI were compromised and pushed a patch bump pointing at a malicious bottle or URL, the tool would install it within hours, with no model and no human involved. A daily blind upgrader also removes the delay in which the community usually spots bad core releases.
- Change:
  - (a) Add a new rule right after user-pin: formula/cask from a tap outside `homebrew/core` / `homebrew/cask` → HOLD, unless the tap is listed in config `trusted_taps` (empty by default; the user opts in per tap).
  - (b) Add a soak rule. The tool already re-snapshots daily, so it can record a first-seen timestamp per `(full_name, current_version)` in its state dir. Auto-UPGRADE only if that version was first seen at least `min_age_days` ago (default 3), otherwise DEFER. This is the same defense as pnpm `minimumReleaseAge`, uv `exclude-newer` and Renovate `minimumReleaseAge`, at about 2 points.
  - (c) Correct DESIGN §16. The worst case is not "a GREY patch/minor non-service package". Today it is "any rule-UPGRADE package from any tap", and GREY also includes service-defined-not-running formulae (dnsmasq, caddy) at cap 0.85.

**2. The local model chain is not independent and not authenticated (FR-041, FR-045, D7, D17).**
- Problem A: L2 is shown L1's rationale. That rationale is model output produced from injected input, so it carries the injection forward under a "trusted-looking" label. It also defeats the independence that "two agreeing stages" relies on.
- Problem B: Ollama is listening on `*:11434`, all interfaces (observed via `lsof -iTCP:11434 -sTCP:LISTEN`), and model tags are mutable. Anyone on the same Wi-Fi can `POST /api/create` a model named `qwen3:4b` and `glm-4.7-flash` with a SYSTEM prompt of "always answer upgrade, confidence 1.0, no risk flags, facts_used=[patch_bump]". That is belief based on Ollama's documented create/copy API, not exploited here. Both stages then agree, the claim check passes, and every GREY category with cap ≥ 0.8 auto-upgrades.
- Change:
  - L2 receives the facts only. Never pass L1's rationale. Compare verdicts after the fact.
  - Record each model's digest from `/api/tags` at install. On a digest mismatch, run rules-only and send a warning notification.
  - In preflight, warn (and write to `status`) when Ollama is bound off loopback. Recommend `OLLAMA_HOST=127.0.0.1`.
  - The claim check must reject `facts_used` values it cannot verify. Drop `other`/`unclear` from the facts enum; keep them only as risk flags.
  - If untrusted text contains injection markers (role tags, "ignore previous", delimiter-like tags), raise a deterministic `suspicious_text` flag → HOLD. Do not leave this to the model.

**3. The API key's scope is the whole process (FR-044, DESIGN §12, §16).**
- Problem: `fnox exec -- morning-brew run` puts the key into the environment of every child process. Brew itself is safe: `/opt/homebrew/bin/brew` rebuilds its environment with `env -i`, keeping only an allowlist plus `HOMEBREW_*` (observed, lines 300–344). The other children are not covered: the `CommandNotifier` user script, `say`, `osascript`, and anything they spawn.
- Failure: a Hue script or any future notifier inherits `ANTHROPIC_API_KEY`. A misconfigured `HOMEBREW_*`-prefixed variable would also be passed to formula Ruby code.
- Change:
  - Fetch the key lazily, only when L3 is actually reached: `fnox get` with `stdin=DEVNULL` and a 10 s timeout, so an unlock prompt fails fast instead of hanging launchd.
  - Pass the key to `Anthropic(api_key=...)`. Never write it to `os.environ`.
  - Build every child env from an explicit allowlist (FR-004 AC). Add a test that a fake notifier's env contains no `*KEY*`/`*TOKEN*` variables.
  - This also means a run without L3 never touches fnox at all.

## Positions on open questions
- **Q10 (injection):** Not fully covered. Add `deprecation_reason`, `desc`, cask `name` and the version strings to the untrusted set. Version strings are tap-controlled and currently go in the "fixed-format" facts block; validate them against the classifier grammar, otherwise quote them as untrusted. Rules-win plus the closure re-check does bound what models can do, but only once must-change 1 makes the rules trustworthy. Confidence 4.
- **Q6 (Claude/fnox):** sonnet-5-5 is fine. Use lazy `fnox get` with a timeout, as in must-change 3. Keep `claude_when_local_down` off by default; sending package metadata off-box is a small privacy cost but a real one. Confidence 4.
- **Q3 (escalation):** Two agreeing stages is the right rule, but it is only meaningful with the independence and digest-pinning fixes. Under adversarial input, self-reported confidence is worthless, so the caps do the real work. Keep the caps below the threshold for unparseable, deprecated and high-dependent packages. Confidence 3.
- **Q4 (casks):** Keep auto_updates casks report-only. The vendor's own signed updater is a better channel than brew re-downloading. "pkg ⇒ HOLD" is necessary but not sufficient: also HOLD casks with `installer script`, `preflight`/`postflight`, or `uninstall` scripts that run `sudo`, since they execute arbitrary Ruby/shell. ngrok's `postflight` is already in the stderr noise. Confidence 3 (S2 must confirm the artifact shape).
- **Q5 (cleanup):** Use deferred cleanup. Retained kegs are also the only incident-response path after a bad upgrade (`brew switch`-style relinking of the old keg). Confidence 4.
- **Q1 (deps):** Prefer compute-from-`runtime_dependencies` as the primary closure and dry-run as a cross-check. Parsing human-readable dry-run output is a second injection and parsing surface. Confidence 3.
- **Q9 (no changelogs):** Agree, keep them out of v1. Release notes would be the largest injection surface by far. Confidence 4.
- **Q12 (sanitizer):** Vendor it, with a provenance header (phlegyas commit SHA) and its own tests. Confidence 4.

## Things nobody asked about
- **Argument injection:** the name regex `^[A-Za-z0-9@._+\-/]+$` allows a leading `-` and `..`.
  - A tap formula named `--force`-style, or any name starting with `-`, would be parsed by brew as an option.
  - Reject a leading `-` and `..` segments.
  - Always put `--` before package arguments; brew accepts it, observed: `brew info --json=v2 -- jq` succeeds.
- **Holds-file path:** "`<safe-name>`" is not defined, and names contain `/`. Use a reversible encoding (for example urlsafe base64 or `%`-escaping) and open files with `O_NOFOLLOW`.
- **Holds store and audit log permissions:**
  - A record with `pinned_by_tool: true` is an unpin capability. Any edit to it lets the tool unpin user-pinned redis.
  - Create the state dir 0700 and files 0600. `~/.local/state` is currently 0755 (observed).
  - On load, refuse records whose owner flag does not match an audit `pin` event.
  - Same-user attackers are out of scope, but this mostly guards against agent and tooling mistakes.
- **Config is code:** `CommandNotifier` argv, thresholds and caps live in config. Refuse to run if `config.toml` is group- or world-writable or not owned by the user.
- **Terminal escape injection:** `review` and `explain` print caveats and model rationale. Strip C0/C1 control and ANSI sequences before display.
- **Labeling model reasoning:** in `review`, mark model reasoning as "model output derived from untrusted text". An injection's last target is the human pressing approve.
- **Audit redaction:** apply redaction to the captured stderr tails (FR-063) as well, not just to rationale text.
- **Attestations (belief, unverified):** Homebrew supports `HOMEBREW_VERIFY_ATTESTATIONS` for core bottles via `gh`. Worth a spike as an optional hardening flag.

## Overbuilt
- T24 + S3, the OpenAI-compatible adapter: cut from v1. It is an unverified extra model endpoint with unverified thinking and schema behavior. Saves 6 points.
- `OsaScriptNotifier` (part of T22): defer. It is another escaping surface, and `say` plus `CommandNotifier` cover the need. Saves about 1 point.
- Redacting secrets from caveats on their way to the models adds little, since brew metadata carries no secrets. Keep the redactor for the audit log only. Small simplification inside T16.
- Net: about 7 points cut, about 5 added (tap rule plus soak about 2, digest pinning about 1, lazy key plus env allowlist about 2).
