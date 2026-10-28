#!/usr/bin/env bash
# gates.sh: the checks on a finished mini me Reel, run after ship.py. They only read the file: nothing is encoded.
#
#   bash .claude/skills/mini-me/kit/gates.sh <video> [--skip a-b,c-d] [--no-speech]
#
# <video> is the video's name (a folder in work/) or the path to its folder.
#
# One PASS or FAIL line per gate, and exit 1 when any gate fails:
#   1 format          1080x1920 at 60 fps, H.264 picture and AAC sound
#   2 margins         nothing inside the 90 px side margins on any frame (margin_scan.py; --skip leaves out windows)
#   3 no captions     the composition draws no caption track: the headline pages carry the words
#   4 frozen picture  no stretch of 0.8 s where the whole frame holds still
#   5 loudness        -13 LUFS within 1 LU, true peak at or below -1.5 dBTP
#   6 bed             the bed sits at least 15 dB under the voice (build/mix.json, written by ship.py)
#   7 picture/sound   the picture and the sound end within one frame of each other
#   8 end             the Reel ends within 0.7 s of the last word
#   9 speech          every word of the voice-over is heard in the final (kit.qa_captions). --no-speech skips it,
#                     only for a file ship.py has just checked.
# Then the contact sheet and the open for the review: work/<video>/review/sheet.jpg and open.jpg.
set -u
SLUG=${1:-}
if [ -z "$SLUG" ]; then echo "Use: bash .claude/skills/mini-me/kit/gates.sh <video> [--skip a-b] [--no-speech]"; exit 2; fi
shift
SKIP=""; SPEECH=1
while [ $# -gt 0 ]; do
  case "$1" in
    --skip) SKIP=${2:-}; shift 2 ;;
    --no-speech) SPEECH=0; shift ;;
    *) echo "Unknown option $1"; exit 2 ;;
  esac
done
KIT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$KIT/../../../.." && pwd)"
if [ -d "$SLUG" ]; then W="$(cd "$SLUG" && pwd)"; else W="$ROOT/work/$SLUG"; fi
MP4="$W/final.mp4"
PY=(uv run --project "$ROOT" python)
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
[ -f "$MP4" ] || { echo "$MP4 does not exist. Finish the Reel with ship.py first."; exit 2; }
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$MP4")
echo "== $MP4 ($D s)"

echo "== 1 format"
FMT=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate -of csv=p=0 "$MP4")
AUD=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$MP4")
if [ "$FMT" = "h264,1080,1920,60/1" ] && [ "$AUD" = "aac" ]; then pass "1080x1920, 60 fps, H.264 and AAC"; else fail "format is $FMT with $AUD sound (needs h264,1080,1920,60/1 with aac)"; fi

echo "== 2 margins (x 90 / 990)"
if [ -n "$SKIP" ]; then "${PY[@]}" "$KIT/margin_scan.py" "$MP4" --skip "$SKIP"; else "${PY[@]}" "$KIT/margin_scan.py" "$MP4"; fi \
  && pass "nothing inside the side margins" || fail "something crosses a side margin (frames above)"

echo "== 3 no caption track"
INDEX="$W/build/hf/index.html"
if [ ! -f "$INDEX" ]; then fail "$INDEX is missing"
elif grep -qiE 'karaoke|caption|class="clip"' "$INDEX"; then fail "index.html carries a caption track; the headline pages are the only words on screen"
else pass "no caption track: the headline pages carry the words"; fi

echo "== 4 frozen picture (whole frame, 0.8 s)"
FZ=$(ffmpeg -hide_banner -nostats -i "$MP4" -vf "freezedetect=n=-60dB:d=0.8" -an -f null - 2>&1 | grep freeze_start)
[ -z "$FZ" ] && pass "no frozen stretch" || { echo "$FZ" | head -5; fail "the picture holds still for 0.8 s or more"; }

echo "== 5 loudness (-13 LUFS within 1, true peak at or below -1.5)"
LOUD=$(ffmpeg -hide_banner -nostats -i "$MP4" -af ebur128=peak=true -f null - 2>&1 | grep -A20 "Summary")
echo "$LOUD" | grep -E "I:|Peak:" | sed 's/^ */   /'
"${PY[@]}" - "$LOUD" <<'PY' && pass "loudness" || fail "loudness is off target"
import re, sys
s = sys.argv[1]
i = float(re.search(r"I:\s+(-?[\d.]+) LUFS", s).group(1))
tp = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", s).group(1))
sys.exit(0 if abs(i + 13) <= 1 and tp <= -1.5 else 1)
PY

echo "== 6 bed (at least 15 dB under the voice)"
MIX="$W/build/mix.json"
if [ ! -f "$MIX" ]; then fail "build/mix.json is missing: finish with ship.py"
else
  "${PY[@]}" - "$MIX" <<'PY' && pass "bed" || fail "the bed sits closer than 15 dB to the voice"
import json, sys
mix = json.load(open(sys.argv[1]))
if mix.get("bed") is None:
    print("   no bed on this Reel")
    sys.exit(0)
print(f"   voice {mix['voice_lufs']} LUFS, bed {mix['bed_lufs']} LUFS, {mix['under_db']} dB under ({mix['bed']})")
sys.exit(0 if mix["under_db"] >= 15 else 1)
PY
fi

echo "== 7 picture and sound end together (within a frame)"
VF=$(ffprobe -v error -select_streams v -count_frames -show_entries stream=nb_read_frames -of csv=p=0 "$MP4")
AS=$(ffmpeg -v error -i "$MP4" -map 0:a -f s16le -ac 2 -ar 48000 - | wc -c | awk '{printf "%.4f", $1/4/48000}')
"${PY[@]}" -c "import sys; vf=$VF; a=$AS; d=abs(vf/60-a); print(f'   picture {vf} frames = {vf/60:.3f} s, sound {a:.3f} s'); sys.exit(0 if d <= 1/60 + 0.025 else 1)" \
  && pass "picture and sound" || fail "the picture and the sound end more than a frame apart"

echo "== 8 end (within 0.7 s of the last word)"
WORDS="$W/words.json"
if [ ! -f "$WORDS" ]; then fail "words.json is missing"
else
  "${PY[@]}" - "$WORDS" "$D" <<'PY' && pass "end" || fail "the Reel runs on too long after the last word"
import sys
from kit import transcribe
words = transcribe.load(sys.argv[1])
last, length = words[-1].end, float(sys.argv[2])
print(f"   last word ends {last:.2f} s, the Reel {length:.2f} s")
sys.exit(0 if -0.05 <= length - last <= 0.7 else 1)
PY
fi

echo "== 9 speech (every word heard)"
if [ "$SPEECH" = "1" ]; then
  "${PY[@]}" -m kit.qa_captions "$MP4" --words "$W/words.json" --speed 1.0 && pass "speech" || fail "a word is missing from the final audio"
else
  echo "   skipped: ship.py checked this file"
fi

echo "== contact sheet and the open -> $W/review/"
"${PY[@]}" "$ROOT/bin/review_frames.py" "$MP4" --fps 2 | sed 's/^/   /'
echo "Look at review/open.jpg and review/sheet.jpg before calling it done."

echo "== $FAILS gate(s) failed"
[ "$FAILS" -eq 0 ]
