"""Extrait le system prompt du workflow n8n vers src/prompts.py.

Execute une seule fois, a la mise en place du projet. Le fichier genere est
ensuite versionne et edite manuellement si le prompt doit evoluer.

Usage:
    uv run python -m scripts.extract_prompt "<chemin du .json n8n>"
"""

import json
import sys
from pathlib import Path

TARGET = Path("src/prompts.py")

DOCSTRING = (
    "Prompts de l'agent conversationnel.\n\n"
    "SYSTEM_PROMPT est repris mot pour mot du workflow n8n\n"
    '"Restaurant - Telegram Bot Le Delice - v1". Toute modification doit etre\n'
    "deliberee : ce texte encode les regles metier de la prise de commande.\n"
)


def main() -> None:
    """Lit le JSON n8n et ecrit src/prompts.py."""
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.extract_prompt <workflow.json>")

    workflow = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    agent = next(n for n in workflow["nodes"] if n["name"] == "AI Agent")
    prompt: str = agent["parameters"]["options"]["systemMessage"]

    quote = '"' * 3
    # Le prompt devient un litteral triple-guillemets. Une sequence de trois
    # guillemets dans le texte casserait le fichier genere : on echoue plutot
    # que de produire un module invalide.
    if quote in prompt:
        raise SystemExit("le prompt contient une sequence de trois guillemets")
    body = prompt.replace("\\", "\\\\")

    lines = [
        quote + DOCSTRING + quote,
        "",
        "SYSTEM_PROMPT = " + quote + "\\",
        body,
        quote,
        "",
    ]
    TARGET.write_text("\n".join(lines), encoding="utf-8")
    print(f"ecrit {TARGET} ({len(prompt)} caracteres)")


if __name__ == "__main__":
    main()
