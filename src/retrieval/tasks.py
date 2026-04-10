"""
Retrieval Indexing Tasks — v14

Two-stage pipeline:
  Stage 1 (prep)   — decrypt data, write to cache, enqueue Stage 2
  Stage 2 (process) — summarise via LLM, compute embedding, upsert ItemRetrievalProjection
"""

import json
import logging
from typing import Any, Dict, List, Optional

from celery import shared_task
from decouple import config
from src.ingestion.models import FileRole, IngestItem
from src.ingestion.tasks import log_api_usage
from src.common.model_picker import get_llm_config

from .cache import delete_prep_cache, read_prep_cache, write_prep_cache
from .utils import hmac_token

logger = logging.getLogger(__name__)


def _get_primary_classification(item: IngestItem) -> str:
    """Return the primary subject key from the item's retrieval projection."""
    try:
        proj = item.retrieval_projection
        return proj.primary_subject_key or ""
    except Exception:
        return ""


def _plaintext_list_items(item: IngestItem) -> List[str]:
    """ListItem.text for all successful list records on this item."""
    texts: List[str] = []
    for lr in item.list_records.filter(status="success").prefetch_related("items"):
        for li in lr.items.all():
            if li.text:
                texts.append(li.text)
    return texts


def _plaintext_todo_items(item: IngestItem) -> List[str]:
    """TodoItem.text for all successful todo records on this item."""
    texts: List[str] = []
    try:
        for tr in item.todorecord_records.filter(status="success").prefetch_related("items"):
            for ti in tr.items.all():
                if ti.text:
                    texts.append(ti.text)
    except Exception:
        pass
    return texts


def _build_financial_items(item: IngestItem) -> List[Dict[str, Any]]:
    """Build plaintext financial-item dicts (fields are stored unencrypted)."""
    result: List[Dict[str, Any]] = []
    for fr in item.financial_records.filter(status="success").prefetch_related("items"):
        for fi in fr.items.all():
            result.append({
                "merchant": fi.merchant,
                "category": fi.category,
                "description": fi.description,
                "amount": str(fi.amount),
                "currency": fi.currency,
            })
    return result


def _attachment_info(item: IngestItem):
    """Return (has_attachment: bool, attachment_types: list[str])."""
    files = item.files.filter(role=FileRole.ATTACHMENT)
    types = list(files.values_list("mime_type", flat=True).distinct())
    return bool(types), types


# ---------------------------------------------------------------------------
# Stage 1: Prep
# ---------------------------------------------------------------------------

@shared_task(bind=True, max_retries=2, queue="index_prep")
def index_entry_prep_task(self, entry_id: str):
    """Decrypt entry data and write to cache, then enqueue process task."""
    logger.info("index_entry_prep_task started for %s", entry_id)
    try:
        item = (
            IngestItem.objects
            .select_related("user")
            .prefetch_related(
                "list_records__items",
                "financial_records__items",
                "todorecord_records__items",
                "files",
            )
            .get(id=entry_id)
        )
    except IngestItem.DoesNotExist:
        logger.error("IngestItem %s not found, skipping index prep", entry_id)
        return

    if item.is_deleted:
        logger.info("IngestItem %s is deleted, skipping index prep", entry_id)
        return

    try:
        content_text = item.content_text or ""
        title = item.title or ""

        list_items = _plaintext_list_items(item)
        financial_items = _build_financial_items(item)
        todo_items = _plaintext_todo_items(item)
        classification = _get_primary_classification(item)
        has_attachment, attachment_types = _attachment_info(item)

        payload = {
            "entry_id": str(item.id),
            "user_id": item.user_id,
            "content_text": content_text,
            "title": title,
            "classification": classification,
            "list_items": list_items,
            "financial_items": financial_items,
            "todo_items": todo_items,
            "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
            "has_attachment": has_attachment,
            "attachment_types": attachment_types,
        }

        cache_path = write_prep_cache(str(item.id), str(item.user_id), payload)
        index_entry_process_task.delay(str(item.id), cache_path)
        logger.info("index_entry_prep_task completed for %s, enqueued process", entry_id)

    except Exception as exc:
        logger.error("index_entry_prep_task failed for %s: %s", entry_id, exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


# ---------------------------------------------------------------------------
# Stage 2: Process
# ---------------------------------------------------------------------------

def _compute_embedding(text: str) -> tuple[Optional[List[float]], Dict[str, int]]:
    """Compute embedding using OpenAI. Returns (embedding, usage_dict) or (None, {})."""
    if not text.strip():
        return None, {}
    try:
        api_key = _get_openai_key()
        if not api_key:
            logger.warning("No OpenAI API key, skipping embedding")
            return None, {}
        model = get_llm_config("embedding").get("model", "text-embedding-3-small")
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=30.0)
        resp = client.embeddings.create(
            model=model,
            input=text[:8000],
        )
        usage = getattr(resp, "usage", None)
        usage_dict = {
            "input": getattr(usage, "total_tokens", 0) or getattr(usage, "prompt_tokens", 0) if usage else 0,
            "output": 0,
            "total": getattr(usage, "total_tokens", 0) or getattr(usage, "prompt_tokens", 0) if usage else 0,
        }
        return resp.data[0].embedding, usage_dict
    except Exception as exc:
        logger.error("Embedding computation failed: %s", exc)
        return None, {}


def _get_openai_key() -> str:
    try:
        from src.common.config import get_config
        return get_config().ai.openai_api_key
    except Exception:
        return config("AI_OPENAI_API_KEY", default="") or config("OPENAI_API_KEY", default="")


def _build_token_index(keywords, list_items, financial_items, content_text: str = "", todo_items=None) -> List[str]:
    """Build array of HMAC hashes for blind keyword lookup."""
    tokens: set[str] = set()
    for kw in keywords:
        if isinstance(kw, str) and kw.strip():
            tokens.add(kw.strip().lower())
    for li in list_items:
        if isinstance(li, str):
            for word in li.split():
                w = word.strip().lower()
                if len(w) > 2:
                    tokens.add(w)
    for fi in financial_items:
        if isinstance(fi, dict):
            for field in ("merchant", "category"):
                val = fi.get(field, "")
                if val:
                    tokens.add(val.strip().lower())
    for ti in (todo_items or []):
        if isinstance(ti, str):
            for word in ti.split():
                w = word.strip().lower()
                if len(w) > 2:
                    tokens.add(w)
    content_snippet = (content_text or "")[:500]
    for word in content_snippet.split():
        w = word.strip().lower()
        if len(w) > 2:
            tokens.add(w)
    return [hmac_token(t) for t in tokens]


@shared_task(bind=True, max_retries=2, queue="index_process")
def index_entry_process_task(self, entry_id: str, cache_path: str):
    """Read cached prep data, summarise, embed, and upsert ItemRetrievalProjection."""
    logger.info("index_entry_process_task started for %s", entry_id)

    data = read_prep_cache(cache_path)
    if data is None:
        logger.warning("Cache miss for %s, attempting inline decrypt fallback", entry_id)
        index_entry_prep_task.delay(entry_id)
        return

    try:
        from src.retrieval.models import ItemRetrievalProjection
        from src.summarizer.services import summarize_for_search

        list_items_flat = " | ".join(data.get("list_items", []))
        todo_items_flat = " | ".join(data.get("todo_items", []))
        fin_items = data.get("financial_items", [])
        financial_items_flat = " | ".join(
            f"{fi.get('amount', '')} {fi.get('currency', '')} {fi.get('merchant', '')} {fi.get('category', '')}"
            for fi in fin_items
        )

        llm_result: Dict[str, Any] = {
            "summary": "", "keywords": [], "usage": {},
        }
        try:
            llm_result = summarize_for_search(
                content_text=data.get("content_text", ""),
                title=data.get("title", ""),
                classification=data.get("classification", ""),
                list_items=data.get("list_items", []),
                financial_items=data.get("financial_items", []),
            )
        except Exception as summarizer_exc:
            logger.warning(
                "Summariser failed for %s, storing partial index: %s",
                entry_id, summarizer_exc,
            )

        embedding_text = " ".join(filter(None, [
            llm_result.get("summary", ""),
            " ".join(llm_result.get("keywords", [])),
            list_items_flat,
            financial_items_flat,
            todo_items_flat,
            data.get("content_text", "")[:2000],
        ]))
        embedding, embed_usage = _compute_embedding(embedding_text)

        user_id = data.get("user_id")
        user = None
        ingest_item = None
        if user_id:
            try:
                from src.accounts.models import CustomUser
                user = CustomUser.objects.filter(id=user_id).first()
            except Exception:
                pass
        try:
            ingest_item = IngestItem.objects.filter(id=data.get("entry_id")).first()
        except Exception:
            pass
        if user:
            summarizer_usage = llm_result.get("usage", {})
            if summarizer_usage and (summarizer_usage.get("input", 0) + summarizer_usage.get("output", 0) > 0):
                summarizer_model = get_llm_config("semantic_search_summarizer").get("model", "")
                if summarizer_model:
                    log_api_usage(
                        user, summarizer_model, "input_tokens",
                        summarizer_usage.get("input", 0),
                        ingest_item=ingest_item, origin="index_entry_process_task",
                    )
                    log_api_usage(
                        user, summarizer_model, "output_tokens",
                        summarizer_usage.get("output", 0),
                        ingest_item=ingest_item, origin="index_entry_process_task",
                    )
            if embed_usage.get("total"):
                embed_model = get_llm_config("embedding").get("model", "text-embedding-3-small")
                log_api_usage(
                    user, embed_model, "input_tokens",
                    embed_usage.get("total", 0),
                    ingest_item=ingest_item, origin="index_entry_process_task",
                )

        token_index = _build_token_index(
            llm_result.get("keywords", []),
            data.get("list_items", []),
            data.get("financial_items", []),
            data.get("content_text", ""),
            todo_items=data.get("todo_items", []),
        )

        from django.utils.dateparse import parse_datetime
        occurred_at = None
        if data.get("occurred_at"):
            occurred_at = parse_datetime(data["occurred_at"])

        summary_plain = llm_result.get("summary", "")
        keywords_plain = llm_result.get("keywords", [])
        content_plain = data.get("content_text", "")[:10000]
        embedding_ready_plain = embedding_text[:10000]

        enc_summary = summary_plain
        enc_keywords = json.dumps(keywords_plain, default=str)
        enc_list_items = list_items_flat
        enc_financial_items = financial_items_flat
        enc_todo_items = todo_items_flat
        enc_content = content_plain
        enc_summary_text = summary_plain
        enc_embedding_ready = embedding_ready_plain

        defaults = {
            "user_id": data.get("user_id"),
            "occurred_at": occurred_at,
            "summary": enc_summary,
            "keywords": enc_keywords,
            "list_items_flat": enc_list_items,
            "financial_items_flat": enc_financial_items,
            "todo_items_flat": enc_todo_items,
            "embedding": embedding,
            "token_index": token_index,
            "has_attachment": data.get("has_attachment", False),
            "attachment_types": data.get("attachment_types", []),
            "content_text_searchable": enc_content,
            "summary_text_searchable": enc_summary_text,
            "embedding_ready_text": enc_embedding_ready,
        }

        ItemRetrievalProjection.objects.update_or_create(
            ingest_item_id=data["entry_id"],
            defaults=defaults,
        )

        delete_prep_cache(cache_path)
        logger.info("index_entry_process_task completed for %s", entry_id)

    except Exception as exc:
        logger.error("index_entry_process_task failed for %s: %s", entry_id, exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
