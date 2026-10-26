#!/usr/bin/env bash
# Transcrit TOUTES les videos (best-effort) d'un compte et les memorise dans ReelsBrain,
# chaque video = une note reliee a un noeud central [[<compte>]] (cluster du graph Obsidian).
# Reutilise process_reel.sh (transcript -> distill Groq -> note) avec REELS_ACCOUNT.
# Usage: transcript_account.sh <url_compte | @handle> [url_video_sup1] [url_video_sup2] ...
set -uo pipefail
export PYTHONIOENCODING=utf-8

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAVE_REEL_DIR="$HOME/.claude/skills/save-reel/scripts"
for _ef in "$HOME/.claude/telegram-claude-bot/.env.local" "$HOME/.claude/telegram-claude-bot/.env" \
           "$HOME/.claude/skills/save-reel/.env"; do
  [ -f "$_ef" ] && { set -a; . "$_ef"; set +a; }
done
run_py() { if command -v python >/dev/null 2>&1; then python "$@"; elif command -v py >/dev/null 2>&1; then py -3 "$@"; else python3 "$@"; fi; }

ARG="${1:-}"
[ -z "$ARG" ] && { echo "Usage: transcript-account <url_compte | @handle> [urls videos sup...]"; exit 1; }
shift || true   # le reste = URLs de videos supplementaires a inclure manuellement

YTDLP="yt-dlp"; command -v "$YTDLP" >/dev/null 2>&1 || YTDLP="$(command -v yt-dlp.exe 2>/dev/null || echo yt-dlp)"
# cookies URL-aware : YouTube (bot-check en bulk) -> cookies YT dedies ; sinon browser/file Insta-TikTok
COOKIE_ARGS=()
if printf '%s' "$ARG" | grep -qiE 'youtu'; then
  [ -n "${REELS_YT_COOKIES_FILE:-}" ] && [ -f "${REELS_YT_COOKIES_FILE:-}" ] && COOKIE_ARGS=(--cookies "$REELS_YT_COOKIES_FILE")
elif [ -n "${REELS_BROWSER:-}" ]; then COOKIE_ARGS=(--cookies-from-browser "$REELS_BROWSER")
elif [ -n "${REELS_COOKIES_FILE:-}" ] && [ -f "${REELS_COOKIES_FILE:-}" ]; then COOKIE_ARGS=(--cookies "$REELS_COOKIES_FILE"); fi
# limite optionnelle : REELS_ACCOUNT_LIMIT=N -> ne prend que les N plus recentes (newest-first)
LIMIT_ARG=()
[ -n "${REELS_ACCOUNT_LIMIT:-}" ] && LIMIT_ARG=(--playlist-end "$REELS_ACCOUNT_LIMIT")

# --- deriver le handle (= nom du noeud) + l'URL du compte ---
HANDLE="$(printf '%s' "$ARG" | grep -oiE '@[A-Za-z0-9._]+' | head -1 | tr -d '@')"
if [ -z "$HANDLE" ]; then
  HANDLE="$(printf '%s' "$ARG" | sed -E 's#https?://[^/]+/##; s#@##; s#[/?].*##')"
fi
[ -z "$HANDLE" ] && HANDLE="compte"
if printf '%s' "$ARG" | grep -qiE '^https?://'; then ACCT_URL="$ARG"; else ACCT_URL="https://www.tiktok.com/@${HANDLE}"; fi
# YouTube : par defaut on cible l'onglet Videos (format long = contenu methodo), PAS les Shorts ni
# les Lives/Streams (bruit). Une chaine nue liste TOUS les onglets -> on normalise vers /videos.
if printf '%s' "$ACCT_URL" | grep -qiE 'youtube\.com'; then
  case "$ACCT_URL" in
    *"/videos"*|*"/shorts"*|*"/streams"*|*"/watch"*|*"list="*|*"/playlist"*) : ;;  # onglet deja cible
    *) ACCT_URL="${ACCT_URL%/}/videos" ;;
  esac
fi
export REELS_ACCOUNT="$HANDLE"

echo "Compte : @${HANDLE}"
echo "Enumeration des videos (best-effort)..."

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
: > "$TMP/urls.txt"
# 1) listing direct du compte (TikTok/@handle, chaine YouTube, etc.)
"$YTDLP" "${COOKIE_ARGS[@]}" "${LIMIT_ARG[@]}" --flat-playlist --no-warnings --print "%(url)s" "$ACCT_URL" 2>/dev/null >> "$TMP/urls.txt" || true
# 2) fallback TikTok via tiktokuser:channel_id (pagine souvent plus loin que l'@handle)
if printf '%s' "$ACCT_URL" | grep -qi tiktok; then
  CID="$("$YTDLP" "${COOKIE_ARGS[@]}" --skip-download --no-warnings --playlist-end 1 --print "%(channel_id)s" "$ACCT_URL" 2>/dev/null | grep -E '^[0-9A-Za-z_-]{16,}$' | head -1)"
  [ -n "$CID" ] && "$YTDLP" "${COOKIE_ARGS[@]}" --flat-playlist --no-warnings --print "%(url)s" "tiktokuser:$CID" 2>/dev/null >> "$TMP/urls.txt" || true
fi
# 3) URLs de videos passees en argument (pour completer une liste bridee)
for u in "$@"; do printf '%s\n' "$u" >> "$TMP/urls.txt"; done

# dedupe par ID de video (TikTok /video/<id>, Insta shortcode) et reconstruit des URLs propres
run_py - "$HANDLE" "$TMP/urls.txt" > "$TMP/final.txt" <<'PY'
import sys, re
handle = sys.argv[1]
try:
    lines = open(sys.argv[2], encoding="utf-8", errors="replace").read().splitlines()
except OSError:
    lines = []
seen, out = set(), []
for u in lines:
    u = u.strip()
    if not u:
        continue
    mv = re.search(r"/video/(\d+)", u)
    mi = re.search(r"/(?:p|reel|reels)/([^/?]+)", u)
    key = mv.group(1) if mv else (mi.group(1) if mi else u)
    if key in seen:
        continue
    seen.add(key)
    if mv:  # TikTok : reconstruit une URL lisible (evite les handles opaques MS4...)
        u = f"https://www.tiktok.com/@{handle}/video/{mv.group(1)}"
    out.append(u)
for u in out:
    print(u)
PY

COUNT="$(grep -c . "$TMP/final.txt" 2>/dev/null || echo 0)"
echo "-> $COUNT video(s) a traiter (dedup)."
echo "   NB: TikTok bride l'acces anonyme -> la liste auto peut etre partielle. Complete en passant"
echo "   des URLs de videos en argument, ou via des cookies TikTok (REELS_COOKIES_FILE / REELS_BROWSER)."
[ "$COUNT" = "0" ] && { echo "Aucune video trouvee."; exit 2; }

i=0; ok=0; skip=0; err=0
DELAY="${REELS_BATCH_DELAY:-8}"
while IFS= read -r U; do
  [ -z "$U" ] && continue
  i=$((i+1))
  OUT="$(bash "$SAVE_REEL_DIR/process_reel.sh" "$U" 2>&1)"
  LAST="$(printf '%s' "$OUT" | grep -E '^RESULT_' | tail -1)"
  case "$LAST" in
    RESULT_OK*)      ok=$((ok+1));   echo "  [$i/$COUNT] OK     $(printf '%s' "$LAST" | cut -f2-)" ;;
    RESULT_SKIP*)    skip=$((skip+1)); echo "  [$i/$COUNT] deja   $U" ;;
    RESULT_COOKIES*) err=$((err+1)); echo "  [$i/$COUNT] cookies requis : $U" ;;
    *)               err=$((err+1)); echo "  [$i/$COUNT] ERR    $(printf '%s' "$OUT" | tail -1 | cut -c1-120)" ;;
  esac
  [ "$i" -lt "$COUNT" ] && sleep "$DELAY"
done < "$TMP/final.txt"

echo ""
echo "Termine @${HANDLE} : $ok nouvelle(s), $skip deja presente(s), $err echec(s)."
echo "Noeud central : accounts/${HANDLE}.md  (dans Obsidian, tout est relie a [[${HANDLE}]])"
