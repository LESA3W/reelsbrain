#!/usr/bin/env python3
"""Re-traite les posts-images /p/ qui n'ont que leur LÉGENDE (pas le texte des slides).

À lancer UNE FOIS les cookies Insta en place (REELS_COOKIES_FILE). Garde-fou : teste 1 post
d'abord ; si les images sont inaccessibles (cookies KO) -> abandon sans rien casser. Sinon,
pour chaque note légende-seule : supprime l'ancienne note + sa ligne d'index, relance le
pipeline (process_reel.sh) -> nouvelle note avec le contenu des slides. Les posts non traités
(cookies/erreur) sont mis dans pending_images.txt.
Usage: reprocess_image_posts.py
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("REELS_VAULT") or os.path.join(
    os.path.expanduser("~"), "Obsidian", "ReelsBrain")
NOTES = os.path.join(VAULT, "notes")
INDEX = os.path.join(VAULT, "_index.md")
PENDING = os.path.join(VAULT, ".cache", "pending_images.txt")


def find_bash():
    b = shutil.which("bash")
    if b:
        return b
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.isfile(c):
            return c
    return "bash"


def run(url, overwrite=False):
    """Run process_reel.sh; return (status, info). status in ok/cookies/err.
    overwrite=True -> REELS_OVERWRITE=1 : save_note remplace la note SEULEMENT si le
    traitement réussit (jamais de suppression-avant-confirmation)."""
    env = dict(os.environ)
    if overwrite:
        env["REELS_OVERWRITE"] = "1"
    try:
        p = subprocess.run([find_bash(), os.path.join(SCRIPT_DIR, "process_reel.sh"), url],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=360, env=env)
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return "err", "timeout"
    line = ""
    for ln in out.splitlines():
        if ln.startswith(("RESULT_OK", "RESULT_ERR", "RESULT_COOKIES")):
            line = ln
    info = line.split("\t", 1)[1] if "\t" in line else ""
    if line.startswith("RESULT_OK"):
        return "ok", info
    if line.startswith("RESULT_COOKIES"):
        return "cookies", info
    return "err", info or "?"


def caption_only_posts():
    """Notes /p/ dont le transcript commence par [Description] = aucune lecture de slides."""
    out = []
    for p in glob.glob(os.path.join(NOTES, "*.md")):
        c = open(p, encoding="utf-8", errors="replace").read()
        mu = re.search(r'^source_url:\s*"?(.*?)"?\s*$', c, re.M)
        if not mu or "/p/" not in mu.group(1):
            continue
        ms = re.split(r"^##\s+Transcript\s*$", c, flags=re.M)
        body = ms[1].strip() if len(ms) > 1 else ""
        if body.lstrip().startswith("[Description]") or not body.strip():
            out.append((p, mu.group(1)))
    return out


def remove_index_line(note_id):
    try:
        lines = open(INDEX, encoding="utf-8").read().splitlines(keepends=True)
        kept = [l for l in lines if f"[[{note_id}]]" not in l]
        open(INDEX, "w", encoding="utf-8").write("".join(kept))
    except Exception:
        pass


def pending_add(url):
    try:
        items = [json.loads(l) for l in open(PENDING, encoding="utf-8") if l.strip()]
    except Exception:
        items = []
    if any((i.get("url") or "").split("?")[0] == url.split("?")[0] for i in items):
        return
    items.append({"url": url, "chatId": ""})
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    with open(PENDING, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def main():
    posts = caption_only_posts()
    print(f"Posts /p/ légende-seule à re-traiter : {len(posts)}")
    if not posts:
        return
    # garde-fou : tester 1 post (sans overwrite -> visual_pass tourne et nous dit si cookies KO)
    print(f"Test cookies sur 1 post ({posts[0][1]})…", flush=True)
    status, _ = run(posts[0][1], overwrite=False)
    if status == "cookies":
        print("❌ Cookies Insta non fonctionnels (images inaccessibles). "
              "Configure REELS_COOKIES_FILE (cookies.txt frais) puis relance. Rien n'a été touché.")
        sys.exit(3)
    delay = float(os.environ.get("REELS_BATCH_DELAY", "15") or "15")  # pacing anti rate-limit IG
    done = cook = err = 0
    for i, (path, url) in enumerate(posts):
        if i > 0:
            time.sleep(delay)
        # REELS_OVERWRITE=1 : la note n'est remplacée QUE si le traitement réussit (sinon conservée).
        st, inf = run(url, overwrite=True)
        if st == "ok":
            done += 1
            sys.stdout.buffer.write(f"  OK   {inf}\n".encode("utf-8"))
            sys.stdout.flush()
        elif st == "cookies":
            cook += 1
            pending_add(url)
            print(f"  COOKIES (en attente, note conservée) {url}", flush=True)
        else:
            err += 1
            print(f"  ERR (note conservée) {inf[:60]} {url}", flush=True)
    print(f"\n=== Re-traitement : {done} upgradés (slides), {cook} en attente cookies, {err} erreurs ===")


if __name__ == "__main__":
    main()
