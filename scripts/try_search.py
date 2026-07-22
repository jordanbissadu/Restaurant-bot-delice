"""Interroge le menu depuis la ligne de commande, pour verification manuelle."""

import asyncio
import sys

from src.dependencies import AppDependencies
from src.tools.search_menu import search_menu


async def main() -> None:
    """Affiche les resultats de recherche pour la requete passee en argument."""
    query = " ".join(sys.argv[1:]) or "poisson"
    async with AppDependencies() as deps:
        for result in await search_menu(deps, query):
            print(f"[{result.score:.3f}] {result.content[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
