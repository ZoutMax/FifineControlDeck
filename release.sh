#!/usr/bin/env bash
# release.sh — cut a new fifine Control Deck release across all channels.
#
#   ./release.sh <version> ["changelog message"]
#
# What it does (safe, no publishing side effects):
#   1. bumps the version in snap/snapcraft.yaml and adds a debian/changelog entry
#   2. commits (as ZoutMax, no AI attribution), tags v<version>
#   3. pushes main + tags to BOTH remotes (GitHub origin + Launchpad mirror)
# Then it prints the exact publish commands (snap + PPA) for you to run when ready.
set -euo pipefail

VERSION="${1:?usage: ./release.sh <version> [\"changelog message\"]}"
MSG="${2:-Release $VERSION}"
KEY=D42A012CF26518F44F1E4F7BB1174D503445F8FE
REPO="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO"

# repo identity (keeps GitHub contributions credited to ZoutMax, no co-author)
GIT_ID=(-c user.name='ZoutMax' -c user.email='150600436+ZoutMax@users.noreply.github.com')

echo ">> bumping version to $VERSION"
sed -i -E "s/^version: .*/version: '$VERSION'/" snap/snapcraft.yaml

# debian/changelog entry (native package; noble target). Maintainer email matches
# the repo identity; the *signed upload* uses the GPG key, so -k avoids a mismatch.
DEBEMAIL="150600436+ZoutMax@users.noreply.github.com" DEBFULLNAME="ZoutMax" \
  dch -v "$VERSION" --distribution noble "$MSG"

# AppStream metainfo: GNOME Software / App Center show the newest <release>
# entry, and CI (tests/test_packaging.py) fails on a version skew. Skipping
# this is how the metainfo sat at 0.5.2 while 0.5.7 shipped. Done in python:
# sed would corrupt the XML on a message containing &, |, or angle brackets.
VERSION="$VERSION" MSG="$MSG" python3 - <<'PYEOF'
import os, time
from xml.sax.saxutils import escape
version, msg = os.environ["VERSION"], os.environ["MSG"]
entry = ('    <release version="%s" date="%s">\n'
         '      <description>\n        <p>%s</p>\n      </description>\n'
         '    </release>\n') % (version, time.strftime("%Y-%m-%d"), escape(msg))
for mi in ("packaging/io.github.zoutmax.FifineControlDeck.metainfo.xml",
           "flatpak/io.github.zoutmax.FifineControlDeck.metainfo.xml"):
    with open(mi) as f:
        body = f.read()
    if 'version="%s"' % version in body:
        continue
    assert "  <releases>\n" in body, mi
    with open(mi, "w") as f:
        f.write(body.replace("  <releases>\n", "  <releases>\n" + entry, 1))
    print("metainfo: added %s to %s" % (version, mi))
PYEOF
command -v appstreamcli >/dev/null && appstreamcli validate packaging/*.metainfo.xml flatpak/*.metainfo.xml

echo ">> committing + tagging v$VERSION"
git add snap/snapcraft.yaml debian/changelog \
        packaging/*.metainfo.xml flatpak/*.metainfo.xml
git "${GIT_ID[@]}" commit -m "release: v$VERSION

$MSG"
git tag -a "v$VERSION" -m "v$VERSION" 2>/dev/null || { echo "tag v$VERSION exists; reusing"; }

echo ">> pushing to GitHub (origin) + Launchpad (launchpad)"
git push origin main --tags
GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=accept-new" git push launchpad main --tags

cat <<EOF

================ v$VERSION committed, tagged, and pushed ================

Publish when ready:

  GITHUB RELEASE (automatic)
    * .github/workflows/release.yml builds the amd64+arm64 .deb from this tag
      and publishes a GitHub Release with them attached — no action needed.
      Watch it:  gh run watch

  SNAP
    * If the GitHub builder is connected: an edge build (amd64+arm64) starts
      automatically from this push. Promote when happy:
          snapcraft release fifine-control-deck <rev> stable
    * Manual build+upload instead:
          sg lxd -c '/snap/bin/snapcraft pack'
          snapcraft upload --release=edge fifine-control-deck_${VERSION}_amd64.snap
          snapcraft upload-metadata fifine-control-deck_${VERSION}_amd64.snap --force

  PPA (apt)
      debuild -S -k$KEY
      dput ppa:zoutmax/fifine ../fifine-control-deck_${VERSION}_source.changes

=========================================================================
EOF
