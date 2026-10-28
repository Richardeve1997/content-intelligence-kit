#!/usr/bin/env bash
# fetch_gsap.sh: download GSAP into kit/vendor/gsap.min.js. Idempotent.
#
# Usage:
#   bin/fetch_gsap.sh [DEST_DIR]
#
# It fetches exactly GSAP 3.15.0 from the npm registry. GSAP_VERSION overrides that version,
# and DEST_DIR (first argument) or the VENDOR_DIR environment variable overrides the destination
# (kit/vendor at the repo root). If gsap.min.js is already there and not empty, nothing is done.
# The downloaded file is gitignored and never committed.
#
# The package is checked before anything is written: its checksum against the one the registry
# publishes for that version, the version inside it, and the header of gsap.min.js. A failure
# prints one plain sentence and leaves no file behind. Compositions load the local file with a
# relative <script src>, never a CDN.

set -euo pipefail

GSAP_VERSION="${GSAP_VERSION:-3.15.0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="${1:-${VENDOR_DIR:-"$SCRIPT_DIR/../kit/vendor"}}"
DEST="$DEST_DIR/gsap.min.js"
SHOWN="$DEST"
if [ -z "${1:-}" ] && [ -z "${VENDOR_DIR:-}" ]; then
  SHOWN="kit/vendor/gsap.min.js"
fi

stop() {
  echo "$1" >&2
  exit 1
}

if [ -s "$DEST" ]; then
  HAVE="$(head -c 300 "$DEST" | grep -o 'GSAP [0-9][0-9.]*' | head -n 1 | cut -d ' ' -f 2 || true)"
  echo "fetch_gsap: $SHOWN already present ($(wc -c < "$DEST" | tr -d ' ') bytes). Nothing to do."
  if [ -n "$HAVE" ] && [ "$HAVE" != "$GSAP_VERSION" ]; then
    echo "fetch_gsap: it is GSAP $HAVE and this kit pins GSAP $GSAP_VERSION. To switch, delete the file and run bash bin/fetch_gsap.sh again."
  fi
  exit 0
fi

if ! [[ "$GSAP_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  stop "GSAP_VERSION must be a version number such as 3.15.0."
fi

for cmd in curl python3; do
  command -v "$cmd" >/dev/null 2>&1 || stop "bin/fetch_gsap.sh needs $cmd and could not find it. Install it, then run bash bin/fetch_gsap.sh again."
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# fetch URL FILE: save the answer, stay quiet, and turn any failure into one plain sentence.
fetch() {
  local status
  status="$(curl -s --max-time 300 --retry 2 --retry-delay 2 -o "$2" -w '%{http_code}' "$1" 2>/dev/null)" || status="000"
  case "$status" in
    200) ;;
    404) stop "The npm registry has no GSAP ${GSAP_VERSION}. Check the version number in GSAP_VERSION." ;;
    *) stop "Could not download GSAP ${GSAP_VERSION}. Check your internet connection and run bash bin/fetch_gsap.sh again." ;;
  esac
}

fetch "https://registry.npmjs.org/gsap/${GSAP_VERSION}" "$TMP/meta.json"

TARBALL_URL="$(python3 - "$TMP/meta.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        url = json.load(handle)["dist"]["tarball"]
except Exception:
    sys.exit(1)
if not isinstance(url, str) or not url.startswith("https://registry.npmjs.org/"):
    sys.exit(1)
print(url)
PY
)" || stop "Could not download GSAP ${GSAP_VERSION}. Check your internet connection and run bash bin/fetch_gsap.sh again."

fetch "$TARBALL_URL" "$TMP/gsap.tgz"

python3 - "$TMP/meta.json" "$TMP/gsap.tgz" "$GSAP_VERSION" "$TMP/gsap.min.js" <<'PY'
import base64
import hashlib
import json
import sys
import tarfile

meta_path, tgz_path, version, out_path = sys.argv[1:5]


def stop(message):
    print(message, file=sys.stderr)
    sys.exit(1)


# 1. The download must match the checksum the registry publishes for this version.
try:
    with open(meta_path, encoding="utf-8") as handle:
        dist = json.load(handle)["dist"]
    with open(tgz_path, "rb") as handle:
        data = handle.read()
except Exception:
    stop(f"Could not download GSAP {version}. Check your internet connection and run bash bin/fetch_gsap.sh again.")

matched = False
checked = False
for entry in str(dist.get("integrity") or "").split():
    algorithm, _, expected = entry.partition("-")
    if algorithm in ("sha512", "sha384", "sha256"):
        checked = True
        digest = base64.b64encode(hashlib.new(algorithm, data).digest()).decode()
        matched = digest == expected
        if algorithm == "sha512":
            break
if not checked and dist.get("shasum"):
    checked = True
    matched = hashlib.sha1(data).hexdigest() == dist["shasum"]
if not checked:
    stop(f"npm publishes no checksum for GSAP {version}, so the download could not be checked and was thrown away.")
if not matched:
    stop(
        f"The GSAP {version} download did not match the checksum npm publishes for it, so it was thrown away. "
        "Run bash bin/fetch_gsap.sh again."
    )

# 2. The package must be this version, and gsap.min.js must say so in its header.
try:
    with tarfile.open(tgz_path, "r:gz") as tar:
        found = json.load(tar.extractfile("package/package.json")).get("version")
        body = tar.extractfile("package/dist/gsap.min.js").read()
except Exception:
    stop("The download is not a GSAP package this script can read, so it was thrown away. Run bash bin/fetch_gsap.sh again.")
if found != version:
    stop(f"The package that came down is GSAP {found}, not {version}, so it was thrown away.")
if not body or f"GSAP {version}".encode() not in body[:400]:
    stop(f"The downloaded file does not look like GSAP {version}, so it was thrown away.")

with open(out_path, "wb") as handle:
    handle.write(body)
PY

mkdir -p "$DEST_DIR" || stop "Could not create $DEST_DIR. Check that you can save files there."
cp "$TMP/gsap.min.js" "$DEST.part" || stop "Could not write $DEST. Check that you can save files in that folder."
mv -f "$DEST.part" "$DEST"
echo "fetch_gsap: wrote $SHOWN ($(wc -c < "$DEST" | tr -d ' ') bytes, GSAP ${GSAP_VERSION})"
