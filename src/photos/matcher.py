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
from collections.abc import Sequence
from dataclasses import dataclass

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

        current = "".join(haystack)
        pattern = re.compile(
            r"(?<![0-9a-z])" + re.escape(entry.key) + r"(?![0-9a-z])"
        )
        match = pattern.search(current)
        if match is None:
            continue

        found.append((match.start(), entry.name))
        seen.add(entry.name)

        # Blanchir toutes les occurrences pour qu'un nom plus court ne puisse
        # plus matcher a l'interieur d'un nom deja consomme.
        for occurrence in pattern.finditer(current):
            for index in range(occurrence.start(), occurrence.end()):
                haystack[index] = " "

    if not found or len(found) > max_dishes:
        return []

    found.sort(key=lambda pair: pair[0])
    return [name for _, name in found]
