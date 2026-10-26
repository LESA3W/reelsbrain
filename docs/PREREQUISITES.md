# Prerequisites

ReelsBrain has **no Python pip dependencies** — the scripts use the Python 3.13 standard library only. What it needs are a few external command-line tools and (optionally) a few API keys.

## Runtime

| Tool | Why | Install |
|---|---|---|
| **Python 3.13+** | runs the `.py` scripts (stdlib only) | https://www.python.org/downloads/ |
| **Bash** | runs the `.sh` orchestration scripts (Git Bash on Windows) | https://git-scm.com/downloads |

## External CLI tools

| Tool | Why | Install (Windows) |
|---|---|---|
| **yt-dlp** | download videos + fetch subtitles (YouTube / TikTok / Instagram) | `winget install yt-dlp.yt-dlp` |
| **ffmpeg** | extract and downsample audio before transcription | `winget install Gyan.FFmpeg` |
| **gallery-dl** | download Instagram / TikTok image carousels | `pip install gallery-dl` |
| **curl** | API calls (Groq, Upstash, Telegram) | ships with Windows 10+ / Git Bash |

Keep `yt-dlp` up to date — YouTube and TikTok change their internals often, and an outdated binary is the most common cause of a failed download:

```bash
yt-dlp -U
```

## API keys (optional, depending on features used)

| Key | Needed for | Where |
|---|---|---|
| `GROQ_API_KEY` | transcription of videos without captions + LLM distillation + vision pass | https://console.groq.com/keys (free tier) |
| `KV_REST_API_URL` / `KV_REST_API_TOKEN` | Telegram capture queue (always-on link capture) | Upstash KV / `vercel env pull` |
| `TELEGRAM_BOT_TOKEN` | the bot that replies when a video is saved | @BotFather on Telegram |

If you only run `/save-reel` locally on videos that already have captions, you do not strictly need any key — but Groq is what makes the no-caption and image-post paths work. All keys go in `skills/save-reel/.env` (copied from `.env.example`) and are never committed.

### Fully local alternative (no account)

Transcription can run entirely offline with [`whisper.cpp`](https://github.com/ggerganov/whisper.cpp) instead of Groq Whisper — set `WHISPER_CPP_BIN` and `WHISPER_CPP_MODEL` in `.env` (see `.env.example`).
