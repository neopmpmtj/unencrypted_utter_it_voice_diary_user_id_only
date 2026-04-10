"""
Retrieval Projection Refresh — v14

Reads the latest successful classification run and its selections/entity links,
then upserts the taxonomy fields in ItemRetrievalProjection.
"""

import logging
from typing import Optional

from django.utils import timezone

from src.classification.models import (
    ClassificationRunStatus,
    ItemClassificationRun,
    TaxonomyDimension,
)
import json

logger = logging.getLogger(__name__)


def refresh_retrieval_projection(
    ingest_item_id: str,
    run: Optional[ItemClassificationRun] = None,
):
    """
    Upsert taxonomy/entity/governance fields in ItemRetrievalProjection
    from the latest successful classification run.

    If `run` is provided, uses it directly. Otherwise queries for the
    latest completed run for the given item.
    """
    from .models import ItemRetrievalProjection

    if run is None:
        run = (
            ItemClassificationRun.objects
            .filter(
                ingest_item_id=ingest_item_id,
                status=ClassificationRunStatus.COMPLETED,
            )
            .order_by("-created_at")
            .first()
        )

    if run is None:
        logger.debug("No completed classification run for item %s, skipping projection refresh", ingest_item_id)
        return

    selections = run.selections.select_related("taxonomy_node").all()
    entity_links = run.entity_links.select_related("entity").all()

    primary_subject = ""
    secondary_subjects = []
    primary_intent = ""
    secondary_intents = []
    primary_context = ""
    secondary_contexts = []
    time_keys = []
    governance_key = ""

    for sel in selections:
        if sel.dimension == TaxonomyDimension.SUBJECT:
            if sel.is_primary:
                primary_subject = sel.path_key
            else:
                secondary_subjects.append(sel.path_key)
        elif sel.dimension == TaxonomyDimension.INTENT:
            if sel.is_primary:
                primary_intent = sel.path_key
            else:
                secondary_intents.append(sel.path_key)
        elif sel.dimension == TaxonomyDimension.CONTEXT:
            if sel.is_primary:
                primary_context = sel.path_key
            else:
                secondary_contexts.append(sel.path_key)
        elif sel.dimension == TaxonomyDimension.TIME:
            time_keys.append(sel.path_key)
        elif sel.dimension == TaxonomyDimension.GOVERNANCE:
            if sel.is_primary:
                governance_key = sel.path_key

    entity_ids = []
    entity_names = []
    entity_roles = []
    for link in entity_links:
        if link.entity_id:
            entity_ids.append(str(link.entity_id))
        entity_names.append(link.normalized_mention)
        if link.role:
            entity_roles.append(link.role)

    enc_entity_names = json.dumps(entity_names, default=str)
    enc_entity_roles = json.dumps(entity_roles, default=str)

    is_sensitive = "sensitive" in governance_key.lower() if governance_key else False

    actionability = {}
    raw_output = run.raw_verifier_output_json or run.raw_model_output_json
    if isinstance(raw_output, dict):
        actionability = raw_output.get("actionability", {})
    is_actionable = bool(actionability.get("is_actionable", False)) if actionability else False

    defaults = {
        "user_id": run.user_id,
        "latest_classification_run": run,
        "primary_subject_key": primary_subject,
        "secondary_subject_keys": secondary_subjects,
        "primary_intent_key": primary_intent,
        "secondary_intent_keys": secondary_intents,
        "primary_context_key": primary_context,
        "secondary_context_keys": secondary_contexts,
        "time_keys": time_keys,
        "governance_key": governance_key,
        "entity_ids": entity_ids,
        "entity_names_normalized": enc_entity_names,
        "entity_roles": enc_entity_roles,
        "overall_confidence": run.overall_confidence,
        "is_actionable": is_actionable,
        "is_sensitive": is_sensitive,
        "last_classified_at": timezone.now(),
    }

    proj, created = ItemRetrievalProjection.objects.update_or_create(
        ingest_item_id=ingest_item_id,
        defaults=defaults,
    )

    if created and not proj.user_id:
        proj.user_id = run.user_id
        proj.save(update_fields=["user_id"])

    logger.info(
        "Refreshed retrieval projection for item %s (created=%s)",
        ingest_item_id, created,
    )
