#!/usr/bin/env bash
# Snapshot docs/ outside the repo.
#
# docs/ is git-ignored on purpose (it must not be published), which means git
# does not protect it: `git clean -fdx` deletes it without warning, and it has
# already been lost that way once. This keeps timestamped copies somewhere a
# clean cannot reach.
#
# Run manually, or let .git/hooks/post-commit call it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/docs"
DEST="${TRANSLATOR_DOCS_BACKUP_DIR:-$HOME/.local/share/translator-docs-backups}"
KEEP=20

[ -d "$SRC" ] || { echo "backup-docs: no docs/ to back up — skipping"; exit 0; }

# Nothing to do if the tree is identical to the newest snapshot.
# Pick by modification time, not name: the collision suffix below (-1, -2) makes
# lexical order disagree with chronological order.
LATEST="$(ls -1t "$DEST"/docs-*.tar.gz 2>/dev/null | head -1 || true)"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

# Second-resolution stamps collide when two runs land in the same second (two
# quick commits), and a collision would silently overwrite the older snapshot.
ARCHIVE="$DEST/docs-$STAMP.tar.gz"
n=1
while [ -e "$ARCHIVE" ]; do
  ARCHIVE="$DEST/docs-$STAMP-$n.tar.gz"
  n=$((n + 1))
done
# -C so paths inside the archive are relative to the repo root.
tar -czf "$ARCHIVE" -C "$REPO_ROOT" docs

if [ -n "$LATEST" ] && cmp -s <(gunzip -c "$LATEST" 2>/dev/null) <(gunzip -c "$ARCHIVE" 2>/dev/null); then
  rm -f "$ARCHIVE"
  echo "backup-docs: unchanged since $(basename "$LATEST") — no new snapshot"
  exit 0
fi

# Prune oldest, keeping the most recent $KEEP.
# `tail -n +N` rather than `head -n -N`: BSD head (macOS) rejects negative counts.
find "$DEST" -maxdepth 1 -name 'docs-*.tar.gz' | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
done

echo "backup-docs: wrote $ARCHIVE ($(find "$DEST" -maxdepth 1 -name 'docs-*.tar.gz' | wc -l | tr -d ' ') kept)"
