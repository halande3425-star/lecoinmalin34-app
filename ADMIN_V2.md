# ADMIN — V2 BOUTIQUE PRO

L'API admin permet :
- prix
- stock
- tailles (ex. 36, 37, 38… ou S, M, L, XL)
- activation/désactivation produit
- liste des commandes
- changement du statut de suivi

## Sécurité Railway
Créer deux variables Railway :
- `ADMIN_PASSWORD` = votre mot de passe admin (ne jamais le publier sur GitHub)
- `SHOP_SECRET_KEY` = une longue valeur secrète aléatoire

Le contact paiement est configuré vers `@lecoinmalin34w`.

## Important
Cette V2 fournit la base boutique/commandes. Elle ne remplace pas encore automatiquement les messages Telegram historiques par des fiches produits avec prix/tailles : ces informations doivent être renseignées par l'admin, car elles ne sont pas présentes de façon structurée dans le catalogue Telegram.
