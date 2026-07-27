"""Envoi des photos de plats au client Telegram.

Le cache `telegram_file_id` est le coeur de ce module : une photo n'est
telechargee depuis Drive et uploadee vers Telegram qu'une seule fois dans sa
vie. Les envois suivants ne transportent qu'une chaine de caracteres.

Rien de ce qui se passe ici ne doit remonter au client : le texte de la
reponse est deja parti quand ce module s'execute.
"""

import logging
from types import SimpleNamespace
from typing import Any, Protocol, Sequence

from src.photos.matcher import DishEntry, find_dishes, normalize

logger = logging.getLogger(__name__)


class PhotoSender(Protocol):
    """Canal d'envoi de photos vers une conversation Telegram."""

    async def send_photo(self, chat_id: int, photo: Any, caption: str) -> str:
        """Envoie une photo et rend son file_id Telegram."""
        ...

    async def send_media_group(
        self, chat_id: int, media: Sequence[tuple[Any, str]]
    ) -> list[str]:
        """Envoie un album et rend les file_id, dans l'ordre."""
        ...


def _entries_from_documents(documents: list[dict[str, Any]]) -> list[DishEntry]:
    """
    Deduplique des documents `dish_photos` en entrees pour le matcher.

    Args:
        documents: Documents `dish_photos` actifs.

    Returns:
        Une entree par plat, dedoublonnee par `dish_key`.
    """
    seen: set[str] = set()
    entries: list[DishEntry] = []
    for document in documents:
        key = document.get("dish_key", "")
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(DishEntry(name=document["dish_name"], key=key))
    return entries


async def load_active_entries(deps: Any) -> list[DishEntry]:
    """
    Charge les plats disposant d'au moins une photo active.

    Args:
        deps: Dependances applicatives.

    Returns:
        Les entrees consommables par le matcher, dedoublonnees par plat.
    """
    documents = await deps.dish_photos.find(
        {"enabled": True}, {"dish_name": 1, "dish_key": 1}
    ).to_list(None)
    return _entries_from_documents(documents)


def _caption(dish_name: str, suffix: str) -> str:
    """Construit la legende d'une photo."""
    return f"{dish_name} — {suffix}" if suffix else dish_name


async def _resolve_source(
    document: dict[str, Any], drive_client: Any
) -> tuple[Any, bool]:
    """
    Rend la source a envoyer et indique s'il s'agit d'un premier envoi.

    Args:
        document: Ligne `dish_photos` de la photo.
        drive_client: Client Drive, sollicite seulement si le cache est vide.

    Returns:
        Le file_id Telegram si connu, sinon les octets telecharges depuis
        Drive, et un booleen vrai quand le cache devra etre renseigne.
    """
    cached = document.get("telegram_file_id", "")
    if cached:
        return cached, False

    meta = SimpleNamespace(
        file_id=document["drive_file_id"],
        name=document.get("file_name", ""),
        mime_type="image/jpeg",
    )
    return await drive_client.fetch_bytes(meta), True


async def maybe_send_photos(
    deps: Any,
    chat_id: int,
    text: str,
    sender: PhotoSender,
    drive_client: Any,
) -> list[str]:
    """
    Envoie les photos des plats cites dans une reponse, si le seuil le permet.

    Toute defaillance est logguee et avalee : la conversation ne doit jamais
    etre degradee par le chemin photo.

    Args:
        deps: Dependances applicatives.
        chat_id: Conversation Telegram destinataire.
        text: Reponse que l'agent vient d'envoyer.
        sender: Canal d'envoi de photos.
        drive_client: Client Drive, pour les photos pas encore en cache.

    Returns:
        Les noms des plats effectivement illustres, ou une liste vide.
    """
    if not deps.settings.photos_enabled:
        return []

    try:
        # Une seule lecture Mongo par tour : les entrees du matcher et les
        # documents complets sont derives du meme resultat.
        documents = await deps.dish_photos.find({"enabled": True}).to_list(None)
        if not documents:
            return []

        entries = _entries_from_documents(documents)
        dishes = find_dishes(text, entries, deps.settings.photos_max_dishes)
        if not dishes:
            logger.info("photos_skipped: chat_id=%d plats=0", chat_id)
            return []

        by_key: dict[str, list[dict[str, Any]]] = {}
        for document in documents:
            by_key.setdefault(document.get("dish_key", ""), []).append(document)

        selected: list[dict[str, Any]] = []
        for dish in dishes:
            for document in sorted(
                by_key.get(normalize(dish), []),
                key=lambda d: int(d.get("position", 1)),
            ):
                selected.append(document)

        selected = selected[: deps.settings.photos_max_images]
        if not selected:
            return []

        suffix = deps.settings.photos_caption_suffix
        prepared: list[tuple[Any, str, dict[str, Any], bool]] = []
        for document in selected:
            source, is_upload = await _resolve_source(document, drive_client)
            prepared.append(
                (source, _caption(document["dish_name"], suffix), document, is_upload)
            )

        if len(prepared) == 1:
            source, caption, document, is_upload = prepared[0]
            file_id = await sender.send_photo(chat_id, source, caption)
            returned = [file_id]
        else:
            returned = await sender.send_media_group(
                chat_id, [(source, caption) for source, caption, _, _ in prepared]
            )

        for (_, _, document, is_upload), file_id in zip(prepared, returned):
            if is_upload and file_id:
                await deps.dish_photos.update_one(
                    {
                        "dish_key": document["dish_key"],
                        "drive_file_id": document["drive_file_id"],
                    },
                    {"$set": {"telegram_file_id": file_id}},
                )

        logger.info(
            "photos_sent: chat_id=%d plats=%d images=%d",
            chat_id,
            len(dishes),
            len(prepared),
        )
        return dishes

    except Exception:
        logger.exception("photos_failed: chat_id=%d", chat_id)
        return []
