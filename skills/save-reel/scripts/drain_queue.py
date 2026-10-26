#!/usr/bin/env python3
"""ReelsBrain — local queue drainer (Phase 2, deferred-local design).

The Vercel bot enqueues reel URLs into Upstash Redis (list `reelsbrain:queue`)
the instant you send them — they're never lost even if this PC is off. This script,
run on the PC (scheduled task every few minutes), drains the queue: for each URL it
runs the local pipeline (process_reel.sh: yt-dlp + Groq + Obsidian note) using local
browser cookies, then replies on Telegram. Reads config from skills/save-reel/.env:
  KV_REST_API_URL, KV_REST_API_TOKEN   (same Upstash as the bot — read the queue)
  TELEGRAM_BOT_TOKEN                    (to reply "✅ Sauvé")
  GROQ_API_KEY, REELS_BROWSER          (used by process_reel.sh)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
VAULT = os.environ.get("REELS_VAULT") or os.path.join(
    os.path.expanduser("~"), "Obsidian", "ReelsBrain")
QUEUE_KEY = "reelsbrain:queue"
LOCK = os.path.join(VAULT, ".cache", "drain.lock")
LOCK_TTL = 20 * 60  # seconds
# posts-images dont les slides attendent les cookies Insta (à re-tenter à chaque drain)
PENDING = os.path.join(VAULT, ".cache", "pending_images.txt")
# capture "façon Loucash" : on lit la collection Enregistrés d'Instagram et on traite les nouveaux
SAVED_SEEN = os.path.join(VAULT, ".cache", "saved_seen.txt")    # shortcodes déjà vus
SAVED_STAMP = os.path.join(VAULT, ".cache", "saved_last_poll")  # throttle anti-ban


def canon(u):
    return (u or "").split("?")[0].rstrip("/")


def load_env():
    # skill .env first (wins via setdefault), then the bot's pulled env (KV creds + bot token)
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


def kv_lpop():
    base = os.environ.get("KV_REST_API_URL", "").strip().rstrip("/")
    token = os.environ.get("KV_REST_API_TOKEN", "").strip()
    if not base or not token:
        raise RuntimeError("KV_REST_API_URL / KV_REST_API_TOKEN manquants dans .env")
    req = urllib.request.Request(
        f"{base}/lpop/{urllib.parse.quote(QUEUE_KEY)}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8")).get("result")


def kv_lindex0():
    """Peek l'item le plus ancien SANS le retirer (crash-safe : on ne retire qu'après traitement)."""
    base = os.environ.get("KV_REST_API_URL", "").strip().rstrip("/")
    token = os.environ.get("KV_REST_API_TOKEN", "").strip()
    if not base or not token:
        raise RuntimeError("KV_REST_API_URL / KV_REST_API_TOKEN manquants dans .env")
    req = urllib.request.Request(
        f"{base}/lindex/{urllib.parse.quote(QUEUE_KEY)}/0",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8")).get("result")


def tg_send(chat_id, text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data), timeout=20).read()
    except Exception as e:
        sys.stderr.write(f"tg_send failed: {e}\n")


def find_bash():
    b = shutil.which("bash")
    if b:
        return b
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if os.path.isfile(c):
            return c
    return "bash"


def process(url):
    script = os.path.join(SCRIPT_DIR, "process_reel.sh")
    try:
        p = subprocess.run([find_bash(), script, url], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=300)
        out = (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "timeout (>5 min)"
    line = ""
    for ln in out.splitlines():
        if ln.startswith(("RESULT_OK", "RESULT_ERR", "RESULT_COOKIES", "RESULT_SKIP")):
            line = ln
    info = line.split("\t", 1)[1] if "\t" in line else ""
    if line.startswith("RESULT_OK"):
        return "ok", info
    if line.startswith("RESULT_SKIP"):
        return "skip", info
    if line.startswith("RESULT_COOKIES"):
        return "cookies", info
    if line.startswith("RESULT_ERR"):
        return "err", info or "erreur"
    return "err", "pipeline: pas de résultat"


def acquire_lock():
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    if os.path.isfile(LOCK):
        try:
            if time.time() - os.path.getmtime(LOCK) < LOCK_TTL:
                return False  # a fresh drain is already running
        except OSError:
            pass
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))
    return True


def pending_load():
    try:
        return [json.loads(l) for l in open(PENDING, encoding="utf-8") if l.strip()]
    except Exception:
        return []


def pending_save(items):
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    with open(PENDING, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def pending_add(url, chat_id):
    items = pending_load()
    if any(canon(i.get("url")) == canon(url) for i in items):
        return len(items)
    items.append({"url": url, "chatId": chat_id})
    pending_save(items)
    return len(items)


def pending_drain():
    """Re-tente les posts-images en attente (cookies peut-être rafraîchis). Garde ceux qui échouent encore."""
    items = pending_load()
    if not items:
        return
    still, done = [], 0
    for it in items:
        status, info = process(it.get("url"))
        if status == "ok":
            done += 1
            tg_send(it.get("chatId"), f"✅ Post images traité (slides lus) : {info}")
        else:
            still.append(it)
    pending_save(still)
    if done or still:
        sys.stderr.write(f"pending: {done} traités, {len(still)} encore en attente.\n")


def _vault_shortcodes():
    """Shortcodes déjà présents dans le vault (pour ne pas re-traiter ce qui est déjà importé)."""
    codes = set()
    notes = os.path.join(VAULT, "notes")
    try:
        files = os.listdir(notes)
    except OSError:
        return codes
    for fn in files:
        if not fn.endswith(".md"):
            continue
        try:
            c = open(os.path.join(notes, fn), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = re.search(r'^source_url:\s*"?(.*?)"?\s*$', c, re.M)
        if m:
            sc = re.search(r"instagram\.com/(?:p|reel|reels|tv)/([^/?\s]+)", m.group(1) or "")
            if sc:
                codes.add(sc.group(1))
    return codes


def poll_saved():
    """Lit la collection Enregistrés d'Instagram (gallery-dl + cookie) et traite les NOUVEAUX reels.
    Throttle pour ne pas marteler IG. Idempotent : seen-set + shortcodes déjà dans le vault.
    Tu tapes juste le bouton Enregistrer natif d'Insta en scrollant -> ça arrive ici tout seul."""
    user = (os.environ.get("REELS_IG_USERNAME") or "").strip()
    cookie = (os.environ.get("REELS_COOKIES_FILE") or "").strip()
    browser = (os.environ.get("REELS_BROWSER") or "").strip()
    if not user or not (browser or (cookie and os.path.isfile(cookie))):
        return  # pas configuré (username + une source cookie requis)
    interval = int(os.environ.get("REELS_SAVED_POLL_SECONDS", "1200"))
    try:
        if time.time() - os.path.getmtime(SAVED_STAMP) < interval:
            return  # déjà scanné récemment
    except OSError:
        pass
    cargs = (["--cookies-from-browser", browser] if browser
             else ["--cookies", cookie])
    rng = os.environ.get("REELS_SAVED_RANGE", "1-30")
    try:
        p = subprocess.run([sys.executable, "-m", "gallery_dl", *cargs, "--range", rng, "-g",
                            f"https://www.instagram.com/{user}/saved/"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        out = p.stdout or ""
    except (subprocess.TimeoutExpired, OSError) as e:
        sys.stderr.write(f"poll_saved: gallery-dl KO ({e})\n")
        return
    # cookie/session expiré -> gallery-dl échoue sur l'auth. NE PAS rester silencieux : alerter (1x/6h)
    # sinon la capture par bouton Enregistrer se casse sans que l'user le sache.
    if p.returncode != 0 and re.search(r"login|401|403|forbidden|not.*authenticat|HttpError 4", p.stderr or "", re.I):
        owner = (os.environ.get("REELS_OWNER_UID") or "").strip() or None
        marker = SAVED_STAMP + ".authwarn"
        try:
            last = os.path.getmtime(marker)
        except OSError:
            last = 0
        if time.time() - last > 6 * 3600:
            tg_send(owner, "⚠️ Je ne peux plus lire ta collection Enregistrés Instagram : ta session a "
                           "expiré. Ré-exporte ton cookies.txt (ou installe Firefox pour le full-auto) et "
                           "la capture par bouton Enregistrer repartira toute seule.")
            try:
                open(marker, "w").write(str(int(time.time())))
            except OSError:
                pass
        try:
            os.makedirs(os.path.dirname(SAVED_STAMP), exist_ok=True)
            open(SAVED_STAMP, "w").write(str(int(time.time())))
        except OSError:
            pass
        return
    codes = []
    for sc in re.findall(r"instagram\.com/(?:p|reel|reels|tv)/([^/?\s]+)", out):
        if sc not in codes:
            codes.append(sc)
    # horodater le scan (même vide) pour respecter l'intervalle
    try:
        os.makedirs(os.path.dirname(SAVED_STAMP), exist_ok=True)
        open(SAVED_STAMP, "w").write(str(int(time.time())))
    except OSError:
        pass
    if not codes:
        return
    first_run = not os.path.isfile(SAVED_SEEN)
    try:
        seen = set(l.strip() for l in open(SAVED_SEEN, encoding="utf-8") if l.strip())
    except OSError:
        seen = set()
    if first_run:
        # baseline : on mémorise l'existant SANS traiter -> pas de flood. Seuls les reels Enregistrés
        # APRÈS l'install s'importent tout seuls. Le backlog reste importable sur demande.
        try:
            with open(SAVED_SEEN, "w", encoding="utf-8") as f:
                for sc in sorted(set(codes)):
                    f.write(sc + "\n")
        except OSError:
            pass
        sys.stderr.write(f"poll_saved: baseline établie ({len(codes)} enregistrés mémorisés, non traités).\n")
        return
    vault_codes = _vault_shortcodes()
    new = [sc for sc in codes if sc not in seen and sc not in vault_codes]
    # tout ce qu'on a vu ce tour -> seen (évite de re-scanner d'anciens enregistrés non importés)
    try:
        with open(SAVED_SEEN, "w", encoding="utf-8") as f:
            for sc in sorted(seen | set(codes)):
                f.write(sc + "\n")
    except OSError:
        pass
    if not new:
        return
    owner = (os.environ.get("REELS_OWNER_UID") or "").strip() or None
    done = 0
    for sc in new:
        url = f"https://www.instagram.com/p/{sc}/"  # forme universelle (vidéo OU carrousel)
        status, info = process(url)
        if status == "ok":
            done += 1
            tg_send(owner, f"🔖 Reel Enregistré traité : {info}")
        elif status == "cookies":
            pending_add(url, owner)
            tg_send(owner, "📸 Un post Enregistré a son contenu sur les images et tes cookies Insta "
                           "ont expiré. Ré-exporte ton cookies.txt et je le traiterai tout seul.")
    if done:
        sys.stderr.write(f"poll_saved: {done} nouveau(x) reel(s) Enregistré(s) traité(s).\n")


def main():
    load_env()
    if not acquire_lock():
        sys.stderr.write("drain déjà en cours, skip.\n")
        return
    try:
        pending_drain()  # d'abord : re-tenter les posts-images en attente (cookies peut-être rafraîchis)
        poll_saved()     # capture "façon Loucash" : traiter les nouveaux reels Enregistrés sur Insta
        n_ok = n_err = n_cook = n_skip = 0
        last_chat = None
        while True:
            try:
                raw = kv_lindex0()  # PEEK l'item le plus ancien SANS le retirer
            except Exception as e:
                sys.stderr.write(f"file Redis injoignable : {e}\n")
                break
            if not raw:
                break
            try:
                item = json.loads(raw) if isinstance(raw, str) else raw
                url, chat_id = item.get("url"), item.get("chatId")
            except Exception:
                url, chat_id = (raw if isinstance(raw, str) else None), None
            if not url:
                try:
                    kv_lpop()  # item invalide -> le retirer pour ne pas boucler dessus
                except Exception:
                    break
                continue
            if chat_id:
                last_chat = chat_id
            status, info = process(url)
            if status == "ok":
                n_ok += 1
                tg_send(chat_id, f"✅ Sauvé : {info}")
            elif status == "skip":
                n_skip += 1
                tg_send(chat_id, "♻️ Déjà dans ton second cerveau (rien à refaire).")
            elif status == "cookies":
                n_cook += 1
                npend = pending_add(url, chat_id)
                tg_send(chat_id,
                        "📸 Post reçu, mais son contenu est SUR les images et je ne peux pas les lire : "
                        "tes cookies Instagram ont expiré.\n\n"
                        "➡️ Va sur instagram.com dans Chrome, ré-exporte ton cookies.txt, "
                        f"et je traiterai tout seul les {npend} post(s) en attente.")
            else:
                n_err += 1
                tg_send(chat_id, f"❌ Échec : {info}")
            # CRASH-SAFE : on ne retire l'item de la file qu'APRÈS l'avoir traité.
            # Si le PC s'éteint pendant process(), l'item reste en Redis -> re-traité au prochain
            # drain (save_note dédup par URL -> pas de doublon).
            try:
                kv_lpop()
            except Exception as e:
                sys.stderr.write(f"lpop échec (item re-tenté plus tard) : {e}\n")
                break
        total = n_ok + n_err + n_cook + n_skip
        if total:
            sys.stderr.write(f"drain terminé : {n_ok} ok, {n_skip} déjà présents, "
                             f"{n_cook} attente-cookies, {n_err} échec.\n")
        if total >= 5:  # gros backlog (typique au rallumage après une absence) -> 1 récap Telegram
            tg_send(last_chat, f"📥 Backlog traité : ✅ {n_ok} sauvés"
                    + (f" · ♻️ {n_skip} déjà présents" if n_skip else "")
                    + (f" · 📸 {n_cook} en attente cookies" if n_cook else "")
                    + (f" · ❌ {n_err} échecs" if n_err else "") + ".")
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    # La tâche planifiée tourne en pythonw (sans console) -> on logge nous-mêmes dans un fichier
    # rotatif pour ne plus être aveugle sur un plantage (le fameux "Last Result: 1" indéboguable).
    _log = os.path.join(VAULT, ".cache", "drain.log")
    try:
        os.makedirs(os.path.dirname(_log), exist_ok=True)
        if os.path.isfile(_log) and os.path.getsize(_log) > 1_000_000:  # rotation simple > 1 Mo
            try:
                os.replace(_log, _log + ".1")
            except OSError:
                pass
        sys.stderr = open(_log, "a", encoding="utf-8", buffering=1)
        sys.stderr.write(f"\n=== drain {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except OSError:
        pass
    try:
        main()
    except Exception:
        import traceback
        sys.stderr.write("DRAIN CRASH:\n" + traceback.format_exc() + "\n")
        raise
