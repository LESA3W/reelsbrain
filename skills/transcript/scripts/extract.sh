#!/usr/bin/env bash
# Extract plain-text transcript from a video URL (YouTube / TikTok / Instagram Reel)
# Usage: extract.sh <url>
# Requires: yt-dlp

set -uo pipefail

URL="${1:-}"
URL="$(echo "$URL" | tr -d '[:space:]')"

if [ -z "$URL" ]; then
  echo "Erreur : URL manquante."
  echo "Usage : /transcript <url-youtube-ou-tiktok-ou-instagram>"
  exit 1
fi

if [[ ! "$URL" =~ ^https?:// ]]; then
  echo "Erreur : URL invalide (doit commencer par http:// ou https://)"
  echo "Reçu : $URL"
  exit 1
fi

YTDLP="yt-dlp"
if ! command -v "$YTDLP" >/dev/null 2>&1; then
  LOCALAPPDATA_BASH="${LOCALAPPDATA:-}"
  LOCALAPPDATA_BASH="${LOCALAPPDATA_BASH//\\//}"
  case "$LOCALAPPDATA_BASH" in
    [A-Za-z]:*) LOCALAPPDATA_BASH="/$(echo "${LOCALAPPDATA_BASH:0:1}" | tr 'A-Z' 'a-z')${LOCALAPPDATA_BASH:2}" ;;
  esac
  USERPROFILE_BASH="${USERPROFILE:-$HOME}"
  USERPROFILE_BASH="${USERPROFILE_BASH//\\//}"
  case "$USERPROFILE_BASH" in
    [A-Za-z]:*) USERPROFILE_BASH="/$(echo "${USERPROFILE_BASH:0:1}" | tr 'A-Z' 'a-z')${USERPROFILE_BASH:2}" ;;
  esac
  for candidate in \
    "$LOCALAPPDATA_BASH/Microsoft/WinGet/Packages/yt-dlp.yt-dlp_Microsoft.Winget.Source_8wekyb3d8bbwe/yt-dlp.exe" \
    "$LOCALAPPDATA_BASH/Microsoft/WinGet/Links/yt-dlp.exe" \
    "$USERPROFILE_BASH/AppData/Local/Microsoft/WinGet/Packages/yt-dlp.yt-dlp_Microsoft.Winget.Source_8wekyb3d8bbwe/yt-dlp.exe"; do
    if [ -x "$candidate" ]; then
      YTDLP="$candidate"
      break
    fi
  done
fi

if ! "$YTDLP" --version >/dev/null 2>&1; then
  echo "Erreur : yt-dlp n'est pas installé (ou pas trouvé dans le PATH ni aux chemins winget standards)."
  echo "Install : winget install yt-dlp.yt-dlp"
  echo "Si déjà installé, redémarre Claude Code pour rafraîchir le PATH."
  exit 1
fi

TMPDIR="${TMPDIR:-/tmp}/transcript-$$"
mkdir -p "$TMPDIR"
trap 'rm -rf "$TMPDIR"' EXIT

# cookies YouTube (herites via env depuis save.sh) : YouTube flague l'IP en bulk et exige --cookies
SUBS_COOKIES=()
case "$URL" in
  *youtu*) [ -n "${REELS_YT_COOKIES_FILE:-}" ] && [ -f "${REELS_YT_COOKIES_FILE:-}" ] \
             && SUBS_COOKIES=(--cookies "$REELS_YT_COOKIES_FILE") ;;
esac

META=$("$YTDLP" \
  "${SUBS_COOKIES[@]}" \
  --skip-download \
  --write-subs --write-auto-subs \
  --sub-lang "fr.*,fr,en.*,en" \
  --sub-format vtt \
  --convert-subs vtt \
  --no-warnings --quiet \
  --ignore-errors \
  --no-simulate \
  --extractor-retries 5 --retries 10 --sleep-subtitles 2 \
  `# --no-simulate required: --print implies --simulate (no actual download)` \
  `# --ignore-errors: tolerate per-lang 429s as long as one VTT lands` \
  `# sleep-subtitles + retries: YouTube 429 l'endpoint captions quand on le martele (batch) ->`\
  `# on espace et on retente au lieu de tomber sur Whisper pour des sous-titres qui EXISTENT`\
  -o "$TMPDIR/%(id)s.%(ext)s" \
  --print "%(webpage_url_domain)s|||%(title)s|||%(uploader)s|||%(duration_string)s" \
  "$URL" 2>&1)
YTDLP_EXIT=$?

VTT_FILE=$(ls "$TMPDIR"/*.vtt 2>/dev/null | sort | head -1)

if [ -z "$VTT_FILE" ] || [ ! -f "$VTT_FILE" ]; then
  if [ $YTDLP_EXIT -ne 0 ]; then
    echo "Erreur yt-dlp :"
    echo "$META"
    exit $YTDLP_EXIT
  fi
  echo "Aucun sous-titre disponible pour cette vidéo."
  echo "(Pas de fallback Whisper configuré. Pour l'activer, voir SKILL.md.)"
  exit 2
fi

META_LINE=$(echo "$META" | grep -m1 '|||' || echo "$META" | tail -1)
DOMAIN=$(echo "$META_LINE" | awk -F'\\|\\|\\|' '{print $1}')
TITLE=$(echo "$META_LINE" | awk -F'\\|\\|\\|' '{print $2}')
AUTHOR=$(echo "$META_LINE" | awk -F'\\|\\|\\|' '{print $3}')
DURATION=$(echo "$META_LINE" | awk -F'\\|\\|\\|' '{print $4}')

echo "Source: ${DOMAIN:-unknown}"
echo "Title: ${TITLE:-?}"
echo "Author: ${AUTHOR:-?}"
echo "Duration: ${DURATION:-?}"
echo "---"

awk '
  /^WEBVTT/ { next }
  /^Kind:/ { next }
  /^Language:/ { next }
  /-->/ { next }
  /^[0-9]+$/ { next }
  /^NOTE/ { next }
  {
    gsub(/<[^>]+>/, "")
    gsub(/&nbsp;/, " ")
    gsub(/&amp;/, "&")
    gsub(/&lt;/, "<")
    gsub(/&gt;/, ">")
    gsub(/&#39;/, "'\''")
    gsub(/&quot;/, "\"")
    gsub(/\r/, "")
    sub(/^[ \t]+/, "")
    sub(/[ \t]+$/, "")
    if (length($0) == 0) next
    if ($0 != prev) {
      print
      prev = $0
    }
  }
' "$VTT_FILE"
