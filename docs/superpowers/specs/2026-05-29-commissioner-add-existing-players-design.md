# Commissioner: Add Existing Players

## Problem

Members join a pool only via the invite link (self-service). A commissioner
re-running a league with the same crew has to chase everyone to click a new
link. They want to directly add people who've signed up before.

## Goal

Let a pool's commissioner add existing registered users — limited to people
they've already played with — to a `pending` pool, in one click. The invite
link stays for brand-new people who still need to sign up.

## Non-goals

- Exposing the full user base. A commissioner only sees their own circle.
- Adding members after the draft has started (avoids scrambling snake order).
- Email/in-app notification on add (silent for now; Resend is wired for a
  later follow-up).
- Removing members (out of scope).

## Who is addable

Distinct users who are co-members of **any pool the commissioner belongs to**,
minus:
- users already in the target pool, and
- the commissioner themselves.

Listed by **display name only** (no emails) — they're people the commissioner
already knows. Empty list → show only the invite link.

## Backend

- `get_addable_players(sb, pool_id, commissioner_id) -> [{id, display_name}]`
  in `routes/pools.py` (takes `sb` explicitly so it unit-tests without
  patching). Steps: pools where the commissioner is a member → co-members in
  those pools → drop current members of `pool_id` and the commissioner → fetch
  display names.
- `pool_home` computes `addable_players` only when the viewer is the creator
  and the pool is `pending`; passes it to the template.
- `POST /pool/<pool_id>/members/add` (login required):
  - creator-only → else 403; pool must be `pending` → else 409.
  - body `user_id`; re-validate it's in `get_addable_players` (so a forged id
    can't be added) → else 403.
  - insert `pool_members {pool_id, user_id, role: 'member'}`; `UNIQUE(pool_id,
    user_id)` dedupes (treat a duplicate as a no-op).
  - flash + redirect back to the pool home. New member appears in the members
    list and appends to the draft order (`draft_position` NULLS LAST).

## UI

On `templates/pool/home.html`, in the creator-only `pending` area near the
invite link: a small "Add players you've played with" list. Each row = display
name + an **Add** button (a tiny `POST` form, no new JS). Hidden entirely when
the addable list is empty.

## Testing

- `get_addable_players`: includes a prior co-player, excludes a current member
  and the commissioner.
- add route: creator + pending adds the member; non-creator → 403; non-pending
  → 409; a user outside the commissioner's circle → 403.
