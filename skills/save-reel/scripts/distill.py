#!/usr/bin/env python3
"""Headless distillation for the Telegram capture path.

Reads the transcript + metadata that save.sh cached, asks a Groq LLM (free tier)
to produce title / topics(synonym-rich) / takeaway, and prints the full JSON that
save_note.py expects on stdout. Interactive /save-reel uses Claude instead; this is
the no-human path. Requires GROQ_API_KEY. Model overridable via GROQ_DISTILL_MODEL.

Usage: distill.py   (reads $REELS_VAULT/.cache/{last_transcript.txt,last_meta.tsv})
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retire regulierement des modeles (llama-3.x tous decommissionnes en 2026). On tente une liste :
# si le modele courant renvoie 404 "does not exist / decommissioned", on bascule sur le suivant.
DEFAULT_MODEL = os.environ.get("GROQ_DISTILL_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODELS = [m for m in ("openai/gpt-oss-20b", "qwen/qwen3.8-27b") if m != DEFAULT_MODEL]

SYSTEM = (
    "Tu distilles la transcription d'un reel (Insta/TikTok/YouTube) en connaissance "
    "réutilisable. Réponds UNIQUEMENT en JSON valide, en français."
)
USER_TMPL = """\
Transcription du reel (auteur: {author}) :
\"\"\"
{transcript}
\"\"\"

La transcription peut contenir des blocs : [Texte à l'écran] (texte des images/slides),
[Description] (légende du créateur), [Avis (commentaires)] (avis de la communauté).
Sers-t'en comme CONTEXTE. Base les "takeaway" sur le CONSEIL du reel (parole + texte/description).
Si les avis donnent un signal fort (ça marche / arnaque / nuance importante), reflète-le dans un takeaway ou les topics.

Produis un objet JSON avec EXACTEMENT ces clés :
- "title": titre court et clair du SUJET réel (pas un titre marketing).
- "topics": liste de 12 à 20 termes pour RETROUVER ce reel plus tard, même de loin. Mets :
  (a) les mots-clés du contenu + leurs SYNONYMES ;
  (b) surtout les SITUATIONS / OBJECTIFS / PROBLÈMES où ce conseil sert, formulés comme
      l'utilisateur les dirait dans une question (ex pour un reel de closing : "trouver des clients",
      "scaler son agence", "vendre ses prestations", "prospection", "signer des contrats",
      "développer son business"). C'est ÇA qui permet de retrouver le reel quand on en parle
      avec d'autres mots. Pense à toutes les questions auxquelles ce conseil répond.
- "takeaway": liste de 3 conseils ACTIONNABLES (le concret à faire, pas un résumé). Si le reel
  n'a aucun conseil actionnable, mets une seule entrée le disant.

JSON uniquement, rien autour."""


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


def vault():
    return os.environ.get("REELS_VAULT") or os.path.join(
        os.path.expanduser("~"), "Obsidian", "ReelsBrain"
    )


def read_meta(path):
    meta = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "\t" in line:
                    k, v = line.rstrip("\n").split("\t", 1)
                    meta[k] = v
    except FileNotFoundError:
        pass
    return meta


def platform_from(domain, url):
    s = (domain or "") + " " + (url or "")
    s = s.lower()
    if "instagram" in s:
        return "instagram"
    if "tiktok" in s:
        return "tiktok"
    if "youtu" in s:
        return "youtube"
    return domain or ""


MAX_SINGLE = 12000      # au-dela, on passe en MAP-REDUCE pour lire TOUT le transcript (0 miette)
CHUNK_CHARS = 11000     # taille d'un morceau envoye au LLM a l'etape MAP

MAP_SYSTEM = (
    "Tu lis un EXTRAIT de la transcription d'une video. Extrais fidelement les points importants de "
    "CET extrait (conseils, faits, chiffres, definitions, etapes). Ne resume pas a l'exces, ne perds "
    "aucune info utile. N'invente rien. Reponds UNIQUEMENT en JSON valide, en francais."
)
MAP_TMPL = """\
Extrait {i}/{n} de la transcription (auteur: {author}) :
\"\"\"
{chunk}
\"\"\"

Produis un objet JSON avec EXACTEMENT cette cle :
- "points": liste de puces concises (string), une par conseil/fait/concept/etape important present
  dans CET extrait. Garde les chiffres et termes techniques. N'invente rien, ne deborde pas de l'extrait.
JSON uniquement."""


def _groq_call(system, user):
    """Un appel chat Groq -> dict JSON. Bascule de modele sur 404, retry sur 400/429/5xx."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY manquant (voir .env)")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Cloudflare (devant api.groq.com) renvoie 403/1010 sur le UA "Python-urllib" par défaut
        "User-Agent": "ReelsBrain/1.0 (+curl-like)",
    }
    last = None
    for model in [DEFAULT_MODEL, *FALLBACK_MODELS]:  # bascule si un modele est retire par Groq
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(payload).encode("utf-8")
        for attempt in range(5):
            try:
                req = urllib.request.Request(GROQ_URL, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=120) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                return json.loads(body["choices"][0]["message"]["content"])
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:200]
                last = RuntimeError(f"Groq HTTP {e.code} ({model}): {detail}")
                if e.code == 404 or "does not exist" in detail or "decommission" in detail.lower():
                    break
                if e.code in (400, 429, 500, 502, 503, 529):
                    time.sleep(2 * (attempt + 1))
                    continue
                raise last
            except (urllib.error.URLError, TimeoutError) as e:
                last = RuntimeError(f"Groq injoignable : {e}")
                time.sleep(2 * (attempt + 1))
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                last = RuntimeError(f"Groq réponse invalide : {e}")
                time.sleep(2 * (attempt + 1))
    raise last


def _chunks(text, size):
    """Decoupe sur des frontieres de ligne pour ne pas casser une phrase au milieu."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if cur and len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        while len(line) > size:  # ligne unique plus grosse que size -> coupe dure
            out.append(line[:size])
            line = line[size:]
        cur += line
    if cur.strip():
        out.append(cur)
    return out


def _map_points(transcript, author):
    """MAP : lit TOUT le transcript morceau par morceau -> liste fusionnee des points (0 miette)."""
    points = []
    chunks = _chunks(transcript, CHUNK_CHARS)
    for i, ch in enumerate(chunks, 1):
        try:
            r = _groq_call(MAP_SYSTEM, MAP_TMPL.replace("{i}", str(i)).replace("{n}", str(len(chunks)))
                           .replace("{author}", author or "?").replace("{chunk}", ch))
            for p in r.get("points", []):
                p = str(p).strip()
                if p:
                    points.append(p)
        except Exception as e:
            sys.stderr.write(f"map chunk {i}/{len(chunks)} echoue (ignore): {e}\n")
    return points


def groq_distill(transcript, author):
    transcript = transcript or ""
    # Court : un seul appel (comportement historique).
    if len(transcript) <= MAX_SINGLE:
        return _groq_call(SYSTEM, USER_TMPL.replace("{author}", author or "?")
                          .replace("{transcript}", transcript))
    # Long : MAP (lit tout) -> REDUCE (fusionne points -> topics/takeaway finaux).
    points = _map_points(transcript, author)
    if not points:  # map totalement rate -> repli sur le debut du transcript (mieux que rien)
        return _groq_call(SYSTEM, USER_TMPL.replace("{author}", author or "?")
                          .replace("{transcript}", transcript[:MAX_SINGLE]))
    merged = "\n".join(f"- {p}" for p in points)
    guard = 0
    while len(merged) > MAX_SINGLE and guard < 3:  # video tres longue : recompresse les points
        points = _map_points(merged, author)
        merged = "\n".join(f"- {p}" for p in points) if points else merged[:MAX_SINGLE]
        guard += 1
    return _groq_call(SYSTEM, USER_TMPL.replace("{author}", author or "?")
                      .replace("{transcript}", merged[:MAX_SINGLE]))


def main():
    load_env()
    v = vault()
    url = os.environ.get("REEL_URL", "")
    for i, a in enumerate(sys.argv[1:]):
        if a == "--url" and i + 2 <= len(sys.argv[1:]):
            url = sys.argv[1:][i + 1]

    meta = read_meta(os.path.join(v, ".cache", "last_meta.tsv"))
    url = url or meta.get("url", "")
    try:
        with open(os.path.join(v, ".cache", "last_transcript.txt"), encoding="utf-8", errors="replace") as f:
            transcript = f.read().strip()
    except FileNotFoundError:
        transcript = ""
    if not transcript:
        sys.stderr.write("distill: transcript vide\n")
        sys.exit(2)

    try:
        d = groq_distill(transcript, meta.get("author", ""))
    except RuntimeError as e:
        sys.stderr.write(f"distill: {e}\n")
        sys.exit(2)

    out = {
        "url": url,
        "platform": platform_from(meta.get("domain", ""), url),
        "author": meta.get("author", ""),
        "duration": meta.get("duration", ""),
        "method": meta.get("method", ""),
        "title": d.get("title") or meta.get("title") or "Sans titre",
        "topics": d.get("topics") or [],
        "takeaway": d.get("takeaway") or [],
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
