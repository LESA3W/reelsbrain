#!/usr/bin/env bash
# Visual pass: extract on-screen text from a reel/post for ReelsBrain.
# - Reel (video): download + ffmpeg scene-change frames (catches each slide/text change).
# - Post /p/ (carousel): download every image; for any video slide, scene-frames too.
# Then run vision.py (Groq Scout) on all images and print the combined on-screen text.
# Usage: visual_pass.sh <url>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
for _ef in "$HOME/.claude/telegram-claude-bot/.env.local" "$HOME/.claude/telegram-claude-bot/.env" "$SKILL_DIR/.env"; do
  [ -f "$_ef" ] && { set -a; . "$_ef"; set +a; }
done
run_py() { if command -v python >/dev/null 2>&1; then python "$@"; elif command -v py >/dev/null 2>&1; then py -3 "$@"; else python3 "$@"; fi; }

URL="${1:-}"
[ -z "$URL" ] && { echo ""; exit 0; }

# locate yt-dlp + ffmpeg (REELS_FFMPEG_DIR is pinned in .env; fallback to PATH)
YTDLP="yt-dlp"; command -v "$YTDLP" >/dev/null 2>&1 || YTDLP="$(command -v yt-dlp.exe 2>/dev/null || echo yt-dlp)"
FFDIR="${REELS_FFMPEG_DIR:-}"
if [ -z "$FFDIR" ] && command -v ffmpeg >/dev/null 2>&1; then FFDIR="$(dirname "$(command -v ffmpeg)")"; fi
FF="${FFDIR:+$FFDIR/}ffmpeg"
FF_LOC=()
[ -n "$FFDIR" ] && FF_LOC=(--ffmpeg-location "$FFDIR")
COOKIES=()
[ -n "${REELS_BROWSER:-}" ] && COOKIES=(--cookies-from-browser "$REELS_BROWSER")

WORK="${TMPDIR:-/tmp}/reels-vision-$$"; mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT

MAX_FRAMES="${REELS_VISION_MAX_FRAMES:-12}"
scenes() { # <video> <outprefix> : extract first frame + scene-change frames
  "$FF" -i "$1" -vf "select='eq(n,0)+gt(scene,0.22)',showinfo" -vsync vfr -frames:v "$MAX_FRAMES" \
    "${2}_%03d.jpg" -loglevel error -y 2>/dev/null
}

has_media() {  # vrai si on a déjà téléchargé une image OU une vidéo
  [ -n "$(find "$WORK" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \
      -o -iname '*.mp4' -o -iname '*.webm' -o -iname '*.mkv' -o -iname '*.mov' \) -print -quit 2>/dev/null)" ]
}

# 1) yt-dlp : récupère la/les vidéo(s) (Insta reel, TikTok vidéo, YouTube) ou les slides d'un carrousel
case "$URL" in
  *"/p/"* | *"/photo/"*)  # post images connu (Insta /p/, TikTok /photo/)
    "$YTDLP" "${COOKIES[@]}" "${FF_LOC[@]}" --no-warnings --quiet --ignore-errors \
      -o "$WORK/m_%(playlist_index)s.%(ext)s" "$URL" >/dev/null 2>&1 || true ;;
  *)  # reel / vidéo
    "$YTDLP" "${COOKIES[@]}" "${FF_LOC[@]}" --no-warnings --quiet --ignore-errors \
      -o "$WORK/v.%(ext)s" "$URL" >/dev/null 2>&1 || true ;;
esac

# 2) Fallback gallery-dl si yt-dlp n'a RIEN récupéré -> carrousels images, TOUTE plateforme
#    (Insta /p/, TikTok /photo/, ET les short-links vm./vt.tiktok.com qui redirigent vers un carrousel).
if ! has_media; then
  # Navigateur live d'abord (Firefox = cookie frais auto-renouvele), fichier cookies.txt en secours.
  GDL=()
  if [ -n "${REELS_BROWSER:-}" ]; then GDL=(--cookies-from-browser "$REELS_BROWSER")
  elif [ -n "${REELS_COOKIES_FILE:-}" ]; then GDL=(--cookies "$REELS_COOKIES_FILE"); fi
  run_py -m gallery_dl "${GDL[@]}" -D "$WORK" "$URL" >/dev/null 2>&1 || true
fi

# 3) Frames de TOUTES les vidéos récupérées (yt-dlp ou gallery-dl)
while IFS= read -r v; do [ -n "$v" ] && scenes "$v" "${v%.*}_fr"; done < <(
  find "$WORK" -type f \( -iname '*.mp4' -o -iname '*.webm' -o -iname '*.mkv' -o -iname '*.mov' \) 2>/dev/null
)

# collect images RECURSIVELY (gallery-dl peut créer des sous-dossiers) + frames extraites
IMGS=()
while IFS= read -r f; do [ -n "$f" ] && IMGS+=("$f"); done < <(
  find "$WORK" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) 2>/dev/null
)
if [ "${#IMGS[@]}" -eq 0 ]; then
  # aucune image/frame -> post images inaccessible (cookies manquants/expirés), toute plateforme -> signal dédié
  exit 10
fi
run_py "$SCRIPT_DIR/vision.py" "${IMGS[@]}"
