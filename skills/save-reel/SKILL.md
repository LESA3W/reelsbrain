---
name: save-reel
description: Transcrit un reel (Insta/TikTok/YouTube) et l'enregistre dans le second cerveau
  ReelsBrain (vault Obsidian) sous forme de note distillée (titre, topics+synonymes, 3 takeaways
  actionnables, transcript complet) + ligne d'index. Usage `/save-reel <url>`. Sous-titres d'abord,
  fallback Whisper (Groq free tier) pour les reels sans sous-titres.
allowed-tools: [Bash]
---

# save-reel

Transforme un reel utile en connaissance réutilisable par Claude. Pipeline :
**transcription** (déterministe, script) → **distillation** (toi, Claude) → **classement** (déterministe, script).

## Étape 1 — Transcrire

Exécuter :

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/save.sh" "$ARGUMENTS"
```

Codes de sortie :
- **0** : succès. La sortie est `Method: ...` puis `Source/Title/Author/Duration`, puis `---`, puis le transcript.
- **2** : pas de transcript possible (aucun sous-titre + audio non récupérable, reel gated, ou audio sans parole). Afficher le message du script et **s'arrêter** (ne rien écrire).
- **3** : aucun moteur Whisper configuré pour un reel sans sous-titres. Afficher le message (qui explique comment remplir `.env`) et **s'arrêter**.

Si gated/privé : le message suggère `REELS_BROWSER=chrome` dans `${CLAUDE_SKILL_DIR}/.env`. Relayer tel quel.

## Étape 2 — Distiller (ton job, en français)

Le transcript peut contenir des blocs `[Texte à l'écran]` (texte des images/slides via vision),
`[Description]` (légende) et `[Avis (commentaires)]` (avis de la commu). Sers-t'en comme contexte ;
base les takeaways sur le conseil, et reflète un signal fort des avis si pertinent.

À partir du transcript, produire :
- **title** : titre court et clair du sujet (pas le titre marketing du reel ; le *contenu* réel).
- **topics** : 12 à 20 termes pour RETROUVER ce reel même de loin. Mets (a) les mots-clés + SYNONYMES, ET surtout (b) les **situations / objectifs / problèmes** où ce conseil sert, formulés comme l'utilisateur les dirait (ex closing : "trouver des clients", "scaler son agence", "vendre ses prestations", "prospection"). C'est ça qui permet de le retrouver quand on en parle avec d'autres mots.
  - Ex : reel mélatonine → `["sommeil", "mélatonine", "rythme circadien", "insomnie", "mal dormir", "se réveiller en forme", "être fatigué le matin", "manque d'énergie", "routine du soir", "mieux dormir"]`.
- **takeaway** : 3 bullets **actionnables** (le conseil concret, pas un résumé). Si le reel n'a aucun conseil actionnable, le dire (1 bullet) plutôt que d'inventer.

Ne pas halluciner : si le transcript est bruité (musique, hors-sujet), le signaler honnêtement.

## Étape 3 — Classer (déterministe)

Le transcript brut est déjà en cache (écrit par save.sh) — **ne pas le réinclure** dans le JSON.
Fournir seulement la distillation + les métadonnées (lues à l'étape 1) :

```bash
python "${CLAUDE_SKILL_DIR}/scripts/save_note.py" <<'REEL_JSON_EOF'
{
  "url": "<l'url donnée>",
  "platform": "instagram|tiktok|youtube",
  "author": "<Author de l'étape 1>",
  "duration": "<Duration de l'étape 1>",
  "method": "<Method de l'étape 1>",
  "title": "<ton titre>",
  "topics": ["...", "..."],
  "takeaway": ["...", "...", "..."]
}
REEL_JSON_EOF
```

Si `python` échoue, réessayer avec `py -3` puis `python3`.

## Étape 4 — Confirmer

Afficher en 2 lignes : le titre + le chemin de la note créée + les topics. Exemple :
> ✅ Sauvé : **<titre>** → `notes/2026-06-21-...md`
> Topics : sommeil, mélatonine, rythme circadien, …

## Notes
- Réutilise le skill `transcript` (`~/.claude/skills/transcript/scripts/extract.sh`) pour le chemin sous-titres.
- Coût : 0€ (Groq Whisper free tier 2000 req/jour, ou whisper.cpp local).
- Le rappel automatique (que Claude ressorte ce reel tout seul) est géré par le hook Phase 1, pas par ce skill.
- Limite : Whisper = audio seul ; le texte incrusté à l'écran n'est pas capté.
