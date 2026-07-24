"""Configuration typee de l'application, chargee depuis l'environnement."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Parametres applicatifs avec support des variables d'environnement."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MongoDB
    mongodb_uri: str = Field(..., description="Chaine de connexion MongoDB Atlas")
    mongodb_database: str = Field(default="restaurant_delice")
    mongodb_collection_documents: str = Field(default="documents")
    mongodb_collection_chunks: str = Field(default="chunks")
    mongodb_collection_orders: str = Field(default="orders")
    mongodb_collection_conversations: str = Field(default="conversations")
    mongodb_collection_messages: str = Field(default="messages")
    mongodb_collection_counters: str = Field(default="counters")
    mongodb_vector_index: str = Field(default="vector_index")
    mongodb_text_index: str = Field(default="text_index")

    # OpenAI
    openai_api_key: str = Field(..., description="Cle API OpenAI")
    llm_model: str = Field(default="gpt-4.1-mini")
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dimension: int = Field(default=1536)

    # Telegram
    telegram_bot_token: str = Field(..., description="Token du bot Telegram")
    telegram_kitchen_chat_id: str = Field(default="")

    # Google Drive
    google_service_account_file: str = Field(
        default="./credentials/service-account.json"
    )
    google_drive_folder_id: str = Field(..., description="ID du dossier Drive surveille")
    drive_sync_interval_minutes: int = Field(default=15, gt=0)
    drive_sync_max_delete_ratio: float = Field(default=0.5, gt=0.0, le=1.0)

    # Reranking
    cohere_api_key: str = Field(default="")
    rerank_model: str = Field(default="rerank-v3.5")
    rerank_top_n: int = Field(default=10, gt=0)

    # Photos des plats
    photos_enabled: bool = Field(default=True)
    photos_max_dishes: int = Field(
        default=4, gt=0, description="Au-dela, la reponse cite trop de plats"
    )
    photos_max_images: int = Field(
        default=10, gt=0, le=10, description="Limite Telegram par album"
    )
    photos_caption_suffix: str = Field(
        default="", description="Ex. 'Photo d'illustration'"
    )
    mongodb_collection_dish_photos: str = Field(default="dish_photos")
    drive_photos_catalogue_name: str = Field(
        default="photos", description="Nom du fichier catalogue dans Drive, sans extension"
    )

    # Divers
    memory_max_messages: int = Field(default=50, gt=0)
    chunk_max_tokens: int = Field(default=512, gt=0)


def load_settings() -> Settings:
    """
    Charge la configuration applicative.

    Returns:
        L'instance de configuration validee.

    Raises:
        pydantic.ValidationError: Si un champ obligatoire est absent ou invalide.
    """
    return Settings()
