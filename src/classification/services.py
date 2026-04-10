"""
Classification Services — v14

Full 2-pass LLM classification pipeline:
  1. Decrypt content
  2. Load allowed taxonomy + entity hints
  3. LLM #1 (primary classifier)
  4. Deterministic validator
  5. Optional LLM #2 (verifier)
  6. Deterministic validator on verifier output
  7. Normalize: write ItemClassificationRun, selections, entities
  8. Refresh retrieval projection
"""

import json
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from src.common.ai_client import call_llm_json, get_openai_api_key
from src.ingestion.models import IngestItem, IngestStatus

from .config_taxonomy_classifier import (
    CLASSIFIER_PROMPT_VERSION,
    CLASSIFIER_SYSTEM_PROMPT,
    CLASSIFIER_USER_TEMPLATE,
    CLASSIFIER_VERSION,
    ENABLE_VERIFIER,
    VERIFIER_PROMPT_VERSION,
    VERIFIER_SYSTEM_PROMPT,
    VERIFIER_USER_TEMPLATE,
    VERIFIER_VERSION,
    get_classifier_model_config,
    get_verifier_model_config,
)
from .models import (
    ClassificationRunStatus,
    EntityCatalog,
    EntityType,
    ItemClassificationRun,
    ItemClassificationSelection,
    ItemEntityLink,
    TaxonomyNode,
    TaxonomyPack,
)
from .taxonomy_loader import build_classification_payload, load_allowed_taxonomy, load_entity_hints
from .validator import TaxonomyLookup, ValidationResult, load_taxonomy_lookup, validate_classification_output

logger = logging.getLogger(__name__)

_ENTITY_TYPE_ALIASES = {
    "contacts": EntityType.CONTACT,
    "people": EntityType.PERSON,
    "persons": EntityType.PERSON,
    "orgs": EntityType.ORGANIZATION,
    "organizations": EntityType.ORGANIZATION,
    "locations": EntityType.LOCATION,
    "projects": EntityType.PROJECT,
    "devices": EntityType.DEVICE,
    "accounts": EntityType.ACCOUNT,
    "documents": EntityType.DOCUMENT,
    "products": EntityType.PRODUCT,
    "vendors": EntityType.VENDOR,
    "clients": EntityType.CLIENT,
}


def _normalize_entity_type(value: Any) -> str:
    """Map LLM entity_type to a valid EntityType choice. Handles null, plural forms, typos."""
    if not value or not isinstance(value, str):
        return EntityType.UNKNOWN
    key = value.strip().lower()
    if key in dict(EntityType.choices):
        return key
    return _ENTITY_TYPE_ALIASES.get(key, EntityType.UNKNOWN)


# ---------------------------------------------------------------------------
# Content (plaintext)
# ---------------------------------------------------------------------------

def get_item_content(item: IngestItem) -> str:
    """Return plaintext content from an IngestItem."""
    return item.content_text or ""


# ---------------------------------------------------------------------------
# Normalizer: write classification results to DB
# ---------------------------------------------------------------------------

def _normalize_and_write(
    item: IngestItem,
    final_output: Dict[str, Any],
    raw_classifier_output: Dict[str, Any],
    raw_verifier_output: Optional[Dict[str, Any]],
    validation_result: ValidationResult,
    taxonomy_pack: str,
    taxonomy_lookup: TaxonomyLookup,
) -> ItemClassificationRun:
    """
    Create ItemClassificationRun, ItemClassificationSelection rows,
    upsert EntityCatalog, create ItemEntityLink rows.
    """
    confidence = final_output.get("confidence") or {}
    overall_conf = confidence.get("overall")
    reasoning = final_output.get("reasoning") or {}
    ambiguity = final_output.get("ambiguity") or {}

    reasoning_text = "; ".join(
        f"{k}: {v}" for k, v in reasoning.items() if v
    )

    verifier_reasoning = ""
    verifier_conf = None
    if raw_verifier_output:
        vr = raw_verifier_output.get("reasoning") or {}
        verifier_reasoning = "; ".join(f"{k}: {v}" for k, v in vr.items() if v)
        verifier_conf = (raw_verifier_output.get("confidence") or {}).get("overall")

    run = ItemClassificationRun.objects.create(
        user=item.user,
        ingest_item=item,
        taxonomy_pack_used=taxonomy_pack,
        classifier_version=CLASSIFIER_VERSION,
        prompt_version=CLASSIFIER_PROMPT_VERSION,
        verifier_version=VERIFIER_VERSION if raw_verifier_output else "",
        verifier_prompt_version=VERIFIER_PROMPT_VERSION if raw_verifier_output else "",
        status=ClassificationRunStatus.COMPLETED,
        raw_model_output_json=raw_classifier_output,
        raw_verifier_output_json=raw_verifier_output,
        reasoning_text=reasoning_text,
        verifier_reasoning_text=verifier_reasoning,
        overall_confidence=Decimal(str(overall_conf)) if overall_conf is not None else None,
        verifier_overall_confidence=Decimal(str(verifier_conf)) if verifier_conf is not None else None,
        has_ambiguity=bool(ambiguity.get("has_ambiguity") or False),
        ambiguity_notes=ambiguity.get("notes"),
        validation_errors_json=validation_result.errors if validation_result.errors else None,
    )

    # Write selections
    primary = final_output.get("primary") or {}
    secondary = final_output.get("secondary") or {}

    dim_primary_map = {
        "subject": primary.get("subject_key"),
        "intent": primary.get("intent_key"),
        "context": primary.get("context_key"),
        "governance": primary.get("governance_key"),
    }

    for dim, key in dim_primary_map.items():
        if not key or key not in taxonomy_lookup.id_by_key:
            continue
        dim_conf = confidence.get(dim)
        dim_reason = reasoning.get(f"{dim}_reason") or ""
        ItemClassificationSelection.objects.create(
            classification_run=run,
            ingest_item=item,
            dimension=dim,
            taxonomy_node_id=taxonomy_lookup.id_by_key[key],
            path_key=key,
            is_primary=True,
            rank_order=1,
            confidence=Decimal(str(dim_conf)) if dim_conf is not None else None,
            selection_reason=dim_reason,
        )

    dim_secondary_map = {
        "subject": secondary.get("subject_keys") or [],
        "intent": secondary.get("intent_keys") or [],
        "context": secondary.get("context_keys") or [],
        "time": secondary.get("time_keys") or [],
    }

    for dim, keys in dim_secondary_map.items():
        for rank, key in enumerate(keys, start=1):
            if key not in taxonomy_lookup.id_by_key:
                continue
            ItemClassificationSelection.objects.create(
                classification_run=run,
                ingest_item=item,
                dimension=dim,
                taxonomy_node_id=taxonomy_lookup.id_by_key[key],
                path_key=key,
                is_primary=False,
                rank_order=rank,
            )

    # Write entities
    entities = final_output.get("entities") or []
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        raw_mention = ent.get("raw_mention") or ""
        if not raw_mention:
            continue
        normalized = raw_mention.strip().lower()
        etype = _normalize_entity_type(ent.get("entity_type") or "unknown")

        catalog_entry = None
        try:
            catalog_entry, _ = EntityCatalog.objects.get_or_create(
                user=item.user,
                entity_type=etype,
                normalized_name=normalized,
                defaults={
                    "canonical_name": ent.get("canonical_name") or raw_mention,
                },
            )
        except Exception as e:
            logger.warning("Failed to upsert entity catalog: %s", e)

        ent_conf = ent.get("confidence")
        ItemEntityLink.objects.create(
            classification_run=run,
            ingest_item=item,
            entity=catalog_entry,
            entity_type=etype,
            raw_mention=raw_mention,
            normalized_mention=normalized,
            role=ent.get("role") or "",
            confidence=Decimal(str(ent_conf)) if ent_conf is not None else None,
        )

    return run


# ---------------------------------------------------------------------------
# Main entry point: classify_item()
# ---------------------------------------------------------------------------

def classify_item(item: IngestItem, taxonomy_pack: str = TaxonomyPack.PERSONAL) -> Dict[str, Any]:
    """
    Classify an IngestItem using the v14 hierarchical taxonomy pipeline.

    Steps:
      1. Decrypt content
      2. Load allowed taxonomy + entity hints
      3. LLM #1 (primary classifier)
      4. Deterministic validator
      5. Optional LLM #2 (verifier) if ENABLE_VERIFIER
      6. Deterministic validator on verifier output
      7. Normalize and write to DB
      8. Refresh retrieval projection

    Returns:
        {
            "run_id": str,
            "selections": list,
            "entities": list,
            "usage": {"classifier": {...}, "verifier": {...}},
        }
    """
    api_key = get_openai_api_key()
    if not api_key:
        raise ValueError("OpenAI API key is required for classification")

    content = get_item_content(item)
    if not content or not content.strip():
        logger.warning("Item %s has no content to classify", item.id)
        item.status = IngestStatus.TAGGED
        item.save(update_fields=["status"])
        return {"run_id": None, "selections": [], "entities": [], "usage": {}}

    # Load taxonomy
    allowed_taxonomy = load_allowed_taxonomy(
        pack=taxonomy_pack,
    )
    entity_hints = load_entity_hints(user_id=item.user_id)
    taxonomy_lookup = load_taxonomy_lookup(
        pack=taxonomy_pack,
    )

    # Build classification payload
    title = item.title or ""

    payload = build_classification_payload(
        item_id=str(item.id),
        user_id=str(item.user_id),
        taxonomy_pack=taxonomy_pack,
        provider=item.provider,
        item_type=item.item_type,
        template_type=item.template_type,
        occurred_at=item.occurred_at.isoformat() if item.occurred_at else None,
        ingested_at=item.ingested_at.isoformat() if item.ingested_at else None,
        detected_language=item.detected_language,
        title=title or "",
        content_text=content,
        summary_text="",
        allowed_taxonomy=allowed_taxonomy,
        entity_hints=entity_hints,
    )

    # --- LLM #1: Primary classifier ---
    classifier_config = get_classifier_model_config()
    user_prompt = CLASSIFIER_USER_TEMPLATE.format(
        ingest_item_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )
    classifier_output, classifier_usage = call_llm_json(
        CLASSIFIER_SYSTEM_PROMPT, user_prompt, classifier_config, api_key,
    )

    # --- Deterministic validator on LLM #1 output ---
    validation_result = validate_classification_output(
        classifier_output, taxonomy_pack,
        taxonomy_lookup=taxonomy_lookup,
    )

    final_output = classifier_output
    verifier_output = None
    verifier_usage: Dict[str, int] = {}

    # --- LLM #2: Verifier (configurable) ---
    if ENABLE_VERIFIER:
        verifier_config = get_verifier_model_config()
        verifier_user_prompt = VERIFIER_USER_TEMPLATE.format(
            ingest_item_json=json.dumps(payload, ensure_ascii=False, indent=2),
            primary_output_json=json.dumps(classifier_output, ensure_ascii=False, indent=2),
            validator_json=json.dumps({
                "is_valid": validation_result.is_valid,
                "errors": validation_result.errors,
                "warnings": validation_result.warnings,
            }, ensure_ascii=False, indent=2),
        )
        try:
            verifier_output, verifier_usage = call_llm_json(
                VERIFIER_SYSTEM_PROMPT, verifier_user_prompt, verifier_config, api_key,
            )

            verifier_validation = validate_classification_output(
                verifier_output, taxonomy_pack,
                taxonomy_lookup=taxonomy_lookup,
            )

            if verifier_validation.is_valid:
                final_output = verifier_output
                validation_result = verifier_validation
            else:
                logger.warning(
                    "Verifier output failed validation for item %s, falling back to classifier output: %s",
                    item.id, verifier_validation.errors,
                )
                if not validation_result.is_valid:
                    final_output = verifier_output
                    validation_result = verifier_validation

        except Exception as e:
            logger.warning("Verifier LLM failed for item %s, using classifier output: %s", item.id, e)

    # --- Normalize and write ---
    run = _normalize_and_write(
        item=item,
        final_output=final_output,
        raw_classifier_output=classifier_output,
        raw_verifier_output=verifier_output,
        validation_result=validation_result,
        taxonomy_pack=taxonomy_pack,
        taxonomy_lookup=taxonomy_lookup,
    )

    # --- Refresh retrieval projection ---
    try:
        from src.retrieval.projection import refresh_retrieval_projection
        refresh_retrieval_projection(str(item.id), run)
    except Exception as e:
        logger.error("Failed to refresh retrieval projection for item %s: %s", item.id, e)

    # --- Update item status ---
    item.status = IngestStatus.TAGGED
    item.save(update_fields=["status"])

    logger.info("Classified item %s: run=%s", item.id, run.id)

    return {
        "run_id": str(run.id),
        "selections": list(run.selections.values_list("path_key", flat=True)),
        "entities": list(run.entity_links.values_list("raw_mention", flat=True)),
        "usage": {
            "classifier": classifier_usage,
            "verifier": verifier_usage,
        },
    }


# ---------------------------------------------------------------------------
# Parser route matching (single source of truth for TaxonomyParserRoute)
# ---------------------------------------------------------------------------

def _matches_parser_route(parser_action: str, dimension: str, path_key: str) -> bool:
    """Check if (dimension, path_key) matches any active TaxonomyParserRoute for parser_action."""
    import fnmatch
    from .models import TaxonomyParserRoute

    if not path_key or not dimension:
        return False
    routes = TaxonomyParserRoute.objects.filter(
        parser_action=parser_action,
        dimension_match=dimension,
        is_active=True,
    )
    for route in routes:
        if fnmatch.fnmatch(path_key, route.key_pattern):
            return True
    return False


# ---------------------------------------------------------------------------
# Classification checkers (query ItemRetrievalProjection)
# ---------------------------------------------------------------------------

def _get_projection(item: IngestItem):
    """Return the item's ItemRetrievalProjection or None."""
    try:
        return item.retrieval_projection
    except Exception:
        return None


def _get_triage_route(item: IngestItem) -> Optional[str]:
    """Return the triage primary_route for an item, or None if no triage exists."""
    try:
        triage = item.triage_result
        if triage:
            return triage.primary_route
    except Exception:
        pass
    return None


def has_calendar_classification(item: IngestItem) -> bool:
    """Check if item is classified with a calendar/appointment/reminder intent or subject."""
    triage_route = _get_triage_route(item)
    if triage_route == "event":
        return True

    proj = _get_projection(item)
    if not proj:
        return False
    intent = proj.primary_intent_key or ""
    subject = proj.primary_subject_key or ""
    if _matches_parser_route("calendar", "intent", intent):
        return True
    if _matches_parser_route("calendar", "subject", subject):
        return True
    return False


def has_list_classification(item: IngestItem) -> bool:
    """Check if item is classified as a list."""
    triage_route = _get_triage_route(item)
    if triage_route == "collection":
        return True

    proj = _get_projection(item)
    if not proj:
        return False
    intent = proj.primary_intent_key or ""
    subject = proj.primary_subject_key or ""
    if _matches_parser_route("list", "intent", intent):
        return True
    if _matches_parser_route("list", "subject", subject):
        return True
    return False


def has_financial_classification(item: IngestItem) -> bool:
    """Check if item is classified as financial."""
    triage_route = _get_triage_route(item)
    if triage_route == "finance":
        return True

    proj = _get_projection(item)
    if not proj:
        return False
    intent = proj.primary_intent_key or ""
    subject = proj.primary_subject_key or ""
    if _matches_parser_route("financial", "intent", intent):
        return True
    if _matches_parser_route("financial", "subject", subject):
        return True
    return False


def has_todo_classification(item: IngestItem) -> bool:
    """Check if item is classified with a todo/task intent."""
    triage_route = _get_triage_route(item)
    if triage_route == "task":
        return True

    proj = _get_projection(item)
    if not proj:
        return False
    intent = proj.primary_intent_key or ""
    subject = proj.primary_subject_key or ""
    if _matches_parser_route("todo", "intent", intent):
        return True
    if _matches_parser_route("todo", "subject", subject):
        return True
    return False


# ---------------------------------------------------------------------------
# Parser route resolution
# ---------------------------------------------------------------------------

def get_parser_routes_for_run(run: ItemClassificationRun) -> List[str]:
    """
    Determine which downstream parsers to trigger based on classification
    selections and the TaxonomyParserRoute mapping table.

    Returns list of parser_action strings (e.g. ['calendar', 'financial']).
    """
    import fnmatch
    from .models import TaxonomyParserRoute

    routes = TaxonomyParserRoute.objects.filter(is_active=True).order_by("-priority")
    selections = run.selections.values_list("dimension", "path_key")

    selection_map: Dict[str, List[str]] = {}
    for dim, key in selections:
        selection_map.setdefault(dim, []).append(key)

    matched_actions: List[str] = []
    seen_actions: set = set()

    for route in routes:
        dim_keys = selection_map.get(route.dimension_match, [])
        for key in dim_keys:
            if fnmatch.fnmatch(key, route.key_pattern):
                if route.parser_action not in seen_actions:
                    matched_actions.append(route.parser_action)
                    seen_actions.add(route.parser_action)
                break

    return matched_actions
