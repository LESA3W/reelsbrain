---
name: transcript-account
description: Transcrit TOUTES les videos (best-effort) d'un compte TikTok/YouTube/Instagram et les
  memorise dans le second cerveau ReelsBrain (vault Obsidian) : chaque video = une note distillee
  (titre, topics, takeaways, transcript), toutes reliees a un noeud central [[<compte>]] qui forme
  un cluster dans le graph. Usage `/transcript-account <url_compte | @handle> [urls videos sup...]`.
allowed-tools: [Bash]
---

# transcript-account

Aspire un compte entier dans ReelsBrain. Chaque video passe par le meme pipeline que `/save-reel`
(transcription sous-titres -> fallback Whisper Groq -> distillation LLM -> note Obsidian), mais toutes
les notes du compte sont **reliees a un noeud central `accounts/<compte>.md`** (lien `[[<compte>]]` +
tag du compte) -> elles forment un cluster visuel dans le graph Obsidian.

## Etape unique — lancer l'orchestrateur

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/transcript_account.sh" "$ARGUMENTS"
```

Le script fait tout, headless (pas de distillation manuelle a faire ici, `distill.py` s'en charge) :
1. **Enumere** les videos du compte via yt-dlp (best-effort : `@handle` + fallback `tiktokuser:channel_id`).
2. **Dedup** par ID de video, plus dedup finale par shortcode dans `save_note.py` (un renvoi = no-op).
3. Pour chaque video : `process_reel.sh` avec `REELS_ACCOUNT=<handle>` -> note reliee au noeud.
4. **Pace** entre les videos (`REELS_BATCH_DELAY`, defaut 8s) pour menager les quotas Groq / anti-ban.
5. Affiche un recap : X nouvelles, Y deja presentes, Z echecs, + le chemin du noeud central.

## Limite a relayer honnetement

TikTok **bride l'acces anonyme** : la liste auto peut etre **partielle** (page recente seulement).
Deux moyens de completer :
- **Passer des URLs de videos en argument** apres l'URL du compte (elles sont ajoutees a la liste) :
  `/transcript-account @themacrotape https://www.tiktok.com/@themacrotape/video/123 https://.../video/456`
- **Cookies TikTok** : renseigner `REELS_COOKIES_FILE` (export cookies.txt) ou `REELS_BROWSER` dans
  `~/.claude/skills/save-reel/.env` -> le listing pagine bien plus loin.

## Apres execution

Confirmer en 2-3 lignes : nombre de videos traitees + le noeud cree. Exemple :
> Aspire **@themacrotape** : 8 videos memorisees, reliees a `accounts/themacrotape.md`.
> Ouvre ce noeud dans Obsidian -> tout le cluster part de `[[themacrotape]]`.

## Notes
- Reutilise `save-reel/scripts/process_reel.sh`, `save.sh`, `distill.py`, `save_note.py`.
- Les notes video restent dans `notes/` (a plat) -> elles sont **rappelees par le hook** comme les reels.
  Le noeud central vit dans `accounts/` (hors scan du recall = pas de bruit), relie par nom dans le graph.
- Cout : 0EUR (Groq free tier). Gros compte = attention au quota quotidien Whisper/LLM Groq.
