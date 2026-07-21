"""Modeles de donnees partages par le pipeline d'ingestion et le bot."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ServiceMode = Literal["sur_place", "livraison"]
OrderStatus = Literal["pending", "confirmed", "cancelled"]


class DriveFileMeta(BaseModel):
    """Metadonnees d'un fichier telles que renvoyees par l'API Google Drive."""

    file_id: str = Field(..., description="Identifiant stable du fichier Drive")
    name: str = Field(..., description="Nom affiche du fichier")
    mime_type: str = Field(..., description="Type MIME Drive")
    modified_time: datetime = Field(
        ..., description="Horodatage de derniere modification"
    )
    trashed: bool = Field(default=False, description="Fichier place dans la corbeille")


class DriveTrace(BaseModel):
    """Trace de synchronisation stockee sur chaque document MongoDB."""

    file_id: str
    modified_time: datetime
    content_hash: str = Field(..., description="sha256 hexadecimal du contenu exporte")
    mime_type: str
    last_synced_at: datetime


class DocumentRecord(BaseModel):
    """Document source ingere depuis Google Drive."""

    title: str
    source: str = Field(..., description="URI de la forme gdrive://<file_id>")
    drive: DriveTrace
    chunk_count: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1, description="Increment a chaque re-ingestion")


class ChunkRecord(BaseModel):
    """Fragment de document, porteur de son embedding."""

    document_id: str
    content: str
    embedding: list[float]
    chunk_index: int = Field(..., ge=0)
    version: int = Field(..., ge=1, description="Version du document parent")
    token_count: int = Field(default=0, ge=0)


class SyncDiff(BaseModel):
    """Resultat de la comparaison entre l'etat Drive et l'etat MongoDB."""

    new: list[DriveFileMeta] = Field(default_factory=list)
    modified: list[DriveFileMeta] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list, description="file_id a supprimer")
    unchanged: list[str] = Field(default_factory=list, description="file_id inchanges")


class OrderItem(BaseModel):
    """Ligne d'une commande. Tous les montants sont des entiers en FCFA."""

    name: str = Field(..., min_length=1)
    quantity: int = Field(..., gt=0)
    unit_price: int = Field(..., ge=0)
    total: int = Field(..., ge=0)

    @model_validator(mode="after")
    def check_line_total(self) -> "OrderItem":
        """Verifie que total == quantity * unit_price."""
        expected = self.quantity * self.unit_price
        if self.total != expected:
            raise ValueError(
                f"total de ligne incoherent pour '{self.name}': "
                f"{self.total} au lieu de {expected}"
            )
        return self


class Order(BaseModel):
    """Commande client enregistree en base."""

    order_number: str = Field(..., description="Format LD-YYYYMMDD-NNNN")
    chat_id: int
    customer_name: str = Field(..., min_length=1)
    customer_phone: str = Field(default="")
    service_mode: ServiceMode
    delivery_address: str = Field(default="")
    delivery_instructions: str = Field(default="")
    items: list[OrderItem] = Field(..., min_length=1)
    total_fcfa: int = Field(..., ge=0)
    status: OrderStatus = Field(default="pending")
    created_at: datetime

    @field_validator("customer_phone", "delivery_address", "delivery_instructions")
    @classmethod
    def strip_text(cls, value: str) -> str:
        """Normalise les champs texte optionnels."""
        return value.strip()

    @model_validator(mode="after")
    def check_grand_total(self) -> "Order":
        """Verifie que total_fcfa egale la somme des totaux de ligne."""
        expected = sum(item.total for item in self.items)
        if self.total_fcfa != expected:
            raise ValueError(
                f"total_fcfa incoherent: {self.total_fcfa} au lieu de {expected}"
            )
        return self

    @model_validator(mode="after")
    def check_delivery_fields(self) -> "Order":
        """Impose les coordonnees completes en mode livraison."""
        if self.service_mode != "livraison":
            return self
        if not self.customer_phone:
            raise ValueError("customer_phone est obligatoire en mode livraison")
        if not self.delivery_address:
            raise ValueError("delivery_address est obligatoire en mode livraison")
        return self
