"""
WeBot buddy state helpers.

This maps the Claude-Code-inspired companion concept onto Clawcross's durable
runtime store and browser UI instead of a terminal renderer.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import random
from typing import Any

from webot.runtime_store import (
    BuddyStateRecord,
    get_buddy_state as load_buddy_state,
    save_buddy_state,
)


_SPECIES = (
    "duck",
    "goose",
    "blob",
    "cat",
    "dragon",
    "octopus",
    "owl",
    "penguin",
    "turtle",
    "snail",
    "ghost",
    "axolotl",
    "capybara",
    "cactus",
    "robot",
    "rabbit",
    "mushroom",
    "chonk",
)
_RARITIES = (
    ("common", 60, 5),
    ("uncommon", 25, 15),
    ("rare", 10, 25),
    ("epic", 4, 35),
    ("legendary", 1, 50),
)
_EYES = ("·", "+", "x", "@", "°", "*")
_HATS = ("none", "crown", "tophat", "propeller", "halo", "wizard", "beanie", "tinyduck")
_STAT_NAMES = ("DEBUGGING", "PATIENCE", "CHAOS", "WISDOM", "SNARK")
_NAMES = ("Pico", "Mochi", "Rune", "Pebble", "Aster", "Comet", "Miso", "Sprig", "Nova", "Tango")
_TRAITS = (
    "curious",
    "sharp",
    "chaotic",
    "earnest",
    "dramatic",
    "gentle",
    "sleepy",
    "mischievous",
)
_PET_REACTIONS = (
    "settles beside the prompt",
    "looks energized",
    "wiggles with focus",
    "demands a cleaner diff",
    "seems pleased with the progress",
)
_ACTION_REACTIONS = {
    "review": "squints at the diff",
    "plan": "starts arranging sticky notes",
    "execute": "leans forward like it means business",
    "dream": "drifts into a memory-sorting trance",
    "bridge": "waves at the remote viewer",
    "voice": "tilts its head to listen",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_for_user(user_id: str) -> int:
    digest = hashlib.sha256(f"{user_id or 'anonymous'}:clawcross-buddy-v1".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _rng_for_user(user_id: str) -> random.Random:
    return random.Random(_seed_for_user(user_id))


def _roll_rarity(rng: random.Random) -> tuple[str, int]:
    cursor = rng.randint(1, sum(weight for _, weight, _ in _RARITIES))
    for rarity, weight, floor in _RARITIES:
        cursor -= weight
        if cursor <= 0:
            return rarity, floor
    fallback = _RARITIES[-1]
    return fallback[0], fallback[2]


def _roll_stats(rng: random.Random, floor: int) -> dict[str, int]:
    stats = {name: floor for name in _STAT_NAMES}
    peak = rng.choice(_STAT_NAMES)
    dump = rng.choice([name for name in _STAT_NAMES if name != peak])
    for name in _STAT_NAMES:
        stats[name] += rng.randint(0, 20)
    stats[peak] += rng.randint(10, 22)
    stats[dump] = max(1, stats[dump] - rng.randint(5, 12))
    return stats


def _generate_base_state(user_id: str) -> dict[str, Any]:
    rng = _rng_for_user(user_id)
    rarity, floor = _roll_rarity(rng)
    hat = "none" if rarity == "common" else rng.choice(_HATS)
    name_suffix = rng.choice(("", " Prime", " Jr.", " II"))
    return {
        "seed": f"{_seed_for_user(user_id):016x}",
        "species": rng.choice(_SPECIES),
        "rarity": rarity,
        "shiny": rng.random() < 0.01,
        "eye": rng.choice(_EYES),
        "hat": hat,
        "stats": _roll_stats(rng, floor),
        "soul_name": f"{rng.choice(_NAMES)}{name_suffix}".strip(),
        "soul_personality": f"{rng.choice(_TRAITS)} companion that {rng.choice(_PET_REACTIONS)}",
    }


def _face_for_species(species: str, eye: str) -> str:
    if species in {"ghost", "blob", "chonk"}:
        return f"({eye}{eye})"
    if species in {"cat", "rabbit", "owl"}:
        return f"^ {eye}{eye} ^"
    if species == "robot":
        return f"[{eye}{eye}]"
    return f"<{eye}{eye}>"


def ensure_buddy_state(user_id: str) -> BuddyStateRecord:
    existing = load_buddy_state(user_id)
    generated = _generate_base_state(user_id)
    if existing is not None and existing.seed and existing.species:
        return existing
    now = _utc_now()
    return save_buddy_state(
        user_id=user_id,
        seed=str(generated["seed"]),
        species=str(generated["species"]),
        rarity=str(generated["rarity"]),
        shiny=bool(generated["shiny"]),
        eye=str(generated["eye"]),
        hat=str(generated["hat"]),
        stats=dict(generated["stats"]),
        soul_name=str(generated["soul_name"]),
        soul_personality=str(generated["soul_personality"]),
        reaction="",
        hatched_at=now,
        last_interaction_at=now,
        metadata={"profile_version": "clawcross-buddy-v1"},
    )


def apply_buddy_action(user_id: str, action: str, note: str = "") -> dict[str, Any]:
    record = ensure_buddy_state(user_id)
    normalized = (action or "pet").strip().lower() or "pet"
    reaction = _ACTION_REACTIONS.get(normalized)
    if normalized == "pet" or not reaction:
        reaction_rng = random.Random(_seed_for_user(user_id) ^ int(datetime.now(timezone.utc).timestamp()))
        reaction = reaction_rng.choice(_PET_REACTIONS)
    if note:
        reaction = f"{reaction} - {note[:80]}"
    updated = save_buddy_state(
        user_id=user_id,
        seed=record.seed,
        species=record.species,
        rarity=record.rarity,
        shiny=record.shiny,
        eye=record.eye,
        hat=record.hat,
        stats=record.stats,
        soul_name=record.soul_name,
        soul_personality=record.soul_personality,
        reaction=reaction,
        hatched_at=record.hatched_at or _utc_now(),
        last_interaction_at=_utc_now(),
        metadata=dict(record.metadata),
    )
    return _serialize_record(updated)


def pet_buddy(user_id: str, note: str = "") -> dict[str, Any]:
    return apply_buddy_action(user_id, "pet", note)


def _serialize_record(record: BuddyStateRecord) -> dict[str, Any]:
    return {
        "enabled": True,
        "seed": record.seed,
        "species": record.species,
        "rarity": record.rarity,
        "shiny": record.shiny,
        "eye": record.eye,
        "hat": record.hat,
        "stats": dict(record.stats),
        "soul": {
            "name": record.soul_name,
            "personality": record.soul_personality,
        },
        "name": record.soul_name,
        "personality": record.soul_personality,
        "reaction": record.reaction,
        "last_bubble": record.reaction,
        "hatched_at": record.hatched_at,
        "last_interaction_at": record.last_interaction_at,
        "compact_face": _face_for_species(record.species, record.eye or "·"),
        "sprite_hint": f"{record.species}:{record.rarity}",
        "available_actions": ["pet", "review", "plan", "execute", "dream", "bridge", "voice"],
        "metadata": dict(record.metadata),
        "updated_at": record.updated_at,
    }


def serialize_buddy_state(user_id: str) -> dict[str, Any]:
    return _serialize_record(ensure_buddy_state(user_id))


def get_buddy_state(user_id: str) -> dict[str, Any]:
    return serialize_buddy_state(user_id)
