#!/usr/bin/env python3
"""Bulk-import a list of reel/post URLs into ReelsBrain.

Dedups against the vault (skips already-saved), runs the full pipeline per URL via
process_reel.sh (transcript/vision/description -> distill -> Obsidian note), logs progress
to the vault .cache/import_log.txt, and is RESUMABLE (re-run skips done, retries failures).
Usage: batch_import.py <urls_file>
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VAULT = os.environ.get("REELS_VAULT") or os.path.join(
    os.path.expanduser("~"), "Obsidian", "ReelsBrain")


def canon(u):
    return u.split("?")[0].rstrip("/")


def existing_urls():
    s = set()
    for p in glob.glob(os.path.join(VAULT, "notes", "*.md")):
        try:
            c = open(p, encoding="utf-8").read()
        except Exception:
            continue
        m = re.search(r'^source_url:\s*"?(.*?)"?\s*$', c, re.M)
        if m:
            s.add(canon(m.group(1)))
    return s


def find_bash():
    b = shutil.which("bash")
    if b:
        return b
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.isfile(c):
            return c
    return "bash"


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: batch_import.py <urls_file>\n")
        sys.exit(2)
    urls = [l.strip() for l in open(sys.argv[1], encoding="utf-8") if l.strip().startswith("http")]
    seen, uniq = set(), []
    for u in urls:
        c = canon(u)
        if c not in seen:
            seen.add(c)
            uniq.append(u)
    done = existing_urls()
    todo = [u for u in uniq if canon(u) not in done]

    os.makedirs(os.path.join(VAULT, ".cache"), exist_ok=True)
    logf = open(os.path.join(VAULT, ".cache", "import_log.txt"), "w", encoding="utf-8")

    def log(s):
        sys.stdout.buffer.write((s + "\n").encode("utf-8"))
        sys.stdout.flush()
        logf.write(s + "\n")
        logf.flush()

    log(f"Liste: {len(uniq)} uniques | déjà en vault: {len(uniq)-len(todo)} | à traiter: {len(todo)}")
    bash = find_bash()
    script = os.path.join(SCRIPT_DIR, "process_reel.sh")
    env = dict(os.environ)
    env.setdefault("REELS_FETCH_COMMENTS", "0")  # bulk = fast (description gardée, pas les commentaires)

    ok = err = 0
    delay = float(os.environ.get("REELS_BATCH_DELAY", "0") or "0")  # pause entre reels (anti rate-limit Groq)
    for i, u in enumerate(todo, 1):
        if i > 1 and delay:
            time.sleep(delay)
        try:
            p = subprocess.run([bash, script, u], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=360, env=env)
            out = (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired:
            err += 1
            log(f"[{i}/{len(todo)}] ERR timeout | {u}")
            continue
        line = ""
        for ln in out.splitlines():
            if ln.startswith("RESULT_OK") or ln.startswith("RESULT_ERR"):
                line = ln
        if line.startswith("RESULT_OK"):
            ok += 1
            title = line.split("\t", 1)[1] if "\t" in line else ""
            log(f"[{i}/{len(todo)}] OK  {title}")
        else:
            err += 1
            reason = line.split("\t", 1)[1] if (line and "\t" in line) else "pas de résultat"
            log(f"[{i}/{len(todo)}] ERR {reason[:90]} | {u}")

    log(f"\n=== FINI : {ok} ok, {err} échecs sur {len(todo)} à traiter ===")
    logf.close()


if __name__ == "__main__":
    main()
