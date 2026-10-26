#!/usr/bin/env python3
"""Range le vault ReelsBrain : hierarchie 2 niveaux + 7 noeuds-themes (MOC) + tags de couleur.

MODELE (demande par l'user) :
- Note de COMPTE (frontmatter `account:`) -> reliee UNIQUEMENT a son hub `accounts/<compte>.md`
  (via le lien `Compte : [[<compte>]]` deja dans la note + sa ligne dans le hub). Le hub -> son theme
  (`topics/<theme>.md`). Donc : video -> compte -> theme. On NE liste PAS les videos de compte dans le
  MOC (sinon double-lien video<->theme = le bordel actuel).
- Note STANDALONE (reel Insta/TikTok sans compte) -> listee directement dans son theme MOC.
- Chaque note + chaque hub recoit UN tag de theme `t/<theme>` (idempotent, surgical sur la ligne
  `tags:`) -> permet de COLORER les grappes dans le graphe Obsidian via colorGroups.

Non destructif a part la ligne `tags:` de chaque note (remplacement chirurgical). Idempotent.
Usage : python organize_vault.py [--dry]   (--dry = n'ecrit rien, affiche juste ce qui changerait)
"""
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows: eviter les crashs cp1252 a l'affichage

VAULT = os.environ.get("REELS_VAULT") or os.path.join(
    os.path.expanduser("~"), "Obsidian", "ReelsBrain")
NOTES = os.path.join(VAULT, "notes")
TOPICS = os.path.join(VAULT, "topics")
ACCOUNTS = os.path.join(VAULT, "accounts")

# Ordre = priorite pour les egalites de score. memecoins en tete pour rester PUR (mots-cles specifiques).
THEMES = [
    ("memecoins", "Memecoins", [
        "memecoin", "meme-coin", "memecoins", "rug-pull", "rugpull", "rug", "bundle", "instabond",
        "instabonding", "solana", "pump", "pumpfun", "dev-wallet", "dev-sell", "kol", "token", "jeton",
        "sniper", "snipe", "bonding", "shitcoin", "degen", "spidy", "vamp", "bundle-wallet", "memescope",
        "lowcap", "low-cap", "trenching", "narrative"]),
    ("trading", "Trading & bots", [
        "trading", "forex", "scalping", "backtest", "mt5", "metatrader", "chart", "bougie", "candle",
        "ict", "smc", "bpr", "profit-factor", "drawdown", "strategie-de-trading", "algorithmique", "algo",
        "bot-trading", "trade", "stop-loss", "pip", "eurusd", "macro", "macroeconomie", "banque-centrale",
        "tradingview", "broker", "win-rate", "aitb", "position", "imbalance", "liquidite", "quantitatif",
        "prop-firm", "ftmo", "martingale", "grid"]),
    ("ofm", "OFM / influenceuse IA", [
        "ofm", "onlyfans", "influenceu", "influenceur", "influenceuse", "whale", "whales", "chatting",
        "chatteur", "dm", "avatar", "fansly", "dropfans", "juicy", "nsfw", "ambre", "alexya", "persona",
        "fans", "nude", "sexy", "model-ia", "ai-girl", "sauveur"]),
    ("business-agence", "Business / Agence / Prospection", [
        "prospection", "agence", "scaler", "scaler-son-agence", "closing", "seo", "client", "site-web",
        "vente", "vendre", "freelance", "lead", "demarchage", "offre", "business", "entreprise", "franchis",
        "chiffre-affaires", "facturation", "comptabilite", "acquisition", "devis", "cold-call", "b2b",
        "referencement", "google-ads", "landing", "tunnel", "saas", "llc", "stripe"]),
    ("contenu-reseaux", "Contenu & reseaux sociaux", [
        "contenu", "hook", "viralite", "viral", "reel", "reels", "growth", "audience", "engagement",
        "reseaux-sociaux", "montage", "caption", "thumbnail", "abonnes", "vues", "algorithme",
        "creer-du-contenu", "storytelling", "edits", "carrousel", "publication", "post"]),
    ("ia-dev-outils", "IA / Dev / Outils", [
        "claude", "claude-code", "cloud-code", "obsidian", "n8n", "api", "open-source", "ollama",
        "automatisation", "automation", "python", "script", "mcp", "agent", "llm", "prompt", "workflow",
        "supabase", "vercel", "github", "docker", "code", "developpement", "intelligence-artificielle",
        "outil", "plugin", "markdown", "second-cerveau", "no-code", "integration", "scraping"]),
    ("perso", "Perso / developpement perso", [
        "eloquence", "relation", "amour", "sommeil", "dormir", "habitude", "mindset", "confiance",
        "motivation", "discipline", "productivite", "communication", "sante", "bien-etre", "apprentissage",
        "memoire", "lecture", "parler", "developpement-personnel", "organisation", "temps", "focus"]),
]
THEME_TITLE = {k: t for k, t, _ in THEMES}
# override : TOUTES les videos de ces comptes vont dans ce theme (quels que soient les mots-cles)
ACCOUNT_THEME = {"alxcooks_off": "memecoins", "LeckSol": "memecoins", "daumen": "memecoins",
                 "themacrotape": "trading"}


def strip_accents(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def classify(tagline, title, account):
    if account in ACCOUNT_THEME:
        return ACCOUNT_THEME[account]
    hay = strip_accents(tagline + " " + title)
    best, best_score = None, 0
    for key, _title, kws in THEMES:  # l'ordre donne la priorite aux egalites
        score = sum(1 for kw in kws if strip_accents(kw) in hay)
        if score > best_score:
            best, best_score = key, score
    return best  # None si aucun mot-cle (-> divers)


def stamp_theme_tag(path, theme, dry):
    """Pose/remplace le tag de theme `t/<theme>` en tete de la ligne `tags:` (chirurgical, idempotent)."""
    try:
        c = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False
    m = re.search(r"^tags:\s*\[(.*)\]\s*$", c, re.M)
    want = f"t/{theme}"
    if not m:
        return False  # pas de ligne tags -> on ne touche pas au frontmatter
    tags = [t.strip() for t in m.group(1).split(",") if t.strip()]
    tags = [t for t in tags if not t.startswith("t/")]  # retire l'ancien tag de theme
    tags = [want] + tags
    newline = "tags: [" + ", ".join(tags) + "]"
    if newline == c[m.start():m.end()]:
        return False  # deja bon
    if not dry:
        open(path, "w", encoding="utf-8").write(c[:m.start()] + newline + c[m.end():])
    return True


def main():
    dry = "--dry" in sys.argv
    try:
        files = sorted(os.listdir(NOTES))
    except OSError as e:
        print("vault introuvable:", e)
        return
    # buckets[theme] = {"accounts": set, "standalone": [(id,title)]}
    buckets = {k: {"accounts": set(), "standalone": []} for k, _, _ in THEMES}
    buckets["divers"] = {"accounts": set(), "standalone": []}
    total = tagged = 0
    for fn in files:
        if not fn.endswith(".md"):
            continue
        total += 1
        p = os.path.join(NOTES, fn)
        c = open(p, encoding="utf-8", errors="replace").read()
        tags = re.search(r"^tags:\s*(.*)$", c, re.M)
        acct = re.search(r'^account:\s*"?([^"\n]+)"?', c, re.M)
        title = re.search(r"^#\s+(.+)$", c, re.M)
        account = acct.group(1).strip() if acct else ""
        theme = classify(tags.group(1) if tags else "", title.group(1) if title else fn, account) or "divers"
        if stamp_theme_tag(p, theme, dry):
            tagged += 1
        nid, t = fn[:-3], (title.group(1).strip() if title else fn[:-3])
        if account:
            buckets[theme]["accounts"].add(account)   # la video reste sous SON compte, pas dans le MOC
        else:
            buckets[theme]["standalone"].append((nid, t))

    os.makedirs(TOPICS, exist_ok=True)

    def write_moc(key, ttitle):
        accts = sorted(buckets[key]["accounts"])
        stand = sorted(buckets[key]["standalone"], reverse=True)
        lines = [f"---\ntype: moc\ntheme: {key}\ntags: [t/{key}]\n---\n", f"# {ttitle}\n",
                 f"Noeud-theme. {len(accts)} compte(s) relie(s) + {len(stand)} note(s) standalone.\n"]
        if accts:
            lines.append("## Comptes")
            lines += [f"- [[{a}]]" for a in accts]
            lines.append("")
        lines.append("## Notes (hors comptes)")
        lines += [f"- [[{nid}]] — {t}" for nid, t in stand] or ["- (aucune)"]
        if not dry:
            open(os.path.join(TOPICS, f"{key}.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

    for key, ttitle, _ in THEMES:
        write_moc(key, ttitle)
    if buckets["divers"]["standalone"] or buckets["divers"]["accounts"]:
        write_moc("divers", "A trier")

    # tag de theme sur les hubs de compte (pour qu'ils prennent la couleur de leur theme)
    for acc, theme in ACCOUNT_THEME.items():
        hub = os.path.join(ACCOUNTS, f"{acc}.md")
        if os.path.isfile(hub):
            stamp_theme_tag(hub, theme, dry)

    # index des themes
    idx = ["---\ntype: moc\n---\n", "# Carte des themes\n", "Les grands noeuds du second cerveau.\n"]
    idx += [f"- [[{k}]] — {THEME_TITLE[k]} ({len(buckets[k]['accounts'])} comptes + "
            f"{len(buckets[k]['standalone'])} notes)" for k, _, _ in THEMES]
    if not dry:
        open(os.path.join(TOPICS, "_MOC.md"), "w", encoding="utf-8").write("\n".join(idx) + "\n")

    # rapport
    print(f"{'[DRY] ' if dry else ''}Total notes: {total} | tags de theme (re)poses: {tagged}")
    cl = 0
    for key, ttitle, _ in THEMES:
        n_a, n_s = len(buckets[key]["accounts"]), len(buckets[key]["standalone"])
        cl += n_s + n_a
        print(f"  {key:16} {n_a} compte(s) + {n_s:4} standalone  {ttitle}")
    d = buckets["divers"]
    print(f"  {'divers':16} {len(d['accounts'])} + {len(d['standalone']):4}  (non classees)")


if __name__ == "__main__":
    main()
