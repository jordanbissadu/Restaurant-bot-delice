# Restaurant Le Delice - Bot Telegram RAG

Pipeline de donnees : un dossier Google Drive partage est synchronise vers
MongoDB Atlas (Docling -> chunks -> embeddings OpenAI).

Bot conversationnel : un agent Pydantic AI (`gpt-4.1-mini`) repond aux clients
sur Telegram, cherche dans le menu, prend les commandes et notifie la cuisine.

## Prerequis

- Python 3.11+, UV
- Un cluster MongoDB Atlas
- Un compte de service Google avec l'API Drive activee

## Mise en place du compte de service Google

1. Console Google Cloud > **APIs & Services** > activer **Google Drive API**
2. **IAM & Admin** > **Service Accounts** > creer un compte de service
3. Onglet **Keys** > **Add key** > **JSON** > enregistrer dans
   `credentials/service-account.json`
4. Copier l'adresse email du compte de service
   (`xxx@projet.iam.gserviceaccount.com`)
5. Dans Google Drive, **partager le dossier** avec cette adresse en lecture
6. Renseigner `GOOGLE_DRIVE_FOLDER_ID` dans `.env` (l'ID est dans l'URL du
   dossier : `https://drive.google.com/drive/folders/<ID>`)

## Installation

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env   # puis renseigner les valeurs
```

> Si un autre projet a un venv actif dans le shell (variable `VIRTUAL_ENV`
> renseignee), `uv pip install` cible ce venv-la. Desactivez-le d'abord, ou
> prefixez par `env -u VIRTUAL_ENV`.

## Index MongoDB

```bash
uv run python -m scripts.create_indexes
```

Le script cree les index classiques et affiche la definition JSON des index
Atlas Search (`vector_index`, `text_index`) a coller dans l'UI Atlas, sur la
collection `chunks`. Ces index ne peuvent pas etre crees par pymongo.

## Photos des plats

Le bot illustre ses reponses quand elles citent 1 a 4 plats. Les images vivent
dans Drive, a cote des documents du menu, accompagnees d'un catalogue `photos`
(Google Sheet ou CSV) aux colonnes `plat`, `fichier`, `ordre`, `actif`.

La colonne `plat` doit reprendre le nom exact du document menu : c'est la cle
d'association. Passer `actif` a `non` retire une photo sans supprimer le
fichier.

Chaque image n'est telechargee et envoyee a Telegram qu'une seule fois : son
`file_id` est ensuite reutilise. Remplacer une image dans Drive invalide
automatiquement ce cache au cycle de synchronisation suivant.

`PHOTOS_ENABLED=false` coupe la fonctionnalite sans redeploiement.

## Utilisation

```bash
# Un cycle de synchronisation, pour verifier la configuration
uv run python -m scripts.run_sync_once

# Worker periodique
uv run python -m src.worker

# API operationnelle
uv run uvicorn src.api:app --port 8000
```

## Tests

```bash
uv run python -m pytest tests/ -v -m unit          # sans dependance externe
uv run python -m pytest tests/ -v -m integration   # necessite Mongo / OpenAI / Drive
```

> Invoquer pytest via `python -m pytest` (et non `uv run pytest`) : cela garantit
> que le pytest utilise est bien celui de l'environnement du projet, meme si un
> autre venv est actif dans le shell.

## Bot Telegram

Creer le bot aupres de [@BotFather](https://t.me/BotFather), recuperer le token
et le placer dans `TELEGRAM_BOT_TOKEN`.

Pour `TELEGRAM_KITCHEN_CHAT_ID` : ajouter le bot au groupe cuisine, envoyer un
message dans ce groupe, puis lire l'identifiant via

```bash
curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
```

L'identifiant d'un groupe est negatif.

### Lancer le bot

```bash
uv run python -m src.telegram_bot
```

Le bot fonctionne en long-polling : aucune URL publique n'est necessaire.

### Commandes client

- `/start` ou `/reset` — reinitialise la conversation
- tout autre message — traite par l'agent

## Tester l'agent de bout en bout

Parcours minimal pour discuter avec le bot depuis zero. Chaque etape suppose la
precedente reussie.

1. **Dependances et configuration**

   ```bash
   uv venv
   uv pip install -e ".[dev]"
   cp .env.example .env
   ```

   Renseigner dans `.env` au minimum : `MONGODB_URI`, `OPENAI_API_KEY`,
   `TELEGRAM_BOT_TOKEN`, `GOOGLE_DRIVE_FOLDER_ID`. Le compte de service Google
   doit exister dans `credentials/service-account.json` (voir plus haut).

   > Le `TELEGRAM_BOT_TOKEN` a la forme `<id>:<secret>`, par ex.
   > `8123456789:AAE...`. Un token sans l'`<id>:` en tete est rejete par
   > Telegram avec `InvalidToken`.

2. **Index MongoDB**

   ```bash
   uv run python -m scripts.create_indexes
   ```

   Puis coller les definitions d'index Atlas Search affichees dans l'UI Atlas
   (collection `chunks`), sinon la recherche menu reste vide.

3. **Peupler le menu et les photos** depuis Drive

   ```bash
   uv run python -m scripts.run_sync_once
   ```

   Ce cycle ingere les documents du menu et, si un catalogue `photos` est
   present dans le dossier Drive, remplit la collection `dish_photos`. Verifier
   dans les logs `photo_sync_done: synced=<n>` que les photos sont chargees.

4. **Verifier la suite de tests** (aucune dependance externe)

   ```bash
   uv run python -m pytest tests/ -v -m unit
   ```

5. **Lancer le bot et discuter**

   ```bash
   uv run python -m src.telegram_bot
   ```

   Ouvrir la conversation avec le bot sur Telegram et derouler quelques cas.
   Le fichier [`tests/scenarios_manuels.md`](tests/scenarios_manuels.md) liste
   des scenarios prets a envoyer, message par message, avec le comportement
   attendu.

   Verifications rapides cote photos :

   - « je veux du poisson » → le texte, puis un album des variantes de poisson
   - « c'est quoi votre menu ? » → texte **sans** photo (trop de plats cites)
   - « le fufu sauce arachide » → ne doit **pas** declencher la photo « Eau »

   Pour couper les photos sans toucher au reste : `PHOTOS_ENABLED=false`.

## Docker

```bash
docker compose up -d
```

Trois services : `worker` (sync Drive periodique), `api` (operations),
`bot` (Telegram).
