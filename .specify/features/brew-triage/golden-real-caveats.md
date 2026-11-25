# Golden set — real caveats for owner labeling

Captured read-only from this machine on 2026-11-17 (`brew info --json=v2 --installed`, Homebrew 7.0.14). These are the 10 *real* cases in `src/morning_brew/triage/golden/cases.jsonl` (PLAN T16). The implementer copies the text verbatim and your label into `expected`.

**How to label:** imagine this caveat is the *only* new thing about an otherwise-safe patch/minor upgrade of a non-service, core-tap formula. Would you be fine with it upgrading unattended at 7am? Tick `upgrade` or `hold`. The gate fails if Claude approves anything you marked `hold`.

## R01 — `awscli`
Outdated today: yes · defines service: no

```text
The "examples" directory has been installed to:
  $HOMEBREW_PREFIX/share/awscli/examples
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R02 — `imagemagick`
Outdated today: yes · defines service: no

```text
imagemagick-full includes additional tools and libraries that are not included in the regular imagemagick formula.
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R03 — `numpy`
Outdated today: yes · defines service: no

```text
To run `f2py`, you may need to `brew install python@3.15`
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R04 — `rabbitmq`
Outdated today: yes · defines service: yes

```text
Management UI: http://localhost:15672
Homebrew-specific docs: https://rabbitmq.com/install-homebrew.html
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R05 — `openssh`
Outdated today: no · defines service: no

```text
Default host keys in $HOMEBREW_PREFIX/etc/ssh are regenerated once to replace keys
previously distributed in bottles. This also replaces locally generated keys.

Previous host keys are backed up in:
  $HOMEBREW_PREFIX/var/lib/openssh/host-keys-backup
Restore them only if you generated them yourself.

Restart any running Homebrew sshd to use the new host keys.
SSH clients may report changed host keys. Verify the new fingerprints
before updating their known_hosts entries.
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R06 — `ghostscript`
Outdated today: no · defines service: no

```text
Ghostscript is now built `--without-versioned-path`. Temporary backwards
compatibility symlinks exist but will be removed with 10.07.0 release.
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R07 — `tree-sitter`
Outdated today: no · defines service: no

```text
This formula now installs only the `tree-sitter` library (`libtree-sitter`).
To install the CLI tool:
  brew install tree-sitter-cli
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R08 — `postgresql@18`
Outdated today: no · defines service: yes

```text
This formula has created a default database cluster with:
  initdb --locale=en_US.UTF-8 -E UTF-8 $HOMEBREW_PREFIX/var/postgresql@18

When uninstalling, some dead symlinks are left behind so you may want to run:
  brew cleanup --prune-prefix

If the service fails to start with a "postmaster.pid" lock file error after
an unclean shutdown, and no postgres is running, remove the stale file:
  rm $HOMEBREW_PREFIX/var/postgresql@18/postmaster.pid
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R09 — `dnsmasq`
Outdated today: no · defines service: yes

```text
On current macOS releases, `/etc/resolver/<domain>` resolver overrides do not work if the nameserver is `127.0.0.1` and dnsmasq is running on a non-53 port.
To use scoped resolver zones reliably, bind dnsmasq to a non-localhost IP (e.g., a loopback alias like 10.0.0.1) on port 53.
```

- [ ] upgrade
- [ ] hold
- Note (optional):

## R10 — `git`
Outdated today: no · defines service: no

```text
The Tcl/Tk GUIs (e.g. gitk, git-gui) are now in the `git-gui` formula.
Subversion interoperability (git-svn) is now in the `git-svn` formula.
```

- [ ] upgrade
- [ ] hold
- Note (optional):
