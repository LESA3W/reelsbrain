#!/usr/bin/env python3
"""Fetch caption (description) + substantive comments ("avis") for a reel/post.

Adds creator's caption and community opinions to ReelsBrain content. Comments are
filtered to keep only substantive ones (real text, not emoji/CTA spam) and sorted by
likes. Prints two blocks: [Description] and [Avis (commentaires)].
Usage: context.py <url>
Env: REELS_BROWSER (cookies), REELS_FETCH_COMMENTS (default 1), REELS_MAX_COMMENTS (default 12)
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def find_ytdlp():
    w = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if w:
        return w
    la = os.environ.get("LOCALAPPDATA", "")
    for pat in (os.path.join(la, "Microsoft", "WinGet", "Packages", "*yt-dlp*", "**", "yt-dlp.exe"),
                os.path.join(la, "Microsoft", "WinGet", "Links", "yt-dlp.exe")):
        h = glob.glob(pat, recursive=True)
        if h:
            return h[0]
    return "yt-dlp"


def substantive(t):
    t = (t or "").strip()
    if len(t) < 15:
        return False
    letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", t)
    return len(letters) >= 8  # has real words, not just emoji/numbers/CTA keyword


def main():
    load_env()
    if len(sys.argv) < 2:
        return
    url = sys.argv[1]
    args = [find_ytdlp(), "--skip-download", "--no-warnings", "-J"]
    want_comments = os.environ.get("REELS_FETCH_COMMENTS", "1") == "1"
    if want_comments:
        args.append("--get-comments")
    br = os.environ.get("REELS_BROWSER")
    if br:
        args += ["--cookies-from-browser", br]
    args.append(url)
    try:
        p = subprocess.run(args, capture_output=True, timeout=150)
        d = json.loads(p.stdout.decode("utf-8", "replace"))
    except Exception as e:
        sys.stderr.write(f"context: {e}\n")
        return

    out = []
    desc = (d.get("description") or "").strip()
    if desc:
        out.append("[Description]\n" + desc)

    comments = d.get("comments") or []
    comments.sort(key=lambda c: c.get("like_count") or 0, reverse=True)
    maxc = int(os.environ.get("REELS_MAX_COMMENTS", "12") or "12")
    seen, kept = set(), []
    for c in comments:
        t = (c.get("text") or "").strip().replace("\n", " ")
        if not substantive(t):
            continue
        key = t.lower()[:60]
        if key in seen:
            continue
        seen.add(key)
        likes = c.get("like_count")
        tag = f" ({likes}❤)" if likes else ""
        kept.append(f"- @{c.get('author') or '?'}{tag}: {t[:220]}")
        if len(kept) >= maxc:
            break
    if kept:
        out.append("[Avis (commentaires)]\n" + "\n".join(kept))

    sys.stdout.buffer.write("\n\n".join(out).encode("utf-8"))


if __name__ == "__main__":
    main()
