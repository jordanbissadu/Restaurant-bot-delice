# Photos des plats dans les réponses Telegram — Design

Date : 2026-07-23
Statut : validé, prêt pour le plan d'implémentation

## Objectif

Envoyer les photos des plats au client Telegram après une réponse du bot portant
sur le menu, sans modifier l'agent conversationnel ni son prompt.

## Décisions structurantes

| Décision | Choix retenu | Raison |
|---|---|---|
| Source des images | Photos fournies par le restaurant, déposées dans Drive | Fidélité à l'assiette servie, pas de droits tiers |
| Association plat ↔ image | Fichier de correspondance `photos.csv` dans Drive | Robuste, plusieurs photos par plat, désactivation ligne à ligne |
| Déclenchement | Seuil : la réponse cite 1 à 4 plats | Évite le mur d'images sur une demande de carte complète |
| Qui décide | Post-traitement déterministe en code, pas le LLM | Testable sans modèle, coût nul en tokens, prompt inchangé |

### Pourquoi pas un outil confié au LLM

Un quatrième outil appelé par l'agent aurait exigé d'encoder la règle du seuil
dans un `SYSTEM_PROMPT` déjà long d'environ 5 000 mots. Les règles de prompt
sont ce qui casse en premier sous charge. Le code, lui, est déterministe et
testable en unitaire.

## État constaté avant travaux

Contrôles exécutés sur la base réelle le 2026-07-23 :

- 13 chunks, 74 lignes de menu ingérées.
- Les 40 noms du catalogue figurent **au caractère près** dans la base : 0 écart.
- 7 collisions de sous-chaînes détectées entre noms de plats.
- 40 images présentes, 3,62 Mo au total, aucune au-dessus de la limite Telegram.

Les collisions mesurées :

```
'Poisson Braisé'  contenu dans  'Poisson Braisé Façon Togolaise'
'Tchakpalo'       contenu dans  'Tchakpalo + Akpan'
'Bissap'          contenu dans  'Jus locaux (bissap, gingembre)'
'Gingembre'       contenu dans  'Jus locaux (bissap, gingembre)'
'Sodas'           contenu dans  'Eau / Sodas'
'Eau'             contenu dans  'Eau / Sodas'
'Eau'             contenu dans  'Fufu + Sauce Arachide + Agneau'
```

La dernière — « agn·eau » — impose la recherche par frontières de mots. Sans
elle, toute commande de fufu à l'agneau déclencherait une photo de bouteille
d'eau.

## Anomalie préexistante à corriger

`DriveClient.list_folder_files` parcourt le dossier surveillé récursivement, et
`run_sync` ingère tout ce qu'il remonte. Déposer des images dans ce dossier les
enverrait aujourd'hui au découpage et à la vectorisation : des chunks binaires
dans la base du menu et une facture d'embeddings inutile.

Correctif : router les fichiers par type MIME **avant** le diff. Les `image/*`
et le catalogue partent dans le chemin photos, le reste garde le chemin
documents actuel. À faire indépendamment de cette fonctionnalité.

## Architecture

```
message client → agent → texte de réponse
                            ↓
                   reply_text(texte)            inchangé
                            ↓
                   matcher : quels plats sont cités ?
                            ↓
                   0 ou plus de 4 → stop
                   1 à 4          → sender : sendPhoto / sendMediaGroup
```

L'agent n'est pas touché : ni `src/agent.py`, ni le `SYSTEM_PROMPT`, ni les
trois outils existants. Le point d'intégration unique est `on_message` dans
`src/telegram_bot.py`, après l'envoi du texte.

### Modules

| Module | Rôle | Dépendances |
|---|---|---|
| `src/photos/catalogue.py` | Parse `photos.csv`, réconcilie avec la collection | csv, Mongo |
| `src/photos/sync.py` | Chemin de sync dédié aux images et au catalogue | Drive, Mongo |
| `src/photos/matcher.py` | Détecte les plats cités dans un texte | **aucune** |
| `src/photos/sender.py` | Envoie l'album, gère le cache `file_id` | Telegram, Drive, Mongo |

`matcher.py` est une fonction pure : c'est là qu'est toute la logique, et elle
se teste sans démarrer quoi que ce soit.

## Données

### Côté Drive

Un sous-dossier `photos/` du dossier surveillé, contenant les images et un
Google Sheet `photos` exporté en CSV par l'API Drive (aucune dépendance
nouvelle : Drive sait exporter un Sheet en `text/csv`).

| plat | fichier | ordre | actif |
|---|---|---|---|
| Poulet Yassa | poulet-yassa.jpg | 1 | oui |
| Poulet Yassa | poulet-yassa-2.jpg | 2 | oui |
| Poisson Braisé Façon Togolaise | poisson-braise-facon-togolaise.jpg | 1 | oui |
| Eau | eau.jpg | 1 | non |

La colonne `plat` doit reprendre le nom exact du document menu : c'est la clé
de tout le mécanisme.

Quatre lignes sont livrées à `actif=non` — `Eau`, `Sodas`, `Eau / Sodas`,
`Jus frais` — parce que ce sont des mots que le bot écrit en prose sans parler
du produit, et qu'ils font doublon entre eux. Repassables à `oui` dans le CSV.

### Côté MongoDB

Collection `dish_photos`, une ligne par photo :

```
dish_name         "Poisson Braisé Façon Togolaise"   nom exact du menu
dish_key          "poisson braise facon togolaise"   normalisé, indexé
drive_file_id     "1a2b3c..."
content_hash      "sha256..."                        détecte le remplacement
telegram_file_id  "AgACAgQAAx..."                    cache, vide au départ
position          1
enabled           true
updated_at        datetime
```

Index unique sur `(dish_key, drive_file_id)`, index simple sur `dish_key`.

## Le matcher

Entrée : le texte de la réponse et le catalogue actif. Sortie : une liste de
plats. Algorithme :

1. Normaliser le texte : minuscules, accents retirés, apostrophes uniformisées,
   espaces réduits.
2. Trier les noms du catalogue **du plus long au plus court**.
3. Chercher chaque nom avec frontières de mots : `\bpoisson braise facon togolaise\b`.
4. À chaque trouvaille, enregistrer le plat puis **blanchir la zone trouvée**
   dans le texte de travail — la remplacer par des espaces, ce qui préserve les
   positions.
5. Rendre les plats dans leur **ordre d'apparition** dans le texte.
6. Appliquer le seuil : 0 → rien ; plus de `photos_max_dishes` → rien.

L'étape 4 règle les collisions de sous-chaînes. L'étape 3 règle « agneau ».

**Limite connue et assumée :** le matcher exige le nom complet. Si le bot écrit
« le Kékélen » au lieu de « Kékélen (Boules de Haricot) », rien ne part. Le
prompt impose déjà les noms exacts de la base. Chaque tour est logué avec le
nombre de plats détectés, afin de mesurer le taux réel en production. Si le
chiffre est mauvais, on ajoutera une colonne d'alias au catalogue — pas avant.

## L'envoi

Le matcher rend des **plats** ; le sender les développe en **photos** : pour
chaque plat, toutes ses lignes `enabled` triées par `ordre` croissant. Comme un
plat peut porter plusieurs photos, quatre plats peuvent dépasser les 10 images
par album qu'autorise Telegram. Le sender tronque donc à 10 images au total,
en respectant l'ordre des plats puis l'ordre des photos — les plats cités en
premier sont servis en premier.

Une seule image au final → `sendPhoto`. De deux à dix → `sendMediaGroup`, avec
une légende par image portant le nom du plat, suivie de
`photos_caption_suffix` s'il est configuré.

Résolution de la source, par photo :

```
telegram_file_id présent ?
  oui → envoyer l'identifiant, aucun octet ne transite
  non → télécharger depuis Drive → envoyer les octets
        → lire le file_id dans la réponse Telegram
        → l'écrire dans dish_photos
```

Chaque photo n'est donc téléchargée et uploadée qu'une seule fois dans sa vie.
Si `content_hash` change au sync, `telegram_file_id` est vidé pour cette ligne
seulement.

L'envoi est attendu après `reply_text`, pas lancé en tâche de fond : l'ordre
d'arrivée des messages est ainsi garanti, et le cache rend l'opération courte.

## Gestion d'erreur

Principe unique : **la photo ne dégrade jamais la conversation.** Le texte est
déjà parti quand l'envoi photo commence.

| Panne | Comportement |
|---|---|
| Fichier du CSV absent de Drive | Signalé dans `SyncReport`, ligne ignorée, le reste fonctionne |
| Drive injoignable à l'envoi | Log, aucune photo, le client a son texte |
| Telegram rejette l'image | Log avec le nom du plat, on passe au suivant |
| Catalogue vide ou absent | Le bot fonctionne comme avant la fonctionnalité |
| Toute autre exception | `try/except` englobant, log en `exception`, aucun message d'erreur au client |

`photos_enabled` coupe tout sans redéploiement.

## Configuration ajoutée

```
photos_enabled                   true
photos_max_dishes                4
photos_caption_suffix            ""          ex. "Photo d'illustration"
mongodb_collection_dish_photos   dish_photos
drive_photos_folder_name         photos
```

## Tests

L'essentiel de l'effort porte sur le matcher, sans aucune doublure :

- « Fufu + Sauce Arachide + Agneau » ne déclenche pas la photo « Eau »
- « Poisson Braisé Façon Togolaise » rend un plat, pas deux
- « Tchakpalo + Akpan » ne déclenche pas « Tchakpalo »
- Casse et accents : « POULET YASSA » et « poulet yassa » donnent le même résultat
- Ordre : trois plats cités sont rendus dans l'ordre du texte
- Seuil : cinq plats cités rendent une liste vide
- Ligne `actif=non` : jamais rendue
- Troncature : quatre plats portant trois photos chacun rendent 10 images, pas 12

Avec doublures :

- Parseur de catalogue : guillemets Windows, colonnes `actif` et `ordre`
- Sender avec un faux bot, sur le modèle de `KitchenSender` : vérifie
  `sendPhoto` contre `sendMediaGroup`, et surtout qu'un deuxième envoi ne
  déclenche **aucun** téléchargement
- Routage MIME au sync : une image ne part plus dans `ingest_document`

## Hors périmètre

- Outil photo confié au LLM pour les demandes explicites du client — évolution
  possible une fois le taux de déclenchement mesuré.
- Colonne d'alias dans le catalogue — seulement si les logs montrent que le
  matcher rate trop souvent.
- Désactivation automatique d'une ligne après échecs répétés d'envoi.

## Réserve documentée

Les images actuellement au catalogue proviennent majoritairement de
téléchargements web, pas de prises de vue du restaurant. Deux fichiers
seulement semblent être des photos maison. La question des droits d'auteur et
de la fidélité à l'assiette réellement servie reste ouverte et relève du
restaurant. Le mécanisme est indifférent à la provenance : remplacer les images
dans Drive suffit, sans toucher au code. En attendant, `photos_caption_suffix`
permet d'afficher « Photo d'illustration ».
