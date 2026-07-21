# Restaurant Le Delice - Bot Telegram RAG

Pipeline de donnees : un dossier Google Drive partage est synchronise vers
MongoDB Atlas (Docling -> chunks -> embeddings OpenAI). Le bot conversationnel
fait l'objet du plan 2.

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

## Index MongoDB

```bash
uv run python -m scripts.create_indexes
```

Le script cree les index classiques et affiche la definition JSON des index
Atlas Search (`vector_index`, `text_index`) a coller dans l'UI Atlas, sur la
collection `chunks`. Ces index ne peuvent pas etre crees par pymongo.

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

## Docker

```bash
docker compose up -d
```
