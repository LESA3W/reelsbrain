#!/usr/bin/env bash
# save-reel: transcribe a reel for the ReelsBrain second brain.
# Strategy: subtitles first (free/instant, reuses transcript/extract.sh),
# then audio -> Whisper fallback (Groq free tier, or local whisper.cpp).
# Usage: save.sh <url>
# Output: "Method:" line, then Source/Title/Author/Duration, then "---", then transcript.
# Exit codes: 0 ok | 1 bad url | 2 no transcript possible | 3 no transcription engine configured

set -uo pipefail
export PYTHONIOENCODING=utf-8  # force yt-dlp (frozen Python) à sortir l'UTF-8, pas le cp1252 Windows

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- load config from the bot's pulled env (KV creds, TELEGRAM_BOT_TOKEN) + skill .env (GROQ, etc.)
#     skill .env is sourced LAST so it wins. ---
for _ef in \
  "$HOME/.claude/telegram-claude-bot/.env.local" \
  "$HOME/.claude/telegram-claude-bot/.env" \
  "$SKILL_DIR/.env"; do
  [ -f "$_ef" ] && { set -a; . "$_ef"; set +a; }
done

URL="${1:-}"
URL="$(echo "$URL" | tr -d '[:space:]')"

if [ -z "$URL" ]; then
  echo "Erreur : URL manquante."
  echo "Usage : /save-reel <url>"
  exit 1
fi
if [[ ! "$URL" =~ ^https?:// ]]; then
  echo "Erreur : URL invalide (doit commencer par http:// ou https://)"
  echo "Reçu : $URL"
  exit 1
fi

# --- cookies : UNE seule definition, URL-aware, utilisee partout (sous-titres, audio, vision) ---
# YouTube flague l'IP en bulk ("confirm you're not a bot") -> cookies YouTube dedies (REELS_YT_COOKIES_FILE).
# Insta/TikTok gated -> navigateur live (REELS_BROWSER) ou export manuel (REELS_COOKIES_FILE).
COOKIE_ARGS=()
case "$URL" in
  *youtu*)
    if [ -n "${REELS_YT_COOKIES_FILE:-}" ] && [ -f "${REELS_YT_COOKIES_FILE:-}" ]; then
      COOKIE_ARGS=(--cookies "$REELS_YT_COOKIES_FILE")
    fi ;;
  *)
    if [ -n "${REELS_BROWSER:-}" ]; then
      COOKIE_ARGS=(--cookies-from-browser "$REELS_BROWSER")
    elif [ -n "${REELS_COOKIES_FILE:-}" ] && [ -f "${REELS_COOKIES_FILE:-}" ]; then
      COOKIE_ARGS=(--cookies "$REELS_COOKIES_FILE")
    fi ;;
esac

# --- helper: normalize a Windows env path (C:\x) to a Git-Bash path (/c/x) ---
winpath() {
  local p="${1//\\//}"
  case "$p" in
    [A-Za-z]:*) printf '/%s%s' "$(echo "${p:0:1}" | tr 'A-Z' 'a-z')" "${p:2}" ;;
    *) printf '%s' "$p" ;;
  esac
}
LOCALAPPDATA_BASH="$(winpath "${LOCALAPPDATA:-}")"
USERPROFILE_BASH="$(winpath "${USERPROFILE:-$HOME}")"

# vault + cache (save.sh writes the raw transcript here so Claude never has to JSON-escape it)
VAULT="${REELS_VAULT:-$USERPROFILE_BASH/Obsidian/ReelsBrain}"
CACHE_DIR="$VAULT/.cache"
mkdir -p "$CACHE_DIR" 2>/dev/null || true
TRANSCRIPT_CACHE="$CACHE_DIR/last_transcript.txt"
META_CACHE="$CACHE_DIR/last_meta.tsv"

# write machine-readable metadata (TSV) so the headless distiller can read it without re-parsing stdout
write_meta() { # method domain title author duration
  { printf 'method\t%s\n' "$1"
    printf 'domain\t%s\n' "$2"
    printf 'title\t%s\n' "$3"
    printf 'author\t%s\n' "$4"
    printf 'duration\t%s\n' "$5"
    printf 'url\t%s\n' "$URL"
  } > "$META_CACHE"
}

run_py() { if command -v python >/dev/null 2>&1; then python "$@"; elif command -v py >/dev/null 2>&1; then py -3 "$@"; else python3 "$@"; fi; }
# caption (description) + avis (commentaires) — désactivable via REELS_FETCH_CONTEXT=0
fetch_context() { [ "${REELS_FETCH_CONTEXT:-1}" = "1" ] && run_py "$SCRIPT_DIR/context.py" "$URL" 2>/dev/null || true; }

# --- locate yt-dlp (PATH, then standard winget locations) ---
YTDLP="yt-dlp"
if ! command -v "$YTDLP" >/dev/null 2>&1; then
  for candidate in \
    "$LOCALAPPDATA_BASH/Microsoft/WinGet/Packages/yt-dlp.yt-dlp_Microsoft.Winget.Source_8wekyb3d8bbwe/yt-dlp.exe" \
    "$LOCALAPPDATA_BASH/Microsoft/WinGet/Links/yt-dlp.exe" \
    "$USERPROFILE_BASH/AppData/Local/Microsoft/WinGet/Packages/yt-dlp.yt-dlp_Microsoft.Winget.Source_8wekyb3d8bbwe/yt-dlp.exe"; do
    [ -x "$candidate" ] && YTDLP="$candidate" && break
  done
fi
if ! "$YTDLP" --version >/dev/null 2>&1; then
  echo "Erreur : yt-dlp introuvable. Install : winget install yt-dlp.yt-dlp"
  exit 1
fi

# ============================================================================
# STEP 1 — subtitles (reuse the existing /transcript extractor)
# ============================================================================
TRANSCRIPT_SKILL="$(winpath "${USERPROFILE:-$HOME}")/.claude/skills/transcript/scripts/extract.sh"
if [ -f "$TRANSCRIPT_SKILL" ]; then
  # Des sous-titres existent-ils (manuels OU auto) ? Metadata rapide. Si OUI mais la recup echoue,
  # c'est un 429 throttle transitoire de YouTube sur l'endpoint captions (martele en batch) ->
  # on RETENTE avec backoff AU LIEU de gaspiller Whisper pour des sous-titres qui existent.
  SUBS_EXIST=0
  if "$YTDLP" --skip-download --no-warnings "${COOKIE_ARGS[@]}" \
       --print "%(subtitles)j|%(automatic_captions)j" "$URL" 2>/dev/null \
       | grep -qiE '"(en|fr)[a-z-]*"[[:space:]]*:'; then SUBS_EXIST=1; fi
  SUB_TRIES=1; [ "$SUBS_EXIST" = 1 ] && SUB_TRIES=3
  _st=0; SUB_EXIT=1
  while [ $_st -lt $SUB_TRIES ]; do
    _st=$((_st + 1))
    SUB_OUT="$(bash "$TRANSCRIPT_SKILL" "$URL" 2>&1)"; SUB_EXIT=$?
    if [ $SUB_EXIT -eq 0 ] && printf '%s' "$SUB_OUT" | grep -q '^---$'; then
      printf '%s\n' "$SUB_OUT" | awk 'f{print} /^---$/{f=1}' > "$TRANSCRIPT_CACHE"
      _CTX="$(fetch_context)"; [ -n "$_CTX" ] && printf '\n\n%s\n' "$_CTX" >> "$TRANSCRIPT_CACHE"
      write_meta "subtitles" \
        "$(printf '%s\n' "$SUB_OUT" | sed -n 's/^Source: //p'   | head -1)" \
        "$(printf '%s\n' "$SUB_OUT" | sed -n 's/^Title: //p'    | head -1)" \
        "$(printf '%s\n' "$SUB_OUT" | sed -n 's/^Author: //p'   | head -1)" \
        "$(printf '%s\n' "$SUB_OUT" | sed -n 's/^Duration: //p' | head -1)"
      echo "Method: subtitles"
      echo "$SUB_OUT"
      exit 0
    fi
    # sous-titres presents mais pas recuperes (429) -> backoff croissant puis retry
    if [ "$SUBS_EXIST" = 1 ] && [ $_st -lt $SUB_TRIES ]; then
      printf 'sous-titres presents mais recup KO (429?), retry %d/%d dans %ds\n' \
        "$_st" "$SUB_TRIES" $((_st * 25)) >&2
      sleep $((_st * 25))
    fi
  done
  # SUB_EXIT 2 = pas de sous-titres. Toute autre erreur (hoquet yt-dlp, post /p/ sans vidéo)
  # ne doit PAS bloquer : on continue vers l'audio (reel) ou le court-circuit vision (post).
  if [ $SUB_EXIT -ne 0 ] && [ $SUB_EXIT -ne 2 ]; then
    printf 'sous-titres indispo (%s), on continue\n' "$SUB_EXIT" >&2
  fi
fi

# ============================================================================
# STEP 2 — audio -> Whisper fallback. Decide engine BEFORE downloading.
# ============================================================================
ENGINE=""
if [ -n "${GROQ_API_KEY:-}" ]; then
  ENGINE="groq"
elif [ -n "${WHISPER_CPP_BIN:-}" ] && [ -x "${WHISPER_CPP_BIN:-/nonexistent}" ] && [ -n "${WHISPER_CPP_MODEL:-}" ]; then
  ENGINE="whispercpp"
else
  echo "Aucun sous-titre disponible, et aucun moteur Whisper configuré pour le fallback."
  echo ""
  echo "Pour activer la transcription audio (gratuit), choisis UNE option dans"
  echo "  $SKILL_DIR/.env  (copie .env.example) :"
  echo "  - Groq (recommandé)  : GROQ_API_KEY=...   (free tier, https://console.groq.com/keys)"
  echo "  - whisper.cpp local  : WHISPER_CPP_BIN=... + WHISPER_CPP_MODEL=..."
  exit 3
fi

# resolve the ffmpeg DIRECTORY (yt-dlp -x + whisper.cpp need ffmpeg & ffprobe).
# NEVER pass "." to yt-dlp: --ffmpeg-location "." overrides PATH and breaks it.
FFMPEG_DIR=""
if [ -n "${REELS_FFMPEG_DIR:-}" ] && [ -x "${REELS_FFMPEG_DIR}/ffmpeg.exe" ]; then
  FFMPEG_DIR="${REELS_FFMPEG_DIR}"
fi
if [ -z "$FFMPEG_DIR" ]; then
  shopt -s nullglob 2>/dev/null || true
  for c in \
    "$LOCALAPPDATA_BASH"/Microsoft/WinGet/Packages/*[Ff][Ff]mpeg*/*/bin/ffmpeg.exe \
    "$USERPROFILE_BASH"/AppData/Local/Microsoft/WinGet/Packages/*[Ff][Ff]mpeg*/*/bin/ffmpeg.exe; do
    [ -x "$c" ] && FFMPEG_DIR="$(dirname "$c")" && break
  done
  shopt -u nullglob 2>/dev/null || true
fi
if [ -z "$FFMPEG_DIR" ] && command -v ffmpeg >/dev/null 2>&1; then
  FFMPEG_DIR="$(dirname "$(command -v ffmpeg)")"
fi
# path form the Windows yt-dlp.exe accepts (C:/...)
FFMPEG_LOC=""
if [ -n "$FFMPEG_DIR" ]; then
  command -v cygpath >/dev/null 2>&1 && FFMPEG_LOC="$(cygpath -m "$FFMPEG_DIR" 2>/dev/null)"
  [ -z "$FFMPEG_LOC" ] && FFMPEG_LOC="$FFMPEG_DIR"
fi
FFMPEG_ARGS=()
[ -n "$FFMPEG_LOC" ] && FFMPEG_ARGS=(--ffmpeg-location "$FFMPEG_LOC")
FFMPEG_BIN="${FFMPEG_DIR:+$FFMPEG_DIR/}ffmpeg"

TMPDIR_R="${TMPDIR:-/tmp}/save-reel-$$"
mkdir -p "$TMPDIR_R"
trap 'rm -rf "$TMPDIR_R"' EXIT

# (cookies deja definis plus haut, URL-aware : YouTube -> REELS_YT_COOKIES_FILE ; autres -> browser/file)

# Post 100% images (sans audio) : le contenu est SUR les slides -> on les lit en VISION (OCR),
# puis on fusionne la description/commentaires. Utilisé pour les carrousels Insta (/p/), TikTok
# (/photo/ ou slideshow via short-link), et tout post sans piste audio. exit 10 = cookies manquants.
emit_vision_note() {
  local VISUAL VP_EXIT PMETA DOMAIN TITLE AUTHOR _CTX
  VISUAL="$(bash "$SCRIPT_DIR/visual_pass.sh" "$URL" 2>/dev/null)"; VP_EXIT=$?
  if [ "$VP_EXIT" = "10" ]; then
    # images inaccessibles (cookies Insta/TikTok manquants/expirés) : le contenu est SUR les slides,
    # pas dans la légende -> on ne sauve PAS de note légende-seule, on signale qu'il faut les cookies.
    echo "COOKIES_NEEDED"
    exit 10
  fi
  PMETA="$("$YTDLP" --skip-download --no-warnings --quiet "${COOKIE_ARGS[@]}" \
    --print "%(webpage_url_domain)s|||%(title)s|||%(uploader)s|||%(duration_string)s" "$URL" 2>/dev/null | grep -m1 '|||')"
  DOMAIN="$(printf '%s' "$PMETA" | awk -F'\\|\\|\\|' '{print $1}')"
  TITLE="$(printf '%s' "$PMETA"  | awk -F'\\|\\|\\|' '{print $2}')"
  AUTHOR="$(printf '%s' "$PMETA" | awk -F'\\|\\|\\|' '{print $3}')"
  _CTX="$(fetch_context)"
  if [ -z "$VISUAL" ] && [ -z "$_CTX" ]; then
    echo "Aucun texte lisible extrait de ce post (images sans texte ?)."
    exit 2
  fi
  [ -n "$_CTX" ] && VISUAL="$(printf '%s\n\n%s' "$VISUAL" "$_CTX")"
  printf '%s\n' "$VISUAL" > "$TRANSCRIPT_CACHE"
  write_meta "vision" "${DOMAIN:-?}" "$TITLE" "$AUTHOR" ""
  echo "Method: vision"
  echo "Source: ${DOMAIN:-?}"
  echo "Title: ${TITLE:-?}"
  echo "Author: ${AUTHOR:-?}"
  echo "Duration: -"
  echo "---"
  printf '%s\n' "$VISUAL"
  exit 0
}

# NB : pas de court-circuit basé sur l'URL. Un lien /p/ peut être une VIDÉO (reel listé via la
# collection Enregistrés) OU un carrousel d'images -> on décide RESULT-BASED plus bas : on tente
# l'audio ; s'il sort un mp3 = vidéo (transcription) ; sinon = post images -> emit_vision_note.

META="$("$YTDLP" \
  -x --audio-format mp3 \
  --postprocessor-args "ffmpeg:-ac 1 -ar 16000 -b:a 48k" \
  "${FFMPEG_ARGS[@]}" \
  "${COOKIE_ARGS[@]}" \
  --no-warnings --quiet --ignore-errors --no-simulate \
  -o "$TMPDIR_R/%(id)s.%(ext)s" \
  --print "%(webpage_url_domain)s|||%(title)s|||%(uploader)s|||%(duration_string)s" \
  "$URL" 2>&1)"; DL_EXIT=$?

MP3_FILE="$(ls "$TMPDIR_R"/*.mp3 2>/dev/null | sort | head -1)"
if [ -z "$MP3_FILE" ] || [ ! -f "$MP3_FILE" ]; then
  # Pas d'audio : soit un post 100% images dont l'URL ne le disait pas (carrousel/slideshow TikTok
  # via short-link vm./vt.tiktok.com, etc.), soit un média gated. On tente la passe VISION (OCR des
  # slides + description) ; si 0 image récupérée -> emit_vision_note sort COOKIES_NEEDED (cookies requis).
  emit_vision_note
fi

META_LINE="$(printf '%s' "$META" | grep -m1 '|||' || printf '%s' "$META" | tail -1)"
DOMAIN="$(printf '%s' "$META_LINE"  | awk -F'\\|\\|\\|' '{print $1}')"
TITLE="$(printf '%s' "$META_LINE"   | awk -F'\\|\\|\\|' '{print $2}')"
AUTHOR="$(printf '%s' "$META_LINE"  | awk -F'\\|\\|\\|' '{print $3}')"
DURATION="$(printf '%s' "$META_LINE" | awk -F'\\|\\|\\|' '{print $4}')"

# --- transcribe ---
TRANSCRIPT=""
if [ "$ENGINE" = "groq" ]; then
  groq_whisper() {  # <audiofile> -> transcript sur stdout ; code retour = code curl
    curl -sS --fail-with-body \
      -A "ReelsBrain/1.0 (+curl-like)" \
      -X POST "https://api.groq.com/openai/v1/audio/transcriptions" \
      -H "Authorization: Bearer ${GROQ_API_KEY}" \
      -F "file=@${1}" \
      -F "model=whisper-large-v3-turbo" \
      -F "response_format=text" \
      -F "temperature=0" 2>&1
  }
  # Groq plafonne chaque requête à 25 Mo. Si l'audio dépasse ~24 Mo (longue vidéo YT sans
  # sous-titres), on le découpe en segments de 25 min (~9 Mo en mono 48k) transcrits l'un après
  # l'autre -> n'importe quelle durée de vidéo passe.
  MP3_SIZE="$(wc -c < "$MP3_FILE" 2>/dev/null || echo 0)"
  if [ "${MP3_SIZE:-0}" -gt 24000000 ]; then
    mkdir -p "$TMPDIR_R/chunks"
    "$FFMPEG_BIN" -i "$MP3_FILE" -f segment -segment_time 1500 -c copy \
      "$TMPDIR_R/chunks/part_%03d.mp3" -loglevel error -y 2>/dev/null
    PARTS=("$TMPDIR_R"/chunks/part_*.mp3)
    [ -e "${PARTS[0]}" ] || PARTS=("$MP3_FILE")
    TRANSCRIPT=""
    for part in "${PARTS[@]}"; do
      PT="$(groq_whisper "$part")"; TR_EXIT=$?
      if [ $TR_EXIT -ne 0 ]; then
        echo "Erreur Groq Whisper :"
        echo "$PT"
        exit 2
      fi
      TRANSCRIPT="$(printf '%s %s' "$TRANSCRIPT" "$PT")"
    done
  else
    TRANSCRIPT="$(groq_whisper "$MP3_FILE")"; TR_EXIT=$?
    if [ $TR_EXIT -ne 0 ]; then
      echo "Erreur Groq Whisper :"
      echo "$TRANSCRIPT"
      exit 2
    fi
  fi
else # whispercpp
  "$FFMPEG_BIN" -i "$MP3_FILE" -ar 16000 -ac 1 -c:a pcm_s16le "$TMPDIR_R/audio.wav" -y -loglevel error
  "$WHISPER_CPP_BIN" -m "$WHISPER_CPP_MODEL" -f "$TMPDIR_R/audio.wav" -nt -otxt -of "$TMPDIR_R/out" >/dev/null 2>&1
  TRANSCRIPT="$(cat "$TMPDIR_R/out.txt" 2>/dev/null)"
fi

TRANSCRIPT="$(printf '%s' "$TRANSCRIPT" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -v '^$' || true)"

# Pas/peu de parole (ou REELS_VISION_ALWAYS=1) -> capter le TEXTE à l'écran (vision).
METHOD="whisper-${ENGINE}"
WC="$(printf '%s' "$TRANSCRIPT" | wc -w | tr -d ' ')"
if [ "${WC:-0}" -lt "${REELS_VISION_MIN_WORDS:-25}" ] || [ "${REELS_VISION_ALWAYS:-0}" = "1" ]; then
  VISUAL="$(bash "$SCRIPT_DIR/visual_pass.sh" "$URL" 2>/dev/null)"
  if [ -n "$VISUAL" ]; then
    if [ -n "$TRANSCRIPT" ]; then
      TRANSCRIPT="$(printf '%s\n\n[Texte à l'\''écran]\n%s' "$TRANSCRIPT" "$VISUAL")"
      METHOD="whisper+vision"
    else
      TRANSCRIPT="$VISUAL"; METHOD="vision"
    fi
  fi
fi
if [ -z "$TRANSCRIPT" ]; then
  echo "Aucune parole ni texte à l'écran exploitable (musique/visuel pur ?)."
  exit 2
fi

_CTX="$(fetch_context)"; [ -n "$_CTX" ] && TRANSCRIPT="$(printf '%s\n\n%s' "$TRANSCRIPT" "$_CTX")"
printf '%s\n' "$TRANSCRIPT" > "$TRANSCRIPT_CACHE"
write_meta "$METHOD" "$DOMAIN" "$TITLE" "$AUTHOR" "$DURATION"

echo "Method: $METHOD"
echo "Source: ${DOMAIN:-unknown}"
echo "Title: ${TITLE:-?}"
echo "Author: ${AUTHOR:-?}"
echo "Duration: ${DURATION:-?}"
echo "---"
printf '%s\n' "$TRANSCRIPT"
