# Publishing via a Launchpad PPA

A PPA distributes the app through `apt` on Ubuntu. Launchpad **builds from
source**, so the `debian/` directory in this repo is what it uses. This has been
test-built locally (`dpkg-buildpackage -b`) and produces a working `.deb`.

## One-time setup

### 1. SSH key (code hosting / git-over-ssh)
Already generated at `~/.ssh/id_ed25519`. Import the **public** key at
<https://launchpad.net/~/+editsshkeys> (paste `~/.ssh/id_ed25519.pub`).

### 2. GPG key (required — PPA uploads must be signed)
Launchpad only accepts source uploads signed by a GPG key registered to your
account, tied to an email **you can receive mail at** (for the confirmation).

```bash
gpg --full-generate-key          # RSA 4096 or ed25519; use YOUR real email
gpg --list-secret-keys --keyid-format=long   # note the key id / fingerprint
# publish the key so Launchpad can fetch it:
gpg --send-keys --keyserver keyserver.ubuntu.com <KEYID>
```
Add the fingerprint at <https://launchpad.net/~/+editpgpkeys>; Launchpad emails
you an **encrypted** confirmation — decrypt it and click the link to confirm.

### 3. Create the PPA
On <https://launchpad.net/~ZoutMax> → *Create a new PPA* (e.g. name `fifine`).
It becomes `ppa:zoutmax/fifine`.

### 4. Tell dput about it (usually automatic)
`dput ppa:zoutmax/fifine …` works out of the box on Ubuntu.

## Build + upload the source package

Set your identity to match your GPG key (the signed upload must verify):

```bash
export DEBFULLNAME="Your Name"
export DEBEMAIL="you@example.com"     # the email on your GPG + Launchpad account

cd fifine-control-deck-linux
# refresh the changelog entry for the target series (e.g. noble/jammy):
dch -v 0.5.2~ppa1 --distribution noble "PPA build"

# build a SIGNED source package (.dsc + .changes) — signs with your GPG key:
debuild -S -sa

# upload:
dput ppa:zoutmax/fifine ../fifine-control-deck_0.5.2~ppa1_source.changes
```

Launchpad then builds the binaries for `amd64` and `arm64` (per `debian/control`)
and publishes them. Users install with:

```bash
sudo add-apt-repository ppa:zoutmax/fifine
sudo apt update
sudo apt install fifine-control-deck
```

## Notes
- **Per-series builds:** repeat the `dch --distribution <series>` + `debuild -S`
  + `dput` for each Ubuntu series you want (noble, jammy, oracular, …). Bump the
  version suffix (`~ppa1`, `~ppa2`) on re-uploads.
- **Maintainer email:** `debian/control` uses the GitHub no-reply address; the
  *signed upload* uses your GPG identity (`DEBEMAIL`). Launchpad cares about the
  signing key, not the Maintainer field.
- The same `debian/` tree also builds a local `.deb` with `dpkg-buildpackage -b`
  (unsigned) if you just want to test.
