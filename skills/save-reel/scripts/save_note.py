#!/usr/bin/env python3
"""Persist a distilled reel into the ReelsBrain vault.

Reads ONE JSON object from stdin with keys:
  url, platform, author, duration, method, title,
  topics (list[str]), takeaway (list[str]), transcript (str)

Writes notes/<YYYY-MM-DD>-<slug>.md and appends a line to _index.md.
Deterministic on purpose: Claude does the distillation, this does the filing.
Vault path: $REELS_VAULT or ~/Obsidian/ReelsBrain
"""
import json
import os
import re
import sys
import unicodedata
from datetime import date


def canon_key(u):
    """Clé de déduplication stable, par ID de contenu (pas par URL exacte) : un même reel peut
    apparaître en /p/XXX ET /reel/XXX, et une vidéo YouTube porte son ID dans ?v= (donc NE PAS
    couper sur '?' pour YouTube, sinon toutes les vidéos collideraient sur .../watch)."""
    u = (u or "").strip().split("#")[0]
    # YouTube : watch?v=ID, youtu.be/ID, shorts/ID, live/ID -> ID avant tout split sur '?'
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)|youtu\.be/)([\w-]{6,})", u)
    if m:
        return "yt:" + m.group(1)
    u = u.split("?")[0].rstrip("/")
    m = re.search(r"instagram\.com/(?:p|reel|reels|tv)/([^/]+)", u)
    if m:
        return "ig:" + m.group(1)
    m = re.search(r"tiktok\.com/.+/(?:video|photo)/(\d+)", u)
    if m:
        return "tt:" + m.group(1)
    return u


def slugify(text, maxlen=60):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:maxlen].strip("-")) or "reel"


def tagify(s):
    # Obsidian tag: pas d'espaces (-> tirets), garde lettres/accents/chiffres/_-/
    s = s.strip().replace(" ", "-")
    s = re.sub(r"[^0-9A-Za-zÀ-ſ_/-]", "", s)
    return s.strip("-").lower()


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in str(v).splitlines() if s.strip()]


def _remove_index_line(vault, note_id):
    idx = os.path.join(vault, "_index.md")
    try:
        lines = open(idx, encoding="utf-8").read().splitlines(keepends=True)
        open(idx, "w", encoding="utf-8").write("".join(l for l in lines if f"[[{note_id}]]" not in l))
    except Exception:
        pass


def _upsert_account_hub(vault, account, note_id, title):
    """Noeud central d'un compte : accounts/<compte>.md liste toutes ses vidéos ([[wikilinks]]).
    Hors de notes/ (pas scanné par le hook de recall = pas de bruit), mais relié dans le graph."""
    hub_dir = os.path.join(vault, "accounts")
    try:
        os.makedirs(hub_dir, exist_ok=True)
    except OSError:
        return
    hub = os.path.join(hub_dir, f"{account}.md")
    line = f"- [[{note_id}]] — {title}"
    try:
        content = open(hub, encoding="utf-8", errors="replace").read()
    except OSError:
        content = (f"---\ntype: account\naccount: {account}\ntags: [{account.lower()}]\n---\n\n"
                   f"# {account}\n\nToutes les vidéos transcrites de ce compte "
                   f"(cluster relié dans le graph).\n\n<!-- BEGIN_VIDEOS -->\n<!-- END_VIDEOS -->\n")
    if f"[[{note_id}]]" in content:
        return  # déjà listé
    if "<!-- END_VIDEOS -->" in content:
        content = content.replace("<!-- END_VIDEOS -->", line + "\n<!-- END_VIDEOS -->", 1)
    else:
        content = content.rstrip() + "\n" + line + "\n"
    try:
        open(hub, "w", encoding="utf-8").write(content)
    except OSError:
        pass


def find_existing_note(notes_dir, url):
    """Nom de fichier d'une note déjà sauvée pour cette URL (dédup par shortcode via canon_key), sinon None."""
    key = canon_key(url)
    if not key:
        return None
    try:
        files = os.listdir(notes_dir)
    except OSError:
        return None
    for fn in files:
        if not fn.endswith(".md"):
            continue
        try:
            c = open(os.path.join(notes_dir, fn), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = re.search(r'^source_url:\s*"?(.*?)"?\s*$', c, re.M)
        if m and canon_key(m.group(1)) == key:
            return fn
    return None


def main():
    vault = os.environ.get("REELS_VAULT") or os.path.join(
        os.path.expanduser("~"), "Obsidian", "ReelsBrain"
    )
    notes_dir = os.path.join(vault, "notes")

    # mode --exists <url> : exit 0 si déjà sauvé (affiche la note), exit 1 sinon. Sert au court-circuit
    # AMONT de process_reel.sh -> ne PAS relancer tout le pipeline (Groq Whisper + LLM) sur un lien connu.
    if len(sys.argv) >= 3 and sys.argv[1] == "--exists":
        fn = find_existing_note(notes_dir, sys.argv[2])
        if fn:
            print(fn)
            sys.exit(0)
        sys.exit(1)

    os.makedirs(notes_dir, exist_ok=True)
    # read stdin as UTF-8 bytes: on Windows sys.stdin defaults to cp1252 and mojibakes accents
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))

    # idempotence : même reel (même shortcode, que ce soit /p/ ou /reel/) déjà sauvé -> pas de doublon
    existing = find_existing_note(notes_dir, data.get("url"))
    if existing:
        if os.environ.get("REELS_OVERWRITE") == "1":
            # remplacer : on n'arrive ici qu'APRÈS un traitement réussi (transcript+distill ok),
            # donc supprimer l'ancienne note + sa ligne d'index puis réécrire est sûr.
            try:
                os.remove(os.path.join(notes_dir, existing))
            except OSError:
                pass
            _remove_index_line(vault, existing[:-3])
        else:
            print(f"SKIP déjà présent : {existing}")
            return

    title = (data.get("title") or "Sans titre").strip()
    topics = as_list(data.get("topics"))
    takeaway = as_list(data.get("takeaway"))

    # transcript: inline if provided, else read the raw file save.sh cached
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        tfile = data.get("transcript_file") or os.path.join(vault, ".cache", "last_transcript.txt")
        try:
            with open(tfile, "r", encoding="utf-8", errors="replace") as f:
                transcript = f.read().strip()
        except FileNotFoundError:
            transcript = ""

    today = date.today().isoformat()

    # compte source (skill /transcript-account) : cluster les vidéos autour d'un noeud [[<compte>]]
    account = re.sub(r"[^0-9A-Za-z._-]", "", (os.environ.get("REELS_ACCOUNT") or "").lstrip("@")).strip()

    slug = slugify(title)
    base = f"{today}-{slug}"
    fname = f"{base}.md"
    n = 2
    while os.path.exists(os.path.join(notes_dir, fname)):
        fname = f"{base}-{n}.md"
        n += 1
    note_id = fname[:-3]  # without .md, used as the [[wikilink]]

    def yaml_str(s):
        return json.dumps(str(s), ensure_ascii=False)  # safe quoting

    fm = ["---"]
    fm.append(f"source_url: {yaml_str(data.get('url', ''))}")
    fm.append(f"platform: {yaml_str(data.get('platform', ''))}")
    fm.append(f"author: {yaml_str(data.get('author', ''))}")
    fm.append(f"date_added: {today}")
    fm.append(f"duration: {yaml_str(data.get('duration', ''))}")
    fm.append(f"method: {yaml_str(data.get('method', ''))}")
    if account:
        fm.append(f"account: {yaml_str(account)}")
    fm.append("topics: [" + ", ".join(yaml_str(t) for t in topics) + "]")
    # tags = topics slugifiés -> Obsidian relie les reels par sujet dans le graph ; + le compte source
    tags = [t for t in (tagify(t) for t in topics) if t]
    if account and account.lower() not in tags:
        tags.insert(0, account.lower())
    fm.append("tags: [" + ", ".join(tags) + "]")
    fm.append("---")

    body = ["", f"# {title}", ""]
    if account:
        body += [f"Compte : [[{account}]]", ""]  # lien vers le noeud central -> cluster dans le graph
    body += ["## Takeaway", ""]
    body += [f"- {b}" for b in takeaway] or ["- (à distiller)"]
    body += ["", "## Transcript", "", transcript, ""]

    note_path = os.path.join(notes_dir, fname)
    with open(note_path, "w", encoding="utf-8") as f:
        f.write("\n".join(fm) + "\n".join(body) + "\n")

    # --- update _index.md (insert before END_REELS marker) ---
    first_take = (takeaway[0] if takeaway else title).replace("\n", " ").strip()
    index_line = f"- [[{note_id}]] · topics: {', '.join(topics)} · {first_take}"
    index_path = os.path.join(vault, "_index.md")
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            idx = f.read()
        if "<!-- END_REELS -->" in idx:
            idx = idx.replace("<!-- END_REELS -->", index_line + "\n<!-- END_REELS -->", 1)
        else:
            idx = idx.rstrip() + "\n" + index_line + "\n"
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(idx)
    except FileNotFoundError:
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("# ReelsBrain — Index\n\n<!-- BEGIN_REELS -->\n" + index_line + "\n<!-- END_REELS -->\n")

    # noeud central du compte (skill /transcript-account) : relie la vidéo au hub [[<compte>]]
    if account:
        _upsert_account_hub(vault, account, note_id, title)

    # régénère le dashboard visuel (best-effort, ne bloque jamais la sauvegarde)
    try:
        import subprocess
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_viewer.py")],
                       timeout=30, capture_output=True)
    except Exception:
        pass

    print(f"OK note    : {note_path}")
    print(f"OK index   : {index_path}")
    print(f"note_id    : {note_id}")


if __name__ == "__main__":
    main()
