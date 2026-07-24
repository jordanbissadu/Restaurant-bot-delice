"""Tests de la configuration typee."""

import pytest

from src.settings import Settings


@pytest.mark.unit
def test_settings_loads_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les champs obligatoires sont lus depuis l'environnement."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-1")

    settings = Settings(_env_file=None)

    assert settings.mongodb_uri == "mongodb://localhost:27017"
    assert settings.openai_api_key == "sk-test"
    assert settings.telegram_bot_token == "123:ABC"
    assert settings.google_drive_folder_id == "folder-1"


@pytest.mark.unit
def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les valeurs par defaut correspondent au design."""
    monkeypatch.setenv("MONGODB_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("GOOGLE_DRIVE_FOLDER_ID", "folder-1")

    settings = Settings(_env_file=None)

    assert settings.mongodb_database == "restaurant_delice"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimension == 1536
    assert settings.llm_model == "gpt-4.1-mini"
    assert settings.drive_sync_interval_minutes == 15
    assert settings.drive_sync_max_delete_ratio == 0.5
    assert settings.rerank_top_n == 10
    assert settings.photos_enabled is True
    assert settings.photos_max_dishes == 4
    assert settings.photos_max_images == 10
    assert settings.photos_caption_suffix == ""
    assert settings.mongodb_collection_dish_photos == "dish_photos"
    assert settings.drive_photos_catalogue_name == "photos"
    assert settings.memory_max_messages == 50
