# Reset de la conversation client après envoi en cuisine

**Date** : 2026-07-27
**Statut** : design approuvé

## Objectif

Effacer l'historique de conversation d'un client dès que sa commande a été
transmise à la cuisine, pour que la prochaine interaction reparte de zéro.

## Portée

- **Uniquement** le client qui vient de commander (son `chat_id`).
- Les conversations des autres clients ne sont **jamais** touchées.
- La commande enregistrée dans la collection `orders` n'est **pas** touchée :
  elle doit rester pour la cuisine et les archives. Seules les collections de
  mémoire conversationnelle (`messages` + `conversations`) sont vidées pour ce
  `chat_id`.

## Déclencheur

Le **succès** de l'outil `notify_kitchen`, c.-à-d. quand le récapitulatif est
réellement parti dans `KITCHEN_CHAT` (`send_to_kitchen` a réussi).

- Si `save_order` réussit mais que `notify_kitchen` échoue → **pas** de
  nettoyage (la mémoire reste intacte, le tour pourra être retenté).

## Mécanisme (approche retenue)

Drapeau porté par le tour de conversation, nettoyage au niveau du bot.

1. Ajouter un champ mutable à `BotDeps` (`src/agent.py`) :
   `order_notified: bool = False`.
2. Dans l'outil `notify_kitchen`, **après** le succès de `send_to_kitchen` :
   `ctx.deps.order_notified = True`.
3. Dans `handle_message` (`src/telegram_bot.py`), **après** `save_turn(...)` :
   si `bot_deps.order_notified`, appeler `clear_history(deps, chat_id)`
   (fonction existante, `src/memory.py`).

### Pourquoi après `save_turn` et pas dans l'outil

`handle_message` persiste le tour courant via `save_turn` **après** le retour de
l'agent. Effacer l'historique à l'intérieur de l'outil laisserait `save_turn`
ré-écrire aussitôt les messages du tour courant → messages fantômes. Le
nettoyage doit donc venir après `save_turn`, qui écrit puis se fait effacer :
ardoise propre garantie, quel que soit le contenu du tour.

## Gestion d'erreur

Le nettoyage est **isolé** dans un `try/except` qui log et continue (même esprit
que la sync photo). Un échec du nettoyage ne doit jamais empêcher le client de
recevoir sa confirmation (déjà calculée) ni la réponse d'être renvoyée.
Journalisation : `history_cleared_after_order: chat_id=...`.

## Conséquence UX (assumée)

Juste après la confirmation, le prochain message du client repart de zéro : le
bot ne se souviendra plus de la commande qui vient d'être passée. Un « ajoute un
coca à ma commande » enchaîné sera traité comme une nouvelle conversation. C'est
le comportement inhérent à « reset après chaque commande » et il est voulu.

## Tests (TDD)

Au niveau de `handle_message` (avec un agent et une mémoire mockés) :

1. `notify_kitchen` réussi → `clear_history` appelé avec le `chat_id` du client,
   **après** `save_turn`.
2. Aucun ordre notifié → `clear_history` **pas** appelé.
3. Un échec de `clear_history` (exception) ne casse pas la réponse renvoyée au
   client.

Au niveau de l'outil `notify_kitchen` : le drapeau `order_notified` passe à
`True` après un envoi réussi.

## Hors périmètre

- Aucun message supplémentaire au client (« conversation réinitialisée ») : la
  confirmation déjà envoyée par l'agent suffit.
- Aucune purge globale multi-clients.
- Aucune modification des collections `orders` / catalogue / photos.
