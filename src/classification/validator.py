"""
Deterministic Validator for Classification Output — v14

Pure-Python module (no LLM) that validates the JSON output from the classifier
against the taxonomy DB. Checks key existence, dimension correctness, pack
match, selection count limits, governance rules, and allowed combinations.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from .models import (
    TaxonomyAllowedCombination,
    TaxonomyDimension,
    TaxonomyNode,
    TaxonomyPack,
)

logger = logging.getLogger(__name__)


SELECTION_LIMITS = {
    "subject": {"primary_max": 1, "secondary_max": 3},
    "intent": {"primary_max": 1, "secondary_max": 2},
    "context": {"primary_max": 1, "secondary_max": 2},
    "time": {"total_max": 3},
    "governance": {"primary_max": 1, "secondary_max": 0},
}


@dataclass
class ValidationResult:
    is_valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    normalized: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class TaxonomyLookup:
    """In-memory taxonomy index loaded once per validation run."""
    nodes_by_key: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    active_keys: Set[str] = field(default_factory=set)
    selectable_keys: Set[str] = field(default_factory=set)
    dimension_by_key: Dict[str, str] = field(default_factory=dict)
    pack_by_key: Dict[str, str] = field(default_factory=dict)
    id_by_key: Dict[str, str] = field(default_factory=dict)


def load_taxonomy_lookup(
    pack: Optional[str] = None,
) -> TaxonomyLookup:
    """
    Load taxonomy into an in-memory lookup structure.
    Single DB query, returns a TaxonomyLookup for fast validation.
    """
    lookup = TaxonomyLookup()

    qs = TaxonomyNode.objects.all()
    if pack:
        qs = qs.filter(taxonomy_pack__in=[pack, TaxonomyPack.SHARED])

    for node in qs.values("id", "key", "dimension", "taxonomy_pack", "is_active", "is_selectable"):
        key = node["key"]
        lookup.nodes_by_key[key] = node
        lookup.id_by_key[key] = str(node["id"])
        lookup.dimension_by_key[key] = node["dimension"]
        lookup.pack_by_key[key] = node["taxonomy_pack"]
        if node["is_active"]:
            lookup.active_keys.add(key)
        if node["is_selectable"]:
            lookup.selectable_keys.add(key)

    return lookup


def _check_key_exists(key: str, lookup: TaxonomyLookup, errors: List[str]) -> bool:
    """Verify key exists in taxonomy."""
    if key not in lookup.nodes_by_key:
        errors.append(f"Key not found in taxonomy: {key}")
        return False
    return True


def _check_key_active(key: str, lookup: TaxonomyLookup, errors: List[str]) -> bool:
    """Verify key is active."""
    if key not in lookup.active_keys:
        errors.append(f"Key is inactive: {key}")
        return False
    return True


def _check_key_selectable(key: str, lookup: TaxonomyLookup, errors: List[str]) -> bool:
    """Verify key is selectable (usually leaf nodes)."""
    if key not in lookup.selectable_keys:
        errors.append(f"Key is not selectable: {key}")
        return False
    return True


def _check_dimension_match(key: str, expected_dim: str, lookup: TaxonomyLookup, errors: List[str]) -> bool:
    """Verify key belongs to the expected dimension."""
    actual_dim = lookup.dimension_by_key.get(key, "")
    if actual_dim != expected_dim:
        errors.append(f"Key {key} belongs to dimension '{actual_dim}', expected '{expected_dim}'")
        return False
    return True


def _check_pack_match(key: str, allowed_pack: str, lookup: TaxonomyLookup, errors: List[str]) -> bool:
    """Verify key belongs to the allowed pack or 'shared'."""
    actual_pack = lookup.pack_by_key.get(key, "")
    if actual_pack not in (allowed_pack, TaxonomyPack.SHARED):
        errors.append(f"Key {key} belongs to pack '{actual_pack}', expected '{allowed_pack}' or 'shared'")
        return False
    return True


def _validate_key(
    key: str,
    expected_dim: str,
    allowed_pack: str,
    lookup: TaxonomyLookup,
    errors: List[str],
) -> bool:
    """Run all key-level checks. Returns True if key is valid."""
    if not _check_key_exists(key, lookup, errors):
        return False
    ok = True
    ok = _check_key_active(key, lookup, errors) and ok
    ok = _check_key_selectable(key, lookup, errors) and ok
    ok = _check_dimension_match(key, expected_dim, lookup, errors) and ok
    ok = _check_pack_match(key, allowed_pack, lookup, errors) and ok
    return ok


def _validate_primary_keys(
    primary: Dict[str, Optional[str]],
    allowed_pack: str,
    lookup: TaxonomyLookup,
    result: ValidationResult,
) -> Dict[str, List[str]]:
    """
    Validate primary selections. Returns resolved IDs per dimension.
    Expects: {"subject_key": str|null, "intent_key": str|null, ...}
    """
    resolved = {}
    dim_map = {
        "subject_key": "subject",
        "intent_key": "intent",
        "context_key": "context",
        "governance_key": "governance",
    }

    for field_name, dim in dim_map.items():
        key = primary.get(field_name)
        if key:
            if _validate_key(key, dim, allowed_pack, lookup, result.errors):
                resolved[f"resolved_{dim}_ids"] = [lookup.id_by_key[key]]
            else:
                result.is_valid = False
        elif dim == "governance":
            result.errors.append("Governance primary key is required")
            result.is_valid = False

    return resolved


def _validate_secondary_keys(
    secondary: Dict[str, List[str]],
    allowed_pack: str,
    lookup: TaxonomyLookup,
    result: ValidationResult,
) -> Dict[str, List[str]]:
    """
    Validate secondary selections. Returns additional resolved IDs.
    Expects: {"subject_keys": [], "intent_keys": [], ...}
    """
    resolved = {}
    dim_map = {
        "subject_keys": ("subject", SELECTION_LIMITS["subject"]["secondary_max"]),
        "intent_keys": ("intent", SELECTION_LIMITS["intent"]["secondary_max"]),
        "context_keys": ("context", SELECTION_LIMITS["context"]["secondary_max"]),
        "time_keys": ("time", SELECTION_LIMITS["time"]["total_max"]),
    }

    for field_name, (dim, max_count) in dim_map.items():
        keys = secondary.get(field_name, [])
        if not isinstance(keys, list):
            result.errors.append(f"{field_name} must be a list")
            result.is_valid = False
            continue

        if len(keys) > max_count:
            result.errors.append(
                f"{field_name} has {len(keys)} items, max allowed is {max_count}"
            )
            result.is_valid = False

        ids = []
        for key in keys[:max_count]:
            if _validate_key(key, dim, allowed_pack, lookup, result.errors):
                ids.append(lookup.id_by_key[key])
            else:
                result.is_valid = False
        resolved_key = f"resolved_{dim}_ids"
        resolved[resolved_key] = resolved.get(resolved_key, []) + ids

    return resolved


def _validate_allowed_combinations(
    primary: Dict[str, Optional[str]],
    lookup: TaxonomyLookup,
    result: ValidationResult,
):
    """
    Check if the combination of primary selections is allowed.
    Only runs if TaxonomyAllowedCombination rows exist.

    Currently dormant: no TaxonomyAllowedCombination rows are seeded,
    so ``combos.exists()`` always short-circuits. Retained for future use
    when admin-defined combination constraints are introduced.
    """
    combos = TaxonomyAllowedCombination.objects.all()
    if not combos.exists():
        return

    subject_id = lookup.id_by_key.get(primary.get("subject_key", "") or "")
    intent_id = lookup.id_by_key.get(primary.get("intent_key", "") or "")
    context_id = lookup.id_by_key.get(primary.get("context_key", "") or "")
    governance_id = lookup.id_by_key.get(primary.get("governance_key", "") or "")

    for combo in combos:
        matches = True
        if combo.subject_node_id and str(combo.subject_node_id) != subject_id:
            matches = False
        if combo.intent_node_id and str(combo.intent_node_id) != intent_id:
            matches = False
        if combo.context_node_id and str(combo.context_node_id) != context_id:
            matches = False
        if combo.governance_node_id and str(combo.governance_node_id) != governance_id:
            matches = False

        if matches and not combo.is_allowed:
            result.errors.append(
                f"Disallowed combination found: {combo}"
            )
            result.is_valid = False
            return


def validate_classification_output(
    output: Dict[str, Any],
    allowed_pack: str,
    taxonomy_lookup: Optional[TaxonomyLookup] = None,
) -> ValidationResult:
    """
    Validate a classification LLM output against the taxonomy database.

    Args:
        output: Parsed JSON from the classifier LLM (PRD 9.1 output shape)
        allowed_pack: The taxonomy pack this item should use ('personal' or 'enterprise')
        taxonomy_lookup: Pre-loaded lookup (for reuse across calls). If None, loads fresh.

    Returns:
        ValidationResult with is_valid, errors, warnings, and normalized IDs.
    """
    result = ValidationResult()

    if not isinstance(output, dict):
        result.is_valid = False
        result.errors.append("Output must be a JSON object")
        return result

    if taxonomy_lookup is None:
        taxonomy_lookup = load_taxonomy_lookup(pack=allowed_pack)

    if not taxonomy_lookup.nodes_by_key:
        result.is_valid = False
        result.errors.append("No taxonomy nodes found in database")
        return result

    primary = output.get("primary", {})
    secondary = output.get("secondary", {})

    if not isinstance(primary, dict):
        result.is_valid = False
        result.errors.append("'primary' must be a JSON object")
        return result

    if not isinstance(secondary, dict):
        result.is_valid = False
        result.errors.append("'secondary' must be a JSON object")
        return result

    # Validate primary keys
    resolved_primary = _validate_primary_keys(primary, allowed_pack, taxonomy_lookup, result)

    # Validate secondary keys
    resolved_secondary = _validate_secondary_keys(secondary, allowed_pack, taxonomy_lookup, result)

    # Merge resolved IDs
    for dim_key, ids in resolved_secondary.items():
        existing = resolved_primary.get(dim_key, [])
        result.normalized[dim_key] = existing + ids
    for dim_key, ids in resolved_primary.items():
        if dim_key not in result.normalized:
            result.normalized[dim_key] = ids

    # Validate allowed combinations
    if result.is_valid:
        _validate_allowed_combinations(primary, taxonomy_lookup, result)

    # Validate entities structure (lightweight)
    entities = output.get("entities", [])
    if not isinstance(entities, list):
        result.errors.append("'entities' must be a list")
        result.is_valid = False
    else:
        for i, entity in enumerate(entities):
            if not isinstance(entity, dict):
                result.errors.append(f"Entity at index {i} must be a JSON object")
                result.is_valid = False
                continue
            if "entity_type" not in entity:
                result.warnings.append(f"Entity at index {i} missing 'entity_type'")
            if "raw_mention" not in entity:
                result.warnings.append(f"Entity at index {i} missing 'raw_mention'")

    if result.errors:
        result.is_valid = False

    return result
