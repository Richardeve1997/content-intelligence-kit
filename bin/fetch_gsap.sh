#!/usr/bin/env bash
# fetch_gsap.sh: download GSAP into kit/vendor/gsap.min.js. Idempotent.
#
# Usage:
#   bin/fetch_gsap.sh [DEST_DIR]
#
# DEST_DIR (first argument) or the VENDOR_DIR environment variable overrides
# the default destination directory (kit/vendor at the repo root).
# If gsap.min.js already exists and is non-empty, the script does nothing.
# The downloaded file is gitignored and never committed.
#
# Source: the official gsap package tarball from registry.npmjs.org
# (dist/gsap.min.js inside the tarball). Never a CDN at render time:
# compositions load this local file via a relative <script src>.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${1:-${VENDOR_DIR:-"$SCRIPT_DIR/../kit/vendor"}}"
DEST="$DEST_DIR/gsap.min.js"

if [ -s "$DEST" ]; then
  echo "fetch_gsap: $DEST already present ($(wc -c < "$DEST" | tr -d ' ') bytes). Nothing to do."
  exit 0
fi

for cmd in curl tar python3; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "fetch_gsap: ERROR: '$cmd' not found on PATH. Install it and re-run." >&2
    exit 1
  }
done

mkdir -p "$DEST_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "fetch_gsap: resolving latest gsap tarball from registry.npmjs.org ..."
TARBALL_URL="$(curl -fsSL https://registry.npmjs.org/gsap/latest \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["dist"]["tarball"])')"

if [ -z "$TARBALL_URL" ]; then
  echo "fetch_gsap: ERROR: could not resolve the gsap tarball URL from the npm registry." >&2
  exit 1
fi

echo "fetch_gsap: downloading $TARBALL_URL"
curl -fsSL "$TARBALL_URL" -o "$TMP/gsap.tgz"
tar -xzf "$TMP/gsap.tgz" -C "$TMP"

SRC="$TMP/package/dist/gsap.min.js"
if [ ! -s "$SRC" ]; then
  echo "fetch_gsap: ERROR: dist/gsap.min.js not found in the downloaded tarball." >&2
  exit 1
fi

if ! grep -qi "gsap" "$SRC"; then
  echo "fetch_gsap: ERROR: downloaded file does not look like GSAP (no 'gsap' string found)." >&2
  exit 1
fi

cp "$SRC" "$DEST"
echo "fetch_gsap: wrote $DEST ($(wc -c < "$DEST" | tr -d ' ') bytes)"
