---
name: transcript
description: Récupère le transcript d'une vidéo TikTok, Instagram Reel ou YouTube via yt-dlp.
  Usage `/transcript <url>`. Output en plain text, FR prioritaire puis EN.
  Si la vidéo n'a pas de sous-titres dispos, renvoie une erreur claire (pas de fallback Whisper).
allowed-tools: [Bash]
---

# Transcript

Skill simple : prend une URL vidéo, sort le transcript en plain text pour que tu puisses enchaîner avec ton prompt.

## Plateformes supportées

- YouTube (toutes URLs `youtube.com` / `youtu.be`)
- TikTok (URLs `tiktok.com`, captions ou auto-generated quand dispos)
- Instagram Reels (URLs `instagram.com/reel/`, publics uniquement)

## Prerequisites

Installer `yt-dlp` une seule fois (gratuit, pas de clé API) :

```bash
winget install yt-dlp.yt-dlp
```

`ffmpeg` n'est PAS requis — on télécharge uniquement les `.vtt`, pas le média.

Vérifier : `yt-dlp --version`

## Usage

```
/transcript https://www.youtube.com/watch?v=...
/transcript https://www.tiktok.com/@user/video/...
/transcript https://www.instagram.com/reel/...
```

## Comportement attendu

Quand l'utilisateur invoque ce skill, exécuter exactement :

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/extract.sh" "$ARGUMENTS"
```

Puis afficher la sortie telle quelle. Le script gère :
- Validation URL
- Check `yt-dlp` installé
- Tentative subtitles manuelles → fallback auto-générées
- Langues : `fr`, `fr-FR`, `fr-CA`, `en`, `en-US`, `en-GB`
- Parsing VTT → plain text dédupliqué
- Cleanup du dossier temp

## Cas d'erreur

| Cas | Sortie |
|-----|--------|
| URL vide / invalide | Message d'erreur explicite |
| `yt-dlp` absent | Commande d'install affichée |
| Aucun subtitle dispo | `Aucun sous-titre disponible` + suggestion |
| Vidéo privée / supprimée | Erreur yt-dlp passée telle quelle |

## Format de sortie (cas succès)

```
Source: youtube.com
Title: ...
Author: ...
Duration: 12:34
---
[transcript plain text dédupliqué]
```

## Hors scope

- Pas de fallback Whisper (refusé par l'user, coût)
- Pas de téléchargement vidéo/audio
- Pas de support stories Insta (auth requise)
- Pas de batch multi-URL

Si besoin un jour : ajouter un mode `--whisper` qui télécharge l'audio et appelle l'API OpenAI Whisper (~15 lignes en plus dans `extract.sh`).
