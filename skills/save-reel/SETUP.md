# ReelsBrain — Setup (archi : bot enfile → PC draine)

## Flux
```
Tu envoies un lien de reel à ton bot Telegram
  → webhook.js détecte le lien, le met en file Redis (reelsbrain:queue), répond "📥 Reçu"
  → ton PC (tâche planifiée) draine la file : transcript → distille → note Obsidian → "✅ Sauvé"
  → dans Claude Code, le hook ressort le takeaway tout seul (synonymes)
```
Capture = always-on (jamais perdu). Traitement = local, quand le PC est ON.

> Les scripts lisent automatiquement DEUX fichiers : l'env téléchargé du bot
> (`~/.claude/telegram-claude-bot/.env.local`) **et** le `.env` du skill. Donc **aucune recopie de clé à la main**.

## 1. Récupérer les clés du bot en local (KV + token Telegram)
Tes clés sont dans Vercel, pas sur ton PC. Télécharge-les une fois :
```bash
cd ~/.claude/telegram-claude-bot
vercel env pull .env.local
```
Ça crée `.env.local` avec `KV_REST_API_URL`, `KV_REST_API_TOKEN`, `TELEGRAM_BOT_TOKEN` (que le drainer lira tout seul).

## 2. Clé Groq (la seule à ajouter — ton bot ne l'a pas)
Le bot utilise Anthropic ; la transcription a besoin de **Groq** (c'est ton dashboard qui a déjà une clé Groq Whisper).
- Copie `.env.example` → `.env` (dans `~/.claude/skills/save-reel/`), mets `GROQ_API_KEY=gsk_...`
  (réutilise celle du dashboard, ou crée-en une gratuite : https://console.groq.com/keys).
- (optionnel) `REELS_BROWSER=chrome` pour les reels Insta gated (Chrome fermé).

## 2bis. Cookies Insta — OBLIGATOIRE pour les posts 100% images (carrousels)
Le contenu de ces posts est **sur les images** (slides), et Instagram les gate derrière la connexion.
Sans cookies, ces posts partent en **file d'attente** + tu reçois une **alerte Telegram** pour les configurer.
1. Dans Chrome (connecté à Insta), installe l'extension **"Get cookies.txt LOCALLY"**.
2. Va sur `instagram.com`, clique l'extension → **Export** → enregistre le fichier sous
   `~/.claude/skills/save-reel/.cookies/instagram.txt` (crée le dossier `.cookies`).
3. Dans le `.env` du skill : `REELS_COOKIES_FILE=C:/Users/<you>/.claude/skills/save-reel/.cookies/instagram.txt`
4. **Quand ça expire** (quelques semaines) : les posts-images échouent → alerte Telegram + rappel à l'ouverture de Claude Code → tu ré-exportes le fichier (même chemin) → le drainer re-traite les posts en attente **tout seul**.

> Pour re-lire les slides des 38 posts déjà importés (légende seule) une fois les cookies en place :
> `python ~/.claude/skills/save-reel/scripts/reprocess_image_posts.py` (garde-fou : s'arrête si les cookies ne marchent pas).

## 3. Déployer la modif du bot (prod, via GitHub)
La détection de reels est déjà ajoutée dans `~/.claude/telegram-claude-bot/api/webhook.js`
(≈15 lignes, isolées, aucune nouvelle env var côté Vercel). Déploie **par GitHub** (jamais `vercel --prod` direct) :
```bash
cd ~/.claude/telegram-claude-bot
git add api/webhook.js && git commit -m "feat: enqueue reel links for ReelsBrain" && git push
```
Vercel auto-déploie sur push. (Ajoute `REELS_OWNER_UID=<ton_uid_telegram>` dans l'env Vercel pour que le bot ne réponde qu'à toi.)

## 4. Drainer automatique (tâche planifiée, toutes les 5 min quand le PC est ON)
```bash
schtasks //Create //TN "ReelsBrain Drain" //SC MINUTE //MO 5 //RL LIMITED //F \
  //TR "pythonw \"C:\\Users\\<you>\\.claude\\skills\\save-reel\\scripts\\drain_queue.py\""
```
(Stop : `schtasks //End //TN "ReelsBrain Drain"` · Supprimer : `//Delete //TN "ReelsBrain Drain"`.)
Drainer à la main quand tu veux : `python ~/.claude/skills/save-reel/scripts/drain_queue.py`

## 5. Activer le rappel
**Redémarre Claude Code** (charge le hook `UserPromptSubmit`). Puis pose une question en utilisant
d'autres mots que ceux du reel → le takeaway doit ressortir + citer `[[note]]`.

## Tester sans Telegram (pipeline local direct)
```bash
bash ~/.claude/skills/save-reel/scripts/process_reel.sh "<url-du-reel>"
```
Doit finir par `RESULT_OK	<titre>` et créer une note dans `~/Obsidian/ReelsBrain/notes/`.
Ou en interactif : `/save-reel <url>` dans Claude Code (Claude distille lui-même).

## Récap des entrées
| Quoi | Où | Pourquoi |
|------|-----|----------|
| GROQ_API_KEY | skill `.env` | transcription (reels sans subs) + distillation headless |
| KV_REST_API_URL / _TOKEN | `vercel env pull` → `.env.local` | lire la file Redis remplie par le bot |
| TELEGRAM_BOT_TOKEN | `vercel env pull` → `.env.local` | répondre "✅ Sauvé" |
| REELS_BROWSER | skill `.env` | reels Insta gated uniquement |
| push webhook.js | GitHub → Vercel | le bot enfile les reels |
| tâche planifiée | Windows | drainer la file quand le PC est ON |
| restart Claude Code | — | activer le hook de rappel |
