#!/usr/bin/env bash
# Headless end-to-end: URL -> transcript (save.sh) -> distill (Groq LLM) -> file (save_note.py).
# Used by the Telegram watcher. Also runnable by hand to test Phase 2 without Telegram.
# Prints a parseable last line: "RESULT_OK\t<title>" or "RESULT_ERR\t<reason>".
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
for _ef in \
  "$HOME/.claude/telegram-claude-bot/.env.local" \
  "$HOME/.claude/telegram-claude-bot/.env" \
  "$SKILL_DIR/.env"; do
  [ -f "$_ef" ] && { set -a; . "$_ef"; set +a; }
done

run_py() { if command -v python >/dev/null 2>&1; then python "$@"; elif command -v py >/dev/null 2>&1; then py -3 "$@"; else python3 "$@"; fi; }

URL="${1:-}"
if [ -z "$URL" ]; then echo "RESULT_ERR	url manquante"; exit 1; fi

# 0) déjà dans le vault ? -> on ne relance RIEN (ni yt-dlp, ni Groq Whisper, ni LLM). No-op instantané.
if run_py "$SCRIPT_DIR/save_note.py" --exists "$URL" >/dev/null 2>&1; then
  printf 'RESULT_SKIP\t%s\n' "$URL"
  exit 0
fi

# 1) transcribe
SAVE_OUT="$(bash "$SCRIPT_DIR/save.sh" "$URL" 2>&1)"; SE=$?
if [ $SE -eq 10 ]; then
  # post images dont les slides nécessitent les cookies Insta (manquants/expirés)
  printf 'RESULT_COOKIES\t%s\n' "$URL"
  exit 10
fi
if [ $SE -ne 0 ]; then
  REASON="$(printf '%s' "$SAVE_OUT" | tr '\n' ' ' | cut -c1-300)"
  printf 'RESULT_ERR\t%s\n' "$REASON"
  exit $SE
fi

# 2) distill (Groq LLM) -> full save_note JSON
# IMPORTANT : stderr SEPARE de stdout. distill.py ecrit des warnings sur stderr (ex "map chunk X
# echoue" en map-reduce) ; si on capturait 2>&1, ces lignes polluaient le JSON -> save_note json.loads
# crashait ("classement: Traceback"). Donc stdout seul -> DJSON, stderr -> fichier (pour le message d'erreur).
DERR="$(mktemp 2>/dev/null || echo "${TMPDIR:-/tmp}/distill_err.$$")"
DJSON="$(run_py "$SCRIPT_DIR/distill.py" --url "$URL" 2>"$DERR")"; DE=$?
if [ $DE -ne 0 ] || [ -z "$DJSON" ]; then
  printf 'RESULT_ERR\tdistillation: %s\n' "$(cat "$DERR" 2>/dev/null | tr '\n' ' ' | cut -c1-300)"
  rm -f "$DERR"
  exit 3
fi
rm -f "$DERR"

# 3) file the note + index
NOTE_OUT="$(printf '%s' "$DJSON" | run_py "$SCRIPT_DIR/save_note.py" 2>&1)"; NE=$?
if [ $NE -ne 0 ]; then
  printf 'RESULT_ERR\tclassement: %s\n' "$(printf '%s' "$NOTE_OUT" | tr '\n' ' ' | cut -c1-300)"
  exit 4
fi

TITLE="$(printf '%s' "$DJSON" | run_py -c "import sys,json;t=json.loads(sys.stdin.buffer.read().decode('utf-8')).get('title','');sys.stdout.buffer.write(t.encode('utf-8'))" 2>/dev/null)"
printf '%s\n' "$NOTE_OUT"
printf 'RESULT_OK\t%s\n' "${TITLE:-(sans titre)}"
