# Photos des plats dans les réponses Telegram — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Envoyer 1 à 4 photos de plats après une réponse du bot portant sur le menu, sans modifier l'agent ni son prompt.

**Architecture :** Post-traitement déterministe. Après `reply_text`, un matcher pur détecte les plats cités dans le texte, un sender développe ces plats en photos et les envoie via Telegram, avec un cache `file_id` qui garantit qu'une image n'est téléchargée et uploadée qu'une seule fois. Le catalogue vient d'un CSV Drive synchronisé vers MongoDB.

**Tech Stack :** Python 3.11+, pydantic v2, pymongo (async), python-telegram-bot, google-api-python-client, pytest (asyncio_mode=auto).

## Global Constraints

- Les docstrings et commentaires sont en français, sans accents dans le code source (convention du dépôt existant : voir `src/tools/notify_kitchen.py`).
- Tout test unitaire porte `@pytest.mark.unit`. Marqueurs disponibles : `unit`, `integration`, `e2e`.
- `asyncio_mode = "auto"` : les tests async n'ont pas besoin de décorateur.
- Aucune dépendance nouvelle dans `pyproject.toml`. Tout est faisable avec la stdlib et l'existant.
- La photo ne doit jamais dégrader la conversation : toute défaillance du chemin photo est logguée et avalée, jamais remontée au client.
- L'agent (`src/agent.py`), le `SYSTEM_PROMPT` (`src/prompts.py`) et les trois outils existants ne sont pas modifiés.
- Commande de test : `uv run pytest <chemin> -v`.

## Écart assumé par rapport à la spec

La spec prévoit d'invalider le cache Telegram sur changement de `content_hash`. Calculer ce hash exige de télécharger l'image, ce qui contredit le téléchargement paresseux. Le plan invalide donc sur `drive_modified_time`, disponible gratuitement dans le listing Drive, et renseigne `content_hash` au premier téléchargement réel. Le comportement observable est identique ; le coût réseau au sync est nul.

## Structure des fichiers

| Fichier | Responsabilité |
|---|---|
| `src/photos/__init__.py` | Package vide |
| `src/photos/matcher.py` | Détecte les plats cités dans un texte. **Aucune dépendance.** |
| `src/photos/catalogue.py` | Parse le CSV du catalogue. **Aucune dépendance.** |
| `src/photos/sync.py` | Route les fichiers Drive par MIME, écrit `dish_photos` |
| `src/photos/sender.py` | Développe les plats en photos, gère le cache, envoie |
| `src/models.py` | + modèle `DishPhoto` |
| `src/settings.py` | + 6 réglages photo |
| `src/dependencies.py` | + accesseur `dish_photos` |
| `src/drive/client.py` | + `fetch_bytes` et `export_csv` |
| `src/drive/sync.py` | Ne traite plus que les documents, délègue le reste |
| `src/telegram_bot.py` | + adaptateur d'envoi photo, + appel après `reply_text` |

---

### Task 1 : Fondations — modèle, configuration, accesseur

**Files:**
- Modify: `src/models.py` (ajout en fin de fichier)
- Modify: `src/settings.py:52-54` (bloc `# Divers`)
- Modify: `src/dependencies.py:86-89` (après la propriété `orders`)
- Test: `tests/test_models.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: rien
- Produces: `DishPhoto` (pydantic), `Settings.photos_enabled: bool`, `Settings.photos_max_dishes: int`, `Settings.photos_max_images: int`, `Settings.photos_caption_suffix: str`, `Settings.mongodb_collection_dish_photos: str`, `Settings.drive_photos_catalogue_name: str`, `AppDependencies.dish_photos` (collection)

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à la fin de `tests/test_models.py` :

```python
@pytest.mark.unit
def test_dish_photo_defaults() -> None:
    """Une photo est active et sans cache Telegram par defaut."""
    photo = DishPhoto(
        dish_name="Poulet Yassa",
        dish_key="poulet yassa",
        drive_file_id="drive-1",
        file_name="poulet-yassa.jpg",
        drive_modified_time=datetime(2026, 7, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
    )

    assert photo.enabled is True
    assert photo.telegram_file_id == ""
    assert photo.content_hash == ""
    assert photo.position == 1


@pytest.mark.unit
def test_dish_photo_rejects_position_zero() -> None:
    """La position commence a 1."""
    with pytest.raises(ValidationError):
        DishPhoto(
            dish_name="Poulet Yassa",
            dish_key="poulet yassa",
            drive_file_id="drive-1",
            file_name="poulet-yassa.jpg",
            position=0,
            drive_modified_time=datetime(2026, 7, 23, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )
```

Compléter les imports en tête de `tests/test_models.py` : `from datetime import datetime, timezone`, `from pydantic import ValidationError`, et ajouter `DishPhoto` à l'import depuis `src.models`.

Ajouter à `tests/test_settings.py`, dans `test_settings_defaults`, avant la dernière ligne :

```python
    assert settings.photos_enabled is True
    assert settings.photos_max_dishes == 4
    assert settings.photos_max_images == 10
    assert settings.photos_caption_suffix == ""
    assert settings.mongodb_collection_dish_photos == "dish_photos"
    assert settings.drive_photos_catalogue_name == "photos"
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/test_models.py tests/test_settings.py -v`
Expected: FAIL — `ImportError: cannot import name 'DishPhoto'` et `AttributeError: 'Settings' object has no attribute 'photos_enabled'`

- [ ] **Step 3: Ajouter le modèle**

À la fin de `src/models.py` :

```python
class DishPhoto(BaseModel):
    """Photo d'un plat, synchronisee depuis Drive et mise en cache Telegram."""

    dish_name: str = Field(..., min_length=1, description="Nom exact tel qu'au menu")
    dish_key: str = Field(..., min_length=1, description="Nom normalise, indexe")
    drive_file_id: str = Field(..., min_length=1)
    file_name: str = Field(..., min_length=1)
    drive_modified_time: datetime = Field(
        ..., description="Horodatage Drive; son changement invalide le cache"
    )
    content_hash: str = Field(
        default="", description="sha256, renseigne au premier telechargement"
    )
    telegram_file_id: str = Field(
        default="", description="Cache Telegram; vide tant que jamais envoyee"
    )
    position: int = Field(default=1, ge=1, description="Ordre d'affichage du plat")
    enabled: bool = Field(default=True)
    updated_at: datetime
```

- [ ] **Step 4: Ajouter les réglages**

Dans `src/settings.py`, remplacer le bloc `# Divers` par :

```python
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
```

- [ ] **Step 5: Ajouter l'accesseur de collection**

À la fin de `src/dependencies.py`, après la propriété `orders` :

```python
    @property
    def dish_photos(self):
        """Collection des photos de plats."""
        return self.db[self.settings.mongodb_collection_dish_photos]
```

- [ ] **Step 6: Lancer les tests**

Run: `uv run pytest tests/test_models.py tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/settings.py src/dependencies.py tests/test_models.py tests/test_settings.py
git commit -m "feat: modele DishPhoto, reglages photo et collection dish_photos"
```

---

### Task 2 : Le matcher

C'est le cœur de la fonctionnalité et la seule logique non triviale. Fonction pure : aucun accès réseau, base ou fichier.

**Files:**
- Create: `src/photos/__init__.py`
- Create: `src/photos/matcher.py`
- Test: `tests/photos/__init__.py`, `tests/photos/test_matcher.py`

**Interfaces:**
- Consumes: rien
- Produces: `normalize(text: str) -> str`, `DishEntry(name: str, key: str)` (dataclass frozen), `find_dishes(text: str, entries: Sequence[DishEntry], max_dishes: int = 4) -> list[str]` qui rend les **noms** des plats dans leur ordre d'apparition, ou une liste vide

**Pourquoi des lookarounds et pas `\b` :** plusieurs noms se terminent par une parenthèse — `Kékélen (Boules de Haricot)`. Entre `)` et la fin de chaîne il n'y a pas de frontière `\b`, donc `\b` ferait échouer le match. `(?![0-9a-z])` fonctionne quel que soit le caractère de bord.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/photos/__init__.py` vide, puis `tests/photos/test_matcher.py` :

```python
"""Tests du matcher de plats. Aucune dependance externe."""

import pytest

from src.photos.matcher import DishEntry, find_dishes, normalize

CATALOGUE = [
    DishEntry(name="Poisson Braisé", key=normalize("Poisson Braisé")),
    DishEntry(
        name="Poisson Braisé Façon Togolaise",
        key=normalize("Poisson Braisé Façon Togolaise"),
    ),
    DishEntry(name="Poulet Yassa", key=normalize("Poulet Yassa")),
    DishEntry(name="Thiéboudienne", key=normalize("Thiéboudienne")),
    DishEntry(name="Eau", key=normalize("Eau")),
    DishEntry(name="Tchakpalo", key=normalize("Tchakpalo")),
    DishEntry(name="Tchakpalo + Akpan", key=normalize("Tchakpalo + Akpan")),
    DishEntry(
        name="Fufu + Sauce Arachide + Agneau",
        key=normalize("Fufu + Sauce Arachide + Agneau"),
    ),
    DishEntry(
        name="Kékélen (Boules de Haricot)", key=normalize("Kékélen (Boules de Haricot)")
    ),
]


@pytest.mark.unit
def test_normalize_strips_accents_and_case() -> None:
    """La normalisation retire accents et casse."""
    assert normalize("Poisson Braisé Façon Togolaise") == "poisson braise facon togolaise"
    assert normalize("Steak de Bœuf Grillé") == "steak de boeuf grille"


@pytest.mark.unit
def test_agneau_does_not_trigger_eau() -> None:
    """'agneau' contient 'eau' mais ne doit pas declencher la photo Eau."""
    text = "Nous avons le Fufu + Sauce Arachide + Agneau à 8 000 FCFA."

    assert find_dishes(text, CATALOGUE) == ["Fufu + Sauce Arachide + Agneau"]


@pytest.mark.unit
def test_longest_name_wins_over_substring() -> None:
    """Le nom le plus long consomme le texte : une seule photo, pas deux."""
    text = "Le Poisson Braisé Façon Togolaise est à 10 000 FCFA."

    assert find_dishes(text, CATALOGUE) == ["Poisson Braisé Façon Togolaise"]


@pytest.mark.unit
def test_both_poisson_variants_are_found_when_both_cited() -> None:
    """Les deux versions citees rendent bien deux plats."""
    text = (
        "Poisson Braisé Façon Togolaise – 10 000 FCFA. "
        "Poisson Braisé – 11 000 FCFA."
    )

    assert find_dishes(text, CATALOGUE) == [
        "Poisson Braisé Façon Togolaise",
        "Poisson Braisé",
    ]


@pytest.mark.unit
def test_tchakpalo_akpan_does_not_trigger_tchakpalo() -> None:
    """La meme regle vaut pour Tchakpalo."""
    text = "Le Tchakpalo + Akpan est disponible."

    assert find_dishes(text, CATALOGUE) == ["Tchakpalo + Akpan"]


@pytest.mark.unit
def test_name_ending_with_parenthesis_is_found() -> None:
    """Un nom finissant par une parenthese est detecte."""
    text = "Nous proposons le Kékélen (Boules de Haricot) en entrée."

    assert find_dishes(text, CATALOGUE) == ["Kékélen (Boules de Haricot)"]


@pytest.mark.unit
def test_case_and_accents_are_ignored() -> None:
    """Majuscules et accents absents ne bloquent pas la detection."""
    assert find_dishes("POULET YASSA", CATALOGUE) == ["Poulet Yassa"]
    assert find_dishes("thieboudienne", CATALOGUE) == ["Thiéboudienne"]


@pytest.mark.unit
def test_dishes_are_returned_in_text_order() -> None:
    """L'ordre suit le texte, pas l'ordre du catalogue."""
    text = "Nous avons Thiéboudienne, puis Poulet Yassa, puis Tchakpalo."

    assert find_dishes(text, CATALOGUE) == [
        "Thiéboudienne",
        "Poulet Yassa",
        "Tchakpalo",
    ]


@pytest.mark.unit
def test_no_dish_returns_empty() -> None:
    """Un texte sans plat ne declenche rien."""
    assert find_dishes("Bonjour, comment allez-vous ?", CATALOGUE) == []


@pytest.mark.unit
def test_above_threshold_returns_empty() -> None:
    """Au-dela du seuil, on considere que le bot deroule la carte."""
    text = (
        "Poulet Yassa, Thiéboudienne, Poisson Braisé, Tchakpalo, "
        "Kékélen (Boules de Haricot)"
    )

    assert find_dishes(text, CATALOGUE, max_dishes=4) == []


@pytest.mark.unit
def test_exactly_at_threshold_is_kept() -> None:
    """Le seuil est inclusif."""
    text = "Poulet Yassa, Thiéboudienne, Poisson Braisé, Tchakpalo."

    assert len(find_dishes(text, CATALOGUE, max_dishes=4)) == 4


@pytest.mark.unit
def test_same_dish_cited_twice_is_returned_once() -> None:
    """Une repetition ne double pas la photo."""
    text = "Le Poulet Yassa est excellent. Je confirme le Poulet Yassa."

    assert find_dishes(text, CATALOGUE) == ["Poulet Yassa"]
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/photos/test_matcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.photos'`

- [ ] **Step 3: Écrire le matcher**

Créer `src/photos/__init__.py` vide, puis `src/photos/matcher.py` :

```python
"""Detection des plats cites dans une reponse du bot.

Fonction pure : c'est ici qu'est toute la logique de la fonctionnalite photo,
et elle se teste sans base, sans reseau et sans modele.

Deux regles gouvernent la recherche :

1. Frontieres de mots par lookarounds. 'Eau' ne doit pas matcher dans
   'Agneau'. Les lookarounds sont preferes a `\\b` car plusieurs noms de plats
   se terminent par une parenthese, ou `\\b` ne s'applique pas.
2. Du plus long au plus court, avec consommation. 'Poisson Braise' ne doit pas
   matcher a l'interieur de 'Poisson Braise Facon Togolaise' deja trouve.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

DEFAULT_MAX_DISHES = 4


@dataclass(frozen=True)
class DishEntry:
    """Un plat du catalogue, tel que le matcher le consomme."""

    name: str
    key: str


def normalize(text: str) -> str:
    """
    Normalise un texte pour la comparaison : minuscules, sans accents.

    Args:
        text: Texte brut.

    Returns:
        Le texte en minuscules, sans diacritiques, espaces reduits.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    stripped = stripped.replace("œ", "oe").replace("Œ", "oe")
    stripped = stripped.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def find_dishes(
    text: str,
    entries: Sequence[DishEntry],
    max_dishes: int = DEFAULT_MAX_DISHES,
) -> list[str]:
    """
    Detecte les plats du catalogue cites dans un texte.

    Args:
        text: Reponse produite par l'agent.
        entries: Catalogue des plats disposant d'une photo active.
        max_dishes: Au-dela de ce nombre de plats cites, on considere que le
            bot deroule la carte et on n'envoie aucune photo.

    Returns:
        Les noms des plats, dans leur ordre d'apparition dans le texte. Liste
        vide si aucun plat n'est cite ou si le seuil est depasse.
    """
    haystack = list(normalize(text))
    found: list[tuple[int, str]] = []
    seen: set[str] = set()

    for entry in sorted(entries, key=lambda e: len(e.key), reverse=True):
        if not entry.key or entry.name in seen:
            continue

        pattern = re.compile(
            r"(?<![0-9a-z])" + re.escape(entry.key) + r"(?![0-9a-z])"
        )
        match = pattern.search("".join(haystack))
        if match is None:
            continue

        found.append((match.start(), entry.name))
        seen.add(entry.name)

        # Blanchir toutes les occurrences pour qu'un nom plus court ne puisse
        # plus matcher a l'interieur d'un nom deja consomme.
        for occurrence in pattern.finditer("".join(haystack)):
            for index in range(occurrence.start(), occurrence.end()):
                haystack[index] = " "

    if not found or len(found) > max_dishes:
        return []

    found.sort(key=lambda pair: pair[0])
    return [name for _, name in found]
```

- [ ] **Step 4: Lancer les tests**

Run: `uv run pytest tests/photos/test_matcher.py -v`
Expected: PASS — 12 tests

- [ ] **Step 5: Commit**

```bash
git add src/photos/__init__.py src/photos/matcher.py tests/photos/
git commit -m "feat: matcher de plats avec frontieres de mots et plus-long-d-abord"
```

---

### Task 3 : Le parseur de catalogue

**Files:**
- Create: `src/photos/catalogue.py`
- Test: `tests/photos/test_catalogue.py`

**Interfaces:**
- Consumes: rien
- Produces: `CatalogueRow(dish_name: str, file_name: str, position: int, enabled: bool)` (dataclass frozen), `parse_catalogue(csv_text: str) -> list[CatalogueRow]`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/photos/test_catalogue.py` :

```python
"""Tests du parseur de catalogue photos."""

import pytest

from src.photos.catalogue import CatalogueRow, parse_catalogue

CSV = """plat,fichier,ordre,actif
Poulet Yassa,poulet-yassa.jpg,1,oui
Poulet Yassa,poulet-yassa-2.jpg,2,oui
Eau,eau.jpg,1,non
"Jus locaux (bissap, gingembre)",jus-locaux.jpg,1,oui
"""


@pytest.mark.unit
def test_parses_rows() -> None:
    """Les quatre lignes sont lues avec leurs colonnes."""
    rows = parse_catalogue(CSV)

    assert len(rows) == 4
    assert rows[0] == CatalogueRow(
        dish_name="Poulet Yassa", file_name="poulet-yassa.jpg", position=1, enabled=True
    )


@pytest.mark.unit
def test_actif_non_disables_row() -> None:
    """La colonne actif pilote l'activation."""
    rows = parse_catalogue(CSV)
    eau = [r for r in rows if r.dish_name == "Eau"][0]

    assert eau.enabled is False


@pytest.mark.unit
def test_quoted_name_with_comma_is_preserved() -> None:
    """Un nom contenant une virgule reste entier."""
    rows = parse_catalogue(CSV)

    assert rows[3].dish_name == "Jus locaux (bissap, gingembre)"


@pytest.mark.unit
def test_bom_is_tolerated() -> None:
    """Un CSV exporte avec BOM reste lisible."""
    rows = parse_catalogue("﻿" + CSV)

    assert rows[0].dish_name == "Poulet Yassa"


@pytest.mark.unit
def test_windows_path_quotes_are_stripped() -> None:
    """Les guillemets litteraux du 'Copier en tant que chemin' sont retires."""
    rows = parse_catalogue('plat,fichier,ordre,actif\nPoulet Yassa,"""poulet.jpg""",1,oui\n')

    assert rows[0].file_name == "poulet.jpg"


@pytest.mark.unit
def test_incomplete_rows_are_skipped() -> None:
    """Une ligne sans plat ou sans fichier est ignoree, pas fatale."""
    rows = parse_catalogue(
        "plat,fichier,ordre,actif\n,orphelin.jpg,1,oui\nPoulet Yassa,,1,oui\n"
    )

    assert rows == []


@pytest.mark.unit
def test_missing_position_defaults_to_one() -> None:
    """Un ordre absent ou illisible vaut 1."""
    rows = parse_catalogue("plat,fichier,ordre,actif\nPoulet Yassa,p.jpg,,oui\n")

    assert rows[0].position == 1


@pytest.mark.unit
def test_empty_csv_returns_empty_list() -> None:
    """Un catalogue vide ne fait pas echouer le parseur."""
    assert parse_catalogue("") == []
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/photos/test_catalogue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.photos.catalogue'`

- [ ] **Step 3: Écrire le parseur**

Créer `src/photos/catalogue.py` :

```python
"""Lecture du catalogue photos, un CSV maintenu par le restaurant dans Drive.

Le parseur est tolerant : une ligne incomplete est ignoree plutot que fatale,
pour qu'une faute de saisie du restaurant ne prive pas le bot de tout son
catalogue.
"""

import csv
import io
import logging

from dataclasses import dataclass

logger = logging.getLogger(__name__)

TRUE_VALUES = {"oui", "yes", "true", "1", "o", "y"}


@dataclass(frozen=True)
class CatalogueRow:
    """Une ligne du catalogue : un plat, une photo."""

    dish_name: str
    file_name: str
    position: int
    enabled: bool


def _clean(value: str | None) -> str:
    """Retire espaces et guillemets litteraux autour d'une valeur."""
    return (value or "").strip().strip('"').strip()


def parse_catalogue(csv_text: str) -> list[CatalogueRow]:
    """
    Parse le contenu CSV du catalogue photos.

    Colonnes attendues : `plat`, `fichier`, `ordre`, `actif`.

    Args:
        csv_text: Contenu textuel du CSV, BOM tolere.

    Returns:
        Les lignes exploitables. Les lignes sans plat ou sans fichier sont
        ignorees et loggees.
    """
    text = csv_text.lstrip("﻿")
    if not text.strip():
        return []

    rows: list[CatalogueRow] = []
    for record in csv.DictReader(io.StringIO(text)):
        dish_name = _clean(record.get("plat"))
        file_name = _clean(record.get("fichier"))
        if not dish_name or not file_name:
            logger.warning(
                "catalogue_row_skipped: plat=%r fichier=%r", dish_name, file_name
            )
            continue

        try:
            position = int(_clean(record.get("ordre")) or 1)
        except ValueError:
            position = 1

        enabled = _clean(record.get("actif")).lower() in TRUE_VALUES

        rows.append(
            CatalogueRow(
                dish_name=dish_name,
                file_name=file_name,
                position=max(position, 1),
                enabled=enabled,
            )
        )

    logger.info("catalogue_parsed: rows=%d", len(rows))
    return rows
```

- [ ] **Step 4: Lancer les tests**

Run: `uv run pytest tests/photos/test_catalogue.py -v`
Expected: PASS — 8 tests

- [ ] **Step 5: Commit**

```bash
git add src/photos/catalogue.py tests/photos/test_catalogue.py
git commit -m "feat: parseur du catalogue photos"
```

---

### Task 4 : Routage MIME au sync (correctif de l'anomalie existante)

Aujourd'hui `run_sync` ingère **tout** ce que Drive remonte. Déposer des images dans le dossier surveillé les enverrait au découpage et à la vectorisation. Cette tâche corrige ce défaut, indépendamment du reste.

**Files:**
- Create: `src/photos/sync.py`
- Modify: `src/drive/sync.py:56-73`
- Test: `tests/photos/test_sync_routing.py`

**Interfaces:**
- Consumes: `DriveFileMeta` (`src/models.py`)
- Produces: `DriveRouting(documents: list[DriveFileMeta], images: list[DriveFileMeta], catalogue: DriveFileMeta | None)` (dataclass frozen), `route_drive_files(files: Sequence[DriveFileMeta], catalogue_name: str) -> DriveRouting`

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/photos/test_sync_routing.py` :

```python
"""Tests du routage des fichiers Drive par type MIME."""

from datetime import datetime, timezone

import pytest

from src.models import DriveFileMeta
from src.photos.sync import route_drive_files

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _meta(name: str, mime: str, file_id: str = "id") -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id, name=name, mime_type=mime, modified_time=NOW
    )


@pytest.mark.unit
def test_images_are_not_routed_to_documents() -> None:
    """Une image ne doit jamais partir en vectorisation."""
    files = [
        _meta("menu.gdoc", "application/vnd.google-apps.document", "doc-1"),
        _meta("poulet-yassa.jpg", "image/jpeg", "img-1"),
        _meta("poisson.png", "image/png", "img-2"),
    ]

    routing = route_drive_files(files, catalogue_name="photos")

    assert [f.file_id for f in routing.documents] == ["doc-1"]
    assert [f.file_id for f in routing.images] == ["img-1", "img-2"]


@pytest.mark.unit
def test_catalogue_sheet_is_isolated() -> None:
    """Le Sheet du catalogue n'est ni un document ni une image."""
    files = [
        _meta("menu.gdoc", "application/vnd.google-apps.document", "doc-1"),
        _meta("photos", "application/vnd.google-apps.spreadsheet", "cat-1"),
    ]

    routing = route_drive_files(files, catalogue_name="photos")

    assert routing.catalogue is not None
    assert routing.catalogue.file_id == "cat-1"
    assert [f.file_id for f in routing.documents] == ["doc-1"]


@pytest.mark.unit
def test_catalogue_as_plain_csv_is_recognized() -> None:
    """Le catalogue peut aussi etre un simple fichier .csv."""
    files = [_meta("photos.csv", "text/csv", "cat-1")]

    routing = route_drive_files(files, catalogue_name="photos")

    assert routing.catalogue is not None
    assert routing.catalogue.file_id == "cat-1"
    assert routing.documents == []


@pytest.mark.unit
def test_other_spreadsheet_stays_a_document() -> None:
    """Un tableur qui n'est pas le catalogue reste un document du menu."""
    files = [_meta("tarifs", "application/vnd.google-apps.spreadsheet", "sheet-1")]

    routing = route_drive_files(files, catalogue_name="photos")

    assert routing.catalogue is None
    assert [f.file_id for f in routing.documents] == ["sheet-1"]


@pytest.mark.unit
def test_no_files_gives_empty_routing() -> None:
    """Un dossier vide ne fait pas echouer le routage."""
    routing = route_drive_files([], catalogue_name="photos")

    assert routing.documents == []
    assert routing.images == []
    assert routing.catalogue is None
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/photos/test_sync_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.photos.sync'`

- [ ] **Step 3: Écrire le routage**

Créer `src/photos/sync.py` :

```python
"""Routage des fichiers Drive et synchronisation du catalogue photos.

Sans ce routage, `run_sync` enverrait les images au decoupage et a la
vectorisation : des chunks binaires dans la base du menu et une facture
d'embeddings pour rien.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from src.models import DriveFileMeta

logger = logging.getLogger(__name__)

IMAGE_MIME_PREFIX = "image/"
CSV_MIMES = {"text/csv", "application/csv"}
SHEET_MIME = "application/vnd.google-apps.spreadsheet"


@dataclass(frozen=True)
class DriveRouting:
    """Fichiers Drive repartis par destination."""

    documents: list[DriveFileMeta] = field(default_factory=list)
    images: list[DriveFileMeta] = field(default_factory=list)
    catalogue: DriveFileMeta | None = None


def route_drive_files(
    files: Sequence[DriveFileMeta], catalogue_name: str
) -> DriveRouting:
    """
    Repartit les fichiers Drive entre documents, images et catalogue.

    Args:
        files: Fichiers remontes par `DriveClient.list_folder_files`.
        catalogue_name: Nom du catalogue photos, sans extension.

    Returns:
        Le routage. Le catalogue vaut None s'il est absent du dossier.
    """
    documents: list[DriveFileMeta] = []
    images: list[DriveFileMeta] = []
    catalogue: DriveFileMeta | None = None

    wanted = catalogue_name.strip().lower()

    for meta in files:
        stem = Path(meta.name).stem.strip().lower()
        is_catalogue_candidate = meta.mime_type in CSV_MIMES or meta.mime_type == SHEET_MIME

        if catalogue is None and is_catalogue_candidate and stem == wanted:
            catalogue = meta
            continue

        if meta.mime_type.startswith(IMAGE_MIME_PREFIX):
            images.append(meta)
            continue

        documents.append(meta)

    logger.info(
        "drive_routed: documents=%d images=%d catalogue=%s",
        len(documents),
        len(images),
        catalogue.name if catalogue else "absent",
    )
    return DriveRouting(documents=documents, images=images, catalogue=catalogue)
```

- [ ] **Step 4: Brancher le routage dans le sync existant**

Dans `src/drive/sync.py`, ajouter l'import en tête :

```python
from src.photos.sync import route_drive_files
```

Puis remplacer les lignes 56 à 59 (de `remote = await client.list_folder_files(...)` jusqu'à la fin du `local_docs = ...`) par :

```python
    all_remote = await client.list_folder_files(deps.settings.google_drive_folder_id)
    routing = route_drive_files(
        all_remote, deps.settings.drive_photos_catalogue_name
    )
    remote = routing.documents
    local_docs = await deps.documents.find(
        {}, {"drive.file_id": 1, "drive.modified_time": 1, "drive.content_hash": 1}
    ).to_list(None)
```

Le reste de `run_sync` est inchangé : il travaille désormais sur `remote`, qui ne contient plus que des documents.

- [ ] **Step 5: Lancer les tests**

Run: `uv run pytest tests/photos/ tests/drive/ -v`
Expected: PASS — les tests de routage passent, ceux de `tests/drive/` restent verts

- [ ] **Step 6: Commit**

```bash
git add src/photos/sync.py src/drive/sync.py tests/photos/test_sync_routing.py
git commit -m "fix: les images Drive ne partent plus en vectorisation"
```

---

### Task 5 : Synchronisation du catalogue vers MongoDB

**Files:**
- Modify: `src/drive/client.py` (ajout de deux méthodes)
- Modify: `src/photos/sync.py` (ajout de `sync_photo_catalogue`)
- Test: `tests/photos/test_sync_catalogue.py`

**Interfaces:**
- Consumes: `route_drive_files`, `DriveRouting` (Task 4), `parse_catalogue`, `CatalogueRow` (Task 3), `normalize` (Task 2), `DishPhoto` (Task 1)
- Produces: `DriveClient.fetch_bytes(meta: DriveFileMeta) -> bytes`, `DriveClient.export_csv(meta: DriveFileMeta) -> str`, `PhotoSyncReport(synced: int, missing_files: list[str], removed: int)`, `sync_photo_catalogue(deps, client, routing) -> PhotoSyncReport`

**Règle d'invalidation du cache :** si `drive_modified_time` change pour un `drive_file_id` donné, `telegram_file_id` est remis à `""`. Le reste de la ligne est mis à jour sans toucher au cache.

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/photos/test_sync_catalogue.py` :

```python
"""Tests de la synchronisation du catalogue photos vers MongoDB."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.models import DriveFileMeta
from src.photos.sync import DriveRouting, sync_photo_catalogue

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)

CSV = """plat,fichier,ordre,actif
Poulet Yassa,poulet-yassa.jpg,1,oui
Eau,eau.jpg,1,non
Plat Fantome,absente.jpg,1,oui
"""


class _FakeCollection:
    """Doublure minimale de collection MongoDB async."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ) -> None:
        existing = await self.find_one(query)
        if existing is None:
            if not upsert:
                return
            existing = dict(query)
            self.docs.append(existing)
        existing.update(update.get("$set", {}))

    async def delete_many(self, query: dict[str, Any]) -> None:
        keep = []
        for doc in self.docs:
            nin = query.get("drive_file_id", {}).get("$nin")
            if nin is not None and doc.get("drive_file_id") in nin:
                keep.append(doc)
        self.docs = keep


class _FakeClient:
    def __init__(self, csv_text: str) -> None:
        self.csv_text = csv_text
        self.exported: list[str] = []

    async def export_csv(self, meta: DriveFileMeta) -> str:
        self.exported.append(meta.file_id)
        return self.csv_text


def _meta(name: str, file_id: str, when: datetime = NOW) -> DriveFileMeta:
    return DriveFileMeta(
        file_id=file_id, name=name, mime_type="image/jpeg", modified_time=when
    )


def _deps(collection: _FakeCollection) -> Any:
    return SimpleNamespace(
        dish_photos=collection,
        settings=SimpleNamespace(drive_photos_catalogue_name="photos"),
    )


def _routing(images: list[DriveFileMeta]) -> DriveRouting:
    catalogue = DriveFileMeta(
        file_id="cat-1", name="photos", mime_type="text/csv", modified_time=NOW
    )
    return DriveRouting(documents=[], images=images, catalogue=catalogue)


@pytest.mark.unit
async def test_catalogue_rows_are_written() -> None:
    """Chaque ligne exploitable devient un document dish_photos."""
    collection = _FakeCollection()
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    report = await sync_photo_catalogue(
        _deps(collection), _FakeClient(CSV), _routing(images)
    )

    assert report.synced == 2
    yassa = await collection.find_one({"drive_file_id": "img-1"})
    assert yassa["dish_name"] == "Poulet Yassa"
    assert yassa["dish_key"] == "poulet yassa"
    assert yassa["enabled"] is True


@pytest.mark.unit
async def test_disabled_row_is_written_but_inactive() -> None:
    """Une ligne actif=non est enregistree desactivee."""
    collection = _FakeCollection()
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), _routing(images))

    eau = await collection.find_one({"drive_file_id": "img-2"})
    assert eau["enabled"] is False


@pytest.mark.unit
async def test_missing_image_is_reported_not_fatal() -> None:
    """Une ligne pointant vers un fichier absent est signalee, pas fatale."""
    collection = _FakeCollection()
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    report = await sync_photo_catalogue(
        _deps(collection), _FakeClient(CSV), _routing(images)
    )

    assert report.missing_files == ["absente.jpg"]
    assert report.synced == 2


@pytest.mark.unit
async def test_unchanged_photo_keeps_its_telegram_cache() -> None:
    """Un sync sans changement ne doit pas invalider le cache."""
    collection = _FakeCollection()
    collection.docs.append(
        {
            "dish_key": "poulet yassa",
            "drive_file_id": "img-1",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-abc",
        }
    )
    images = [_meta("poulet-yassa.jpg", "img-1"), _meta("eau.jpg", "img-2")]

    await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), _routing(images))

    yassa = await collection.find_one({"drive_file_id": "img-1"})
    assert yassa["telegram_file_id"] == "cache-abc"


@pytest.mark.unit
async def test_modified_photo_clears_its_telegram_cache() -> None:
    """Une image remplacee dans Drive invalide son cache Telegram."""
    collection = _FakeCollection()
    collection.docs.append(
        {
            "dish_key": "poulet yassa",
            "drive_file_id": "img-1",
            "drive_modified_time": NOW,
            "telegram_file_id": "cache-abc",
        }
    )
    later = NOW + timedelta(hours=1)
    images = [_meta("poulet-yassa.jpg", "img-1", later), _meta("eau.jpg", "img-2")]

    await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), _routing(images))

    yassa = await collection.find_one({"drive_file_id": "img-1"})
    assert yassa["telegram_file_id"] == ""


@pytest.mark.unit
async def test_absent_catalogue_is_not_fatal() -> None:
    """Sans catalogue dans Drive, le sync photo ne fait rien."""
    collection = _FakeCollection()
    routing = DriveRouting(documents=[], images=[], catalogue=None)

    report = await sync_photo_catalogue(_deps(collection), _FakeClient(CSV), routing)

    assert report.synced == 0
    assert collection.docs == []
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/photos/test_sync_catalogue.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync_photo_catalogue'`

- [ ] **Step 3: Ajouter les deux méthodes au client Drive**

Dans `src/drive/client.py`, ajouter après la méthode `download` :

```python
    async def fetch_bytes(self, meta: DriveFileMeta) -> bytes:
        """
        Recupere le contenu binaire d'un fichier sans l'ecrire sur disque.

        Args:
            meta: Metadonnees du fichier.

        Returns:
            Le contenu brut.

        Raises:
            googleapiclient.errors.HttpError: Si l'API Drive renvoie une erreur.
        """
        return await asyncio.to_thread(self._fetch_bytes, meta)

    async def export_csv(self, meta: DriveFileMeta) -> str:
        """
        Recupere un catalogue au format CSV, Google Sheet ou fichier brut.

        Args:
            meta: Metadonnees du catalogue.

        Returns:
            Le contenu textuel, decode en UTF-8.

        Raises:
            googleapiclient.errors.HttpError: Si l'API Drive renvoie une erreur.
        """
        data = await asyncio.to_thread(self._fetch_csv_bytes, meta)
        return data.decode("utf-8-sig")

    def _fetch_csv_bytes(self, meta: DriveFileMeta) -> bytes:
        """Exporte un Sheet en CSV, ou telecharge un .csv tel quel."""
        if meta.mime_type == "application/vnd.google-apps.spreadsheet":
            return (
                self.service.files()
                .export_media(fileId=meta.file_id, mimeType="text/csv")
                .execute()
            )
        return (
            self.service.files()
            .get_media(fileId=meta.file_id, supportsAllDrives=True)
            .execute()
        )
```

- [ ] **Step 4: Écrire la synchronisation**

Ajouter à la fin de `src/photos/sync.py` :

```python
@dataclass
class PhotoSyncReport:
    """Bilan d'un cycle de synchronisation du catalogue photos."""

    synced: int = 0
    missing_files: list[str] = field(default_factory=list)
    removed: int = 0


async def sync_photo_catalogue(
    deps: Any, client: Any, routing: DriveRouting
) -> PhotoSyncReport:
    """
    Met a jour la collection `dish_photos` a partir du catalogue Drive.

    Le cache Telegram d'une photo est invalide si et seulement si son
    `modifiedTime` Drive a change : l'image a ete remplacee.

    Args:
        deps: Dependances applicatives.
        client: Client Drive authentifie.
        routing: Fichiers Drive deja repartis par `route_drive_files`.

    Returns:
        Le bilan du cycle.
    """
    report = PhotoSyncReport()
    if routing.catalogue is None:
        logger.warning("photo_catalogue_absent")
        return report

    csv_text = await client.export_csv(routing.catalogue)
    rows = parse_catalogue(csv_text)

    images_by_name = {meta.name.strip().lower(): meta for meta in routing.images}
    now = datetime.now(timezone.utc)
    kept_file_ids: list[str] = []

    for row in rows:
        image = images_by_name.get(row.file_name.strip().lower())
        if image is None:
            report.missing_files.append(row.file_name)
            logger.warning(
                "photo_file_missing: plat=%s fichier=%s", row.dish_name, row.file_name
            )
            continue

        query = {"dish_key": normalize(row.dish_name), "drive_file_id": image.file_id}
        existing = await deps.dish_photos.find_one(query)

        changes: dict[str, Any] = {
            "dish_name": row.dish_name,
            "dish_key": normalize(row.dish_name),
            "drive_file_id": image.file_id,
            "file_name": image.name,
            "drive_modified_time": image.modified_time,
            "position": row.position,
            "enabled": row.enabled,
            "updated_at": now,
        }

        if existing is None:
            changes["telegram_file_id"] = ""
            changes["content_hash"] = ""
        elif existing.get("drive_modified_time") != image.modified_time:
            changes["telegram_file_id"] = ""
            changes["content_hash"] = ""
            logger.info("photo_cache_invalidated: fichier=%s", image.name)

        await deps.dish_photos.update_one(query, {"$set": changes}, upsert=True)
        kept_file_ids.append(image.file_id)
        report.synced += 1

    await deps.dish_photos.delete_many({"drive_file_id": {"$nin": kept_file_ids}})

    logger.info(
        "photo_catalogue_synced: synced=%d missing=%d",
        report.synced,
        len(report.missing_files),
    )
    return report
```

Compléter les imports en tête de `src/photos/sync.py` :

```python
from datetime import datetime, timezone
from typing import Any, Sequence

from src.photos.catalogue import parse_catalogue
from src.photos.matcher import normalize
```

- [ ] **Step 5: Lancer les tests**

Run: `uv run pytest tests/photos/ -v`
Expected: PASS — 6 tests de sync catalogue en plus

- [ ] **Step 6: Commit**

```bash
git add src/drive/client.py src/photos/sync.py tests/photos/test_sync_catalogue.py
git commit -m "feat: synchronisation du catalogue photos vers MongoDB"
```

---

### Task 6 : Le sender et son cache

**Files:**
- Create: `src/photos/sender.py`
- Test: `tests/photos/test_sender.py`

**Interfaces:**
- Consumes: `DishEntry`, `find_dishes`, `normalize` (Task 2), `AppDependencies.dish_photos` (Task 1), `DriveClient.fetch_bytes` (Task 5)
- Produces:
  - `PhotoSender` (Protocol) : `send_photo(chat_id: int, photo: Any, caption: str) -> str` rendant le `file_id`, et `send_media_group(chat_id: int, media: Sequence[tuple[Any, str]]) -> list[str]` rendant les `file_id` dans l'ordre
  - `load_active_entries(deps) -> list[DishEntry]`
  - `maybe_send_photos(deps, chat_id: int, text: str, sender: PhotoSender, drive_client: Any) -> list[str]` rendant les noms des plats effectivement envoyés

- [ ] **Step 1: Écrire les tests qui échouent**

Créer `tests/photos/test_sender.py` :

```python
"""Tests de l'envoi des photos et du cache file_id."""

from types import SimpleNamespace
from typing import Any, Sequence

import pytest

from src.photos.sender import maybe_send_photos


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, _length: Any) -> list[dict[str, Any]]:
        return self._docs


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def find(self, query: dict[str, Any], *args: Any, **kwargs: Any) -> _FakeCursor:
        if query.get("enabled") is True:
            return _FakeCursor([d for d in self.docs if d.get("enabled")])
        return _FakeCursor(list(self.docs))

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        self.updates.append((query, update))
        for doc in self.docs:
            if doc.get("drive_file_id") == query.get("drive_file_id"):
                doc.update(update.get("$set", {}))


class _SpySender:
    def __init__(self) -> None:
        self.photos: list[tuple[int, Any, str]] = []
        self.groups: list[tuple[int, list[tuple[Any, str]]]] = []

    async def send_photo(self, chat_id: int, photo: Any, caption: str) -> str:
        self.photos.append((chat_id, photo, caption))
        return "tg-single"

    async def send_media_group(
        self, chat_id: int, media: Sequence[tuple[Any, str]]
    ) -> list[str]:
        self.groups.append((chat_id, list(media)))
        return [f"tg-{i}" for i in range(len(media))]


class _SpyDrive:
    def __init__(self) -> None:
        self.downloads: list[str] = []

    async def fetch_bytes(self, meta: Any) -> bytes:
        self.downloads.append(meta.file_id)
        return b"octets"


def _doc(dish: str, key: str, file_id: str, cache: str = "", position: int = 1) -> dict[str, Any]:
    return {
        "dish_name": dish,
        "dish_key": key,
        "drive_file_id": file_id,
        "file_name": f"{file_id}.jpg",
        "telegram_file_id": cache,
        "position": position,
        "enabled": True,
    }


def _deps(collection: _FakeCollection, **overrides: Any) -> Any:
    settings = SimpleNamespace(
        photos_enabled=True,
        photos_max_dishes=4,
        photos_max_images=10,
        photos_caption_suffix="",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return SimpleNamespace(dish_photos=collection, settings=settings)


@pytest.mark.unit
async def test_single_dish_uses_send_photo() -> None:
    """Un seul plat cite part en sendPhoto, pas en album."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa est à 8 500 FCFA.", sender, drive
    )

    assert sent == ["Poulet Yassa"]
    assert len(sender.photos) == 1
    assert sender.groups == []
    assert sender.photos[0][2] == "Poulet Yassa"


@pytest.mark.unit
async def test_several_dishes_use_media_group() -> None:
    """Deux plats cites partent en album."""
    collection = _FakeCollection(
        [
            _doc("Poulet Yassa", "poulet yassa", "img-1"),
            _doc("Thiéboudienne", "thieboudienne", "img-2"),
        ]
    )
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(
        _deps(collection), 42, "Poulet Yassa et Thiéboudienne.", sender, drive
    )

    assert sent == ["Poulet Yassa", "Thiéboudienne"]
    assert sender.photos == []
    assert len(sender.groups[0][1]) == 2


@pytest.mark.unit
async def test_first_send_downloads_then_caches() -> None:
    """Le premier envoi telecharge et enregistre le file_id Telegram."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", sender, drive
    )

    assert drive.downloads == ["img-1"]
    assert collection.docs[0]["telegram_file_id"] == "tg-single"


@pytest.mark.unit
async def test_second_send_downloads_nothing() -> None:
    """Le cache evite tout telechargement au second envoi."""
    collection = _FakeCollection(
        [_doc("Poulet Yassa", "poulet yassa", "img-1", cache="tg-cache")]
    )
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", sender, drive
    )

    assert drive.downloads == []
    assert sender.photos[0][1] == "tg-cache"


@pytest.mark.unit
async def test_caption_suffix_is_appended() -> None:
    """Le suffixe configure apparait dans la legende."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection, photos_caption_suffix="Photo d'illustration"),
        42,
        "Le Poulet Yassa.",
        sender,
        drive,
    )

    assert sender.photos[0][2] == "Poulet Yassa — Photo d'illustration"


@pytest.mark.unit
async def test_images_are_truncated_to_the_limit() -> None:
    """Quatre plats a trois photos rendent dix images, pas douze."""
    docs = []
    for dish, key in [
        ("Poulet Yassa", "poulet yassa"),
        ("Thiéboudienne", "thieboudienne"),
        ("Tarte Tatin", "tarte tatin"),
        ("Bissap", "bissap"),
    ]:
        for position in (1, 2, 3):
            docs.append(_doc(dish, key, f"{key}-{position}", position=position))
    collection = _FakeCollection(docs)
    sender, drive = _SpySender(), _SpyDrive()

    await maybe_send_photos(
        _deps(collection),
        42,
        "Poulet Yassa, Thiéboudienne, Tarte Tatin et Bissap.",
        sender,
        drive,
    )

    assert len(sender.groups[0][1]) == 10


@pytest.mark.unit
async def test_disabled_flag_sends_nothing() -> None:
    """photos_enabled=false coupe la fonctionnalite."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(
        _deps(collection, photos_enabled=False), 42, "Le Poulet Yassa.", sender, drive
    )

    assert sent == []
    assert sender.photos == []


@pytest.mark.unit
async def test_no_dish_sends_nothing() -> None:
    """Un texte sans plat ne declenche aucun appel Telegram."""
    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])
    sender, drive = _SpySender(), _SpyDrive()

    sent = await maybe_send_photos(_deps(collection), 42, "Bonjour !", sender, drive)

    assert sent == []
    assert sender.photos == []
    assert sender.groups == []


@pytest.mark.unit
async def test_telegram_failure_is_swallowed() -> None:
    """Un echec d'envoi ne remonte jamais au client."""

    class _FailingSender(_SpySender):
        async def send_photo(self, chat_id: int, photo: Any, caption: str) -> str:
            raise RuntimeError("Telegram indisponible")

    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])

    sent = await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", _FailingSender(), _SpyDrive()
    )

    assert sent == []


@pytest.mark.unit
async def test_drive_failure_is_swallowed() -> None:
    """Drive injoignable ne fait pas echouer le tour de conversation."""

    class _FailingDrive:
        async def fetch_bytes(self, meta: Any) -> bytes:
            raise RuntimeError("Drive injoignable")

    collection = _FakeCollection([_doc("Poulet Yassa", "poulet yassa", "img-1")])

    sent = await maybe_send_photos(
        _deps(collection), 42, "Le Poulet Yassa.", _SpySender(), _FailingDrive()
    )

    assert sent == []
```

- [ ] **Step 2: Lancer les tests pour vérifier qu'ils échouent**

Run: `uv run pytest tests/photos/test_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.photos.sender'`

- [ ] **Step 3: Écrire le sender**

Créer `src/photos/sender.py` :

```python
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

    seen: set[str] = set()
    entries: list[DishEntry] = []
    for document in documents:
        key = document.get("dish_key", "")
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(DishEntry(name=document["dish_name"], key=key))
    return entries


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
        entries = await load_active_entries(deps)
        if not entries:
            return []

        dishes = find_dishes(text, entries, deps.settings.photos_max_dishes)
        if not dishes:
            logger.info("photos_skipped: chat_id=%d plats=0", chat_id)
            return []

        documents = await deps.dish_photos.find({"enabled": True}).to_list(None)
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
                    {"drive_file_id": document["drive_file_id"]},
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
```

- [ ] **Step 4: Lancer les tests**

Run: `uv run pytest tests/photos/test_sender.py -v`
Expected: PASS — 10 tests

- [ ] **Step 5: Commit**

```bash
git add src/photos/sender.py tests/photos/test_sender.py
git commit -m "feat: envoi des photos avec cache file_id Telegram"
```

---

### Task 7 : Intégration dans le bot

**Files:**
- Modify: `src/telegram_bot.py` (ajout d'un adaptateur, appel dans `on_message`, câblage dans `main`)
- Test: `tests/test_telegram_bot.py`

**Interfaces:**
- Consumes: `maybe_send_photos`, `PhotoSender` (Task 6), `DriveClient` (`src/drive/client.py`)
- Produces: `TelegramPhotoSender`, conforme au protocole `PhotoSender`

- [ ] **Step 1: Écrire le test qui échoue**

Ajouter à la fin de `tests/test_telegram_bot.py` :

```python
class _SpyPhotoBot:
    def __init__(self) -> None:
        self.photos: list[tuple[int, Any, str]] = []
        self.groups: list[tuple[int, Any]] = []

    async def send_photo(self, chat_id: int, photo: Any, caption: str) -> Any:
        self.photos.append((chat_id, photo, caption))
        return SimpleNamespace(photo=[SimpleNamespace(file_id="tg-small"), SimpleNamespace(file_id="tg-big")])

    async def send_media_group(self, chat_id: int, media: Any) -> Any:
        self.groups.append((chat_id, media))
        return [
            SimpleNamespace(photo=[SimpleNamespace(file_id=f"tg-{i}")])
            for i in range(len(media))
        ]


@pytest.mark.unit
async def test_photo_sender_returns_largest_file_id() -> None:
    """L'adaptateur rend le file_id de la plus grande taille proposee."""
    from src.telegram_bot import TelegramPhotoSender

    bot = _SpyPhotoBot()
    sender = TelegramPhotoSender(bot)

    file_id = await sender.send_photo(42, b"octets", "Poulet Yassa")

    assert file_id == "tg-big"
    assert bot.photos[0][0] == 42


@pytest.mark.unit
async def test_photo_sender_media_group_returns_file_ids() -> None:
    """L'album rend un file_id par image, dans l'ordre."""
    from src.telegram_bot import TelegramPhotoSender

    bot = _SpyPhotoBot()
    sender = TelegramPhotoSender(bot)

    file_ids = await sender.send_media_group(
        42, [(b"a", "Plat A"), (b"b", "Plat B")]
    )

    assert file_ids == ["tg-0", "tg-1"]
```

- [ ] **Step 2: Lancer le test pour vérifier qu'il échoue**

Run: `uv run pytest tests/test_telegram_bot.py -v`
Expected: FAIL — `ImportError: cannot import name 'TelegramPhotoSender'`

- [ ] **Step 3: Écrire l'adaptateur**

Dans `src/telegram_bot.py`, ajouter l'import en tête :

```python
from telegram import InputMediaPhoto, Update

from src.drive.client import DriveClient
from src.photos.sender import maybe_send_photos
```

(remplacer la ligne `from telegram import Update` existante par la première ligne ci-dessus)

Puis ajouter après la classe `TelegramSender` :

```python
class TelegramPhotoSender:
    """Adaptateur d'envoi de photos, conforme au protocole `PhotoSender`."""

    def __init__(self, bot: Any) -> None:
        """
        Args:
            bot: Instance `telegram.Bot` ou equivalent.
        """
        self.bot = bot

    @staticmethod
    def _largest(message: Any) -> str:
        """Rend le file_id de la plus grande taille proposee par Telegram."""
        sizes = getattr(message, "photo", None) or []
        return sizes[-1].file_id if sizes else ""

    async def send_photo(self, chat_id: int, photo: Any, caption: str) -> str:
        """
        Envoie une photo unique.

        Args:
            chat_id: Conversation destinataire.
            photo: file_id Telegram ou octets de l'image.
            caption: Legende affichee sous la photo.

        Returns:
            Le file_id Telegram de l'image envoyee.
        """
        message = await self.bot.send_photo(
            chat_id=chat_id, photo=photo, caption=caption
        )
        return self._largest(message)

    async def send_media_group(
        self, chat_id: int, media: Sequence[tuple[Any, str]]
    ) -> list[str]:
        """
        Envoie un album de 2 a 10 photos.

        Args:
            chat_id: Conversation destinataire.
            media: Couples (source, legende), dans l'ordre d'affichage.

        Returns:
            Les file_id Telegram, dans le meme ordre.
        """
        messages = await self.bot.send_media_group(
            chat_id=chat_id,
            media=[
                InputMediaPhoto(media=source, caption=caption)
                for source, caption in media
            ],
        )
        return [self._largest(message) for message in messages]
```

Compléter l'import de typing en tête du fichier : `from typing import Any, Sequence`.

- [ ] **Step 4: Lancer le test**

Run: `uv run pytest tests/test_telegram_bot.py -v`
Expected: PASS

- [ ] **Step 5: Brancher l'envoi dans `on_message`**

Dans `src/telegram_bot.py`, remplacer le corps de `main` à partir de `sender = TelegramSender(application.bot)` par :

```python
        sender = TelegramSender(application.bot)
        photo_sender = TelegramPhotoSender(application.bot)
        drive_client = DriveClient(deps.settings.google_service_account_file)

        async def on_message(
            update: Update, context: ContextTypes.DEFAULT_TYPE
        ) -> None:
            """Relaie un message Telegram vers l'agent, puis illustre la reponse."""
            if update.message is None or update.message.chat is None:
                return

            chat_id = update.message.chat.id
            reply = await handle_message(
                deps, agent, chat_id, update.message.text or "", sender
            )
            if not reply:
                return

            await update.message.reply_text(reply)
            await maybe_send_photos(
                deps, chat_id, reply, photo_sender, drive_client
            )
```

- [ ] **Step 6: Lancer la suite complète**

Run: `uv run pytest tests/ -m unit -v`
Expected: PASS — aucune régression sur les tests existants

- [ ] **Step 7: Vérifier le lint**

Run: `uv run ruff check src/ tests/`
Expected: aucune erreur

- [ ] **Step 8: Commit**

```bash
git add src/telegram_bot.py tests/test_telegram_bot.py
git commit -m "feat: illustrer les reponses du bot avec les photos des plats"
```

---

### Task 8 : Index MongoDB et documentation

**Files:**
- Modify: `scripts/create_indexes.py`
- Modify: `README.md`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `Settings.mongodb_collection_dish_photos` (Task 1)
- Produces: rien de consommé par du code

- [ ] **Step 1: Ajouter les index**

Dans `scripts/create_indexes.py`, ajouter la création des index de `dish_photos`, en suivant le style déjà présent dans le fichier :

```python
    await deps.dish_photos.create_index(
        [("dish_key", 1), ("drive_file_id", 1)], unique=True
    )
    await deps.dish_photos.create_index([("dish_key", 1), ("enabled", 1)])
```

- [ ] **Step 2: Lancer le script**

Run: `uv run python -m scripts.create_indexes`
Expected: le script se termine sans erreur, les deux index sont créés

- [ ] **Step 3: Documenter la configuration**

Ajouter à `.env.example` :

```
# Photos des plats
PHOTOS_ENABLED=true
PHOTOS_MAX_DISHES=4
PHOTOS_CAPTION_SUFFIX=
DRIVE_PHOTOS_CATALOGUE_NAME=photos
```

Ajouter au `README.md`, dans une section « Photos des plats » :

```markdown
## Photos des plats

Le bot illustre ses réponses quand elles citent 1 à 4 plats. Les images vivent
dans Drive, à côté des documents du menu, accompagnées d'un catalogue `photos`
(Google Sheet ou CSV) aux colonnes `plat`, `fichier`, `ordre`, `actif`.

La colonne `plat` doit reprendre le nom exact du document menu : c'est la clé
d'association. Passer `actif` à `non` retire une photo sans supprimer le
fichier.

Chaque image n'est téléchargée et envoyée à Telegram qu'une seule fois : son
`file_id` est ensuite réutilisé. Remplacer une image dans Drive invalide
automatiquement ce cache au cycle de synchronisation suivant.

`PHOTOS_ENABLED=false` coupe la fonctionnalité sans redéploiement.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/create_indexes.py README.md .env.example
git commit -m "docs: index dish_photos et documentation du catalogue photos"
```

---

## Vérification finale

- [ ] `uv run pytest tests/ -m unit -v` — tous verts
- [ ] `uv run ruff check src/ tests/` — aucune erreur
- [ ] Déposer `photos/` et le catalogue dans Drive, lancer `uv run python -m scripts.run_sync_once`, vérifier que `dish_photos` contient 36 lignes actives et 4 inactives
- [ ] Sur Telegram : « je veux du poisson » doit produire le texte puis un album des variantes de poisson
- [ ] Sur Telegram : « c'est quoi votre menu ? » doit produire le texte **sans** photo — le seuil est dépassé
- [ ] Sur Telegram : « le fufu sauce arachide » ne doit **pas** déclencher la photo « Eau »
- [ ] Deuxième envoi du même plat : vérifier dans les logs qu'aucun `drive_downloaded` n'apparaît
