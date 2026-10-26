#!/usr/bin/env python3
"""Visual text extraction for ReelsBrain (reels sans parole, carrousels, texte à l'écran).

Sends each image/frame to Groq's Llama 4 Scout (free, multimodal) and extracts the
on-screen text/advice — as if someone read it aloud. Used by save.sh when speech is
absent/weak or when the media is a photo post. Prints the combined extracted text (UTF-8).

Usage: vision.py <image1> [<image2> ...]
Env: GROQ_API_KEY ; GROQ_VISION_MODEL (default meta-llama/llama-4-scout-17b-16e-instruct)
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROMPT = (
    "Lis TOUT le texte visible sur cette image : titre, texte incrusté, slide, sous-titres brûlés. "
    "Restitue-le fidèlement, en français, comme si tu transcrivais ce qui est écrit. "
    "Si c'est un conseil / une astuce / une étape, garde-le tel quel. "
    "Ne décris PAS l'image et n'ajoute AUCUN commentaire : sors uniquement le texte lu. "
    "S'il n'y a aucun texte lisible, réponds exactement : (aucun texte)."
)


def load_env():
    home = os.path.expanduser("~")
    for path in (os.path.join(SKILL_DIR, ".env"),
                 os.path.join(home, ".claude", "telegram-claude-bot", ".env.local"),
                 os.path.join(home, ".claude", "telegram-claude-bot", ".env")):
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def vision_one(path, key):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "jpeg"
    if ext == "jpg":
        ext = "jpeg"
    payload = {
        "model": MODEL, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}},
        ]}],
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "User-Agent": "ReelsBrain/1.0 (+curl-like)"}
    last = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(GROQ_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:
                body = json.loads(r.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            last = RuntimeError(f"Groq vision HTTP {e.code}: {e.read().decode('utf-8','replace')[:160]}")
            if e.code in (429, 500, 502, 503, 529):
                time.sleep(3 * (attempt + 1))
                continue
            raise last
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError) as e:
            last = RuntimeError(f"Groq vision: {e}")
            time.sleep(2 * (attempt + 1))
    raise last


def main():
    load_env()
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        sys.stderr.write("vision: GROQ_API_KEY manquant\n")
        sys.exit(2)
    blocks = []
    for p in sys.argv[1:]:
        if not os.path.isfile(p):
            continue
        try:
            t = vision_one(p, key).strip()
        except Exception as e:
            sys.stderr.write(f"vision {os.path.basename(p)}: {e}\n")
            continue
        if t and t.lower().replace(" ", "") not in ("(aucuntexte)", "aucuntexte", ""):
            # dedup consecutive identical frames (static on-screen text)
            if not blocks or blocks[-1] != t:
                blocks.append(t)
    sys.stdout.buffer.write("\n\n".join(blocks).encode("utf-8"))


if __name__ == "__main__":
    main()
