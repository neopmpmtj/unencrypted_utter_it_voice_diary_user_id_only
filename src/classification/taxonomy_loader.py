"""
Taxonomy Loader — v14

Query helpers that build the allowed_taxonomy and entity_hints payloads
required by the LLM classifier prompts (PRD 9.1).
"""

import logging
from typing import Any, Dict, List, Optional

from .models import EntityCatalog, EntityType, TaxonomyNode, TaxonomyPack

logger = logging.getLogger(__name__)


def load_allowed_taxonomy(
    pack: str = TaxonomyPack.PERSONAL,
) -> Dict[str, List[str]]:
    """
    Query TaxonomyNode for active, selectable nodes and return them grouped
    by dimension. Includes both the requested pack and the shared pack.

    Returns:
        {
            "subject": ["personal.health.appointment.dentist", ...],
            "intent": ["intent.capture.note.freeform", ...],
            "context": [...],
            "time": [...],
            "governance": [...],
        }
    """
    qs = TaxonomyNode.objects.filter(
        is_active=True,
        is_selectable=True,
        taxonomy_pack__in=[pack, TaxonomyPack.SHARED],
    )

    taxonomy: Dict[str, List[str]] = {
        "subject": [],
        "intent": [],
        "context": [],
        "time": [],
        "governance": [],
    }

    for dim, key in qs.values_list("dimension", "key").order_by("dimension", "sort_order", "key"):
        if dim in taxonomy:
            taxonomy[dim].append(key)

    return taxonomy


def load_entity_hints(user_id) -> Dict[str, List[str]]:
    """
    Query EntityCatalog for known entities scoped to a specific user,
    to provide as hints to the classifier.

    Returns:
        {
            "contacts": ["João", "Maria", ...],
            "projects": ["VoiceDiary", ...],
            "organizations": ["Worten", ...],
        }
    """
    hints: Dict[str, List[str]] = {
        "contacts": [],
        "projects": [],
        "organizations": [],
    }

    entity_type_map = {
        EntityType.PERSON: "contacts",
        EntityType.CONTACT: "contacts",
        EntityType.PROJECT: "projects",
        EntityType.ORGANIZATION: "organizations",
        EntityType.VENDOR: "organizations",
        EntityType.CLIENT: "organizations",
    }

    qs = EntityCatalog.objects.filter(
        user_id=user_id,
        is_active=True,
    ).values_list("entity_type", "canonical_name")

    for etype, name in qs:
        bucket = entity_type_map.get(etype)
        if bucket:
            hints[bucket].append(name)

    return hints


def build_classification_payload(
    item_id: str,
    user_id: str,
    taxonomy_pack: str,
    provider: str,
    item_type: str,
    template_type: str,
    occurred_at: Optional[str],
    ingested_at: Optional[str],
    detected_language: str,
    title: str,
    content_text: str,
    summary_text: str,
    allowed_taxonomy: Dict[str, List[str]],
    entity_hints: Dict[str, List[str]],
) -> Dict[str, Any]:
    """
    Build the classification input payload matching PRD 9.1 input shape.
    """
    from .config_taxonomy_classifier import SELECTION_LIMITS

    return {
        "ingest_item_id": item_id,
        "user_id": user_id,
        "taxonomy_pack": taxonomy_pack,
        "provider": provider,
        "item_type": item_type,
        "template_type": template_type,
        "occurred_at": occurred_at or "",
        "ingested_at": ingested_at or "",
        "detected_language": detected_language,
        "title": title,
        "content_text": content_text,
        "summary_text": summary_text,
        "allowed_taxonomy": allowed_taxonomy,
        "entity_hints": entity_hints,
        "selection_limits": SELECTION_LIMITS,
    }
