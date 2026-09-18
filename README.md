# LE COIN MALIN 34 — APPLICATION V1 AUTO

Cette version transforme le même service Railway en **application web mobile installable (PWA)** tout en gardant le bot Telegram.

## Automatique
Le groupe Telegram reste la source du catalogue. Quand le bot reçoit une nouvelle photo, vidéo ou un nouveau texte dans un topic, il met à jour `catalog_runtime.json`. L'application lit ce même catalogue : aucune double saisie.

- 🆕 Nouveautés actualisées automatiquement
- 📷 Compteurs photos
- 🎬 Compteurs vidéos
- 📝 Compteurs textes
- ✨ Nouveaux topics détectés par le bot
- 🛒 Commande via Telegram
- 📣 Accès au groupe Telegram
- 📱 Application installable depuis le navigateur

## Important sur les médias
Cette V1 utilise les publications Telegram comme source : les cartes ouvrent la photo/vidéo/message correspondant dans Telegram. Cela évite de dupliquer et d'héberger des milliers de médias sur Railway.

Pour afficher **les photos et vidéos directement dans l'application sans ouvrir Telegram**, il faudra une V2 avec un stockage média public (Cloudinary, S3/R2, etc.) et une migration/synchronisation des anciens médias.

## Railway
Après déploiement, il faut exposer le service avec un domaine Railway dans `Settings > Networking / Public Networking`.
Le serveur écoute déjà sur `PORT`.

Log attendu :
`AUTO APP-V1-AUTO: application mobile + catalogue Telegram automatique = ACTIVÉ`
