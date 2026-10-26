#!/usr/bin/env python3
"""Backfill: enrich each note's topics with the SITUATIONS/GOALS where its advice helps.

Lets the bulletproof lexical recall catch "de loin" relations (e.g. "scaler mon agence"
-> a closing reel) by pre-expanding the query space at the source, via Groq (free).
Idempotent: skips notes already marked `enriched: true`. Updates topics + tags in place.
Usage: enrich_topics.py [--all]   (default: only un-enriched notes)
"""
import glob
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_DISTILL_MODEL", "llama-3.3-70b-versatile")
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)


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


def tagify(s):
    s = s.strip().replace(" ", "-")
    s = re.sub(r"[^0-9A-Za-zÀ-ſ_/-]", "", s)
    return s.strip("-").lower()


def vault():
    return os.environ.get("REELS_VAULT") or os.path.join(
        os.path.expanduser("~"), "Obsidian", "ReelsBrain")


def groq_situations(title, takeaways, transcript, key):
    user = (
        f"Conseil tiré d'un reel.\nTitre: {title}\nTakeaways: {takeaways}\n"
        f"Transcription (extrait): {transcript[:4000]}\n\n"
        "Liste 8 à 12 SITUATIONS / OBJECTIFS / PROBLÈMES concrets où ce conseil est pertinent, "
        "formulés comme une recherche ou question d'utilisateur (ex: \"trouver des clients\", "
        "\"scaler son agence\", \"être plus productif le matin\", \"mieux dormir\"). "
        "Pense large : toutes les questions auxquelles ce conseil répond, même indirectement. "
        "Réponds en JSON: {\"situations\": [\"...\", ...]}. Français, rien autour."
    )
    payload = {"model": MODEL, "temperature": 0.3, "response_format": {"type": "json_object"},
               "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(
        GROQ_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "User-Agent": "ReelsBrain/1.0 (+curl-like)"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read().decode("utf-8"))
    data = json.loads(body["choices"][0]["message"]["content"])
    return [str(s).strip() for s in (data.get("situations") or []) if str(s).strip()]


def parse_topics(fm):
    m = re.search(r"^topics:\s*\[(.*?)\]\s*$", fm, re.M)
    if not m:
        return []
    return [t.strip().strip('"').strip("'") for t in m.group(1).split(",") if t.strip()]


def main():
    load_env()
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        sys.stderr.write("GROQ_API_KEY manquant\n")
        sys.exit(2)
    force = "--all" in sys.argv[1:]
    notes_dir = os.path.join(vault(), "notes")
    done = skipped = failed = 0
    for p in sorted(glob.glob(os.path.join(notes_dir, "*.md"))):
        c = open(p, encoding="utf-8").read()
        m = FM_RE.match(c)
        if not m:
            continue
        fm, body = m.group(1), m.group(2)
        if not force and re.search(r"^enriched:\s*true\s*$", fm, re.M):
            skipped += 1
            continue
        topics = parse_topics(fm)
        title = (re.search(r"^#\s+(.+)$", body, re.M) or [None, ""])[1] if re.search(r"^#\s+(.+)$", body, re.M) else ""
        tk = re.split(r"^##\s+Takeaway\s*$", body, flags=re.M)
        takeaways = ""
        if len(tk) > 1:
            takeaways = re.split(r"^##\s+", tk[1], flags=re.M)[0].strip()
        tr = re.split(r"^##\s+Transcript\s*$", body, flags=re.M)
        transcript = tr[1].strip() if len(tr) > 1 else ""
        try:
            sits = groq_situations(title, takeaways, transcript, key)
        except Exception as e:
            sys.stderr.write(f"  fail {os.path.basename(p)}: {e}\n")
            failed += 1
            continue
        # merge (dedup, case-insensitive)
        seen = {t.lower() for t in topics}
        merged = list(topics)
        for s in sits:
            if s.lower() not in seen:
                merged.append(s)
                seen.add(s.lower())
        tags = []
        seent = set()
        for t in merged:
            tg = tagify(t)
            if tg and tg not in seent:
                tags.append(tg)
                seent.add(tg)
        fm2 = re.sub(r"^topics:.*$",
                     "topics: [" + ", ".join(json.dumps(t, ensure_ascii=False) for t in merged) + "]",
                     fm, count=1, flags=re.M)
        if re.search(r"^tags:", fm2, re.M):
            fm2 = re.sub(r"^tags:.*$", "tags: [" + ", ".join(tags) + "]", fm2, count=1, flags=re.M)
        else:
            fm2 = fm2.rstrip() + "\ntags: [" + ", ".join(tags) + "]"
        if not re.search(r"^enriched:\s*true", fm2, re.M):
            fm2 = fm2.rstrip() + "\nenriched: true"
        with open(p, "w", encoding="utf-8") as f:
            f.write("---\n" + fm2 + "\n---\n" + body)
        done += 1
        sys.stdout.buffer.write(f"OK {os.path.basename(p)[:40]} (+{len(sits)} situations)\n".encode("utf-8"))
    print(f"\nEnrichi: {done} | déjà fait: {skipped} | échec: {failed}")


if __name__ == "__main__":
    main()
