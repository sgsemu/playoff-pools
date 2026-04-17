# Manual Draft Order

## Problem

Draft order is currently fixed as the `joined_at` order of pool members, applied in a snake pattern when the creator starts the draft. Pool creators have no way to override it (e.g., to respect an agreed-upon order from outside the app).

## Goal

Let the pool creator reorder members before starting the draft via drag-and-drop on the draft room page. The chosen order drives the snake draft.

## Non-goals

- Randomize / reset controls.
- Editing order after the draft has started.
- Applying this to auction or salary-cap pools.
- Per-round order overrides.

## Data

Add `draft_position INT NULL` to `pool_members` via migration `migrations/002_add_draft_position.sql`.

Effective draft order is `ORDER BY draft_position NULLS LAST, joined_at`. Members whose position has never been set fall back to join order and sit after any positioned members. A member who joins the pool after the creator has reordered therefore lands at the end automatically, with no extra code path.

## API

`POST /pool/<pool_id>/draft/order`

- Auth: logged-in user, must be the pool creator.
- Guard: rejects when `pool.draft_status != 'pending'`.
- Body: `{ "member_ids": ["<uuid>", "<uuid>", ...] }`.
- Validation: the submitted list must equal the current pool member set (same ids, no dupes, no missing). Reject 400 otherwise.
- Effect: writes `draft_position = 1..N` on the corresponding `pool_members` rows. Done as a batch (loop of updates is acceptable given small N).
- Response: `{ "success": true }`.

## Snake order consumer

`_get_snake_order` is unchanged. The member list it receives changes its ordering: `routes/draft.py` `draft_room` sorts members by `(draft_position if not None else infinity, joined_at)` before building `member_ids`. No schema coupling inside `_get_snake_order`.

## UI

On `templates/pool/draft_room.html`, when `draft_status == 'pending'`:

- Render a "Draft Order" section above the existing "Start Draft" button.
- Each row: `#N` badge, display name, drag handle on the right.
- Creator sees draggable rows. Everyone else sees the same list read-only.
- Uses native HTML5 drag-and-drop (`draggable`, `dragstart`, `dragover`, `drop`). No new dependencies.
- On drop:
  1. Snapshot the pre-drop order.
  2. Re-render the list with the new order and renumber `#N`.
  3. POST the new `member_ids` array to the endpoint.
  4. On non-200, restore the snapshot and show an inline error.

Once `draft_status == 'active'`, the section is no longer draggable; the picks log takes over.

## Testing

- Unit: validation of the endpoint (non-creator rejected, wrong member set rejected, active-draft rejected, happy path writes positions 1..N).
- Unit: draft_room ordering when some members have `draft_position` set and some don't.
- Manual: drag a row, confirm persistence on reload, confirm the snake turn indicator reflects the new order.
