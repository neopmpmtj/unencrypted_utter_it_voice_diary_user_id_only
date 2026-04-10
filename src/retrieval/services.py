"""
Retrieval Services — v14

RAG query pipeline: embed question, vector search ItemRetrievalProjection,
build LLM context with taxonomy/entity data and conversation history, generate answer.
"""

import json
import logging
import re
import time

from decouple import config
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pgvector.django import CosineDistance

from src.common.model_picker import get_llm_config
from .config_retrieval import (
    CANDIDATE_POOL_SIZE,
    CONTEXT_TOP_N,
    HISTORY_WINDOW,
    MAX_DISTANCE,
    MAX_RETRIES,
    RANK_WEIGHT_ENTITY,
    RANK_WEIGHT_KEYWORD,
    RANK_WEIGHT_TEXT,
    RANK_WEIGHT_TOKEN,
    RANK_WEIGHT_VECTOR,
    RETRY_DELAY,
    SINGLE_BEST_CONTEXT_INSTRUCTION,
    SYSTEM_PROMPT,
    TOKEN_INDEX_ENABLED,
)
from .models import AssistantChatMessage, ChatSession, ItemRetrievalProjection, UserChatMessage

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    from src.common.ai_client import get_openai_api_key
    return get_openai_api_key()


def _get_chat_model_config() -> Dict[str, Any]:
    try:
        cfg = get_llm_config("diary_chat")
        if cfg:
            return cfg
    except Exception:
        pass
    return {"model": "gpt-4o", "temperature": 0.3, "max_tokens": 2048}


def _embed_text(text: str, api_key: str) -> tuple[Optional[List[float]], Dict[str, int]]:
    """Compute embedding for query text. Returns (embedding, usage_dict) or (None, {})."""
    if not text.strip():
        return None, {}
    try:
        cfg = get_llm_config("embedding")
        model = cfg.get("model", "text-embedding-3-small")
        client = OpenAI(api_key=api_key, timeout=30.0)
        resp = client.embeddings.create(
            model=model,
            input=text[:8000],
        )
        usage = getattr(resp, "usage", None)
        usage_dict = {
            "input": getattr(usage, "total_tokens", 0) or getattr(usage, "prompt_tokens", 0),
            "output": 0,
            "total": getattr(usage, "total_tokens", 0) or getattr(usage, "prompt_tokens", 0),
        } if usage else {"input": 0, "output": 0, "total": 0}
        return resp.data[0].embedding, usage_dict
    except Exception as exc:
        logger.error("Query embedding failed: %s", exc)
        return None, {}


def _vector_search(
    user_id: str,
    query_embedding: List[float],
    top_k: int = CANDIDATE_POOL_SIZE,
) -> List[ItemRetrievalProjection]:
    """Find the top-k most similar projection rows by cosine distance."""
    candidates = list(
        ItemRetrievalProjection.objects
        .filter(user_id=user_id, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", query_embedding))
        .order_by("distance")[:top_k * 3]
    )
    return [r for r in candidates if float(r.distance) < MAX_DISTANCE][:top_k]


def _parse_json_list_field(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _hydrate_projection(proj: "ItemRetrievalProjection") -> None:
    """
    Normalize projection fields in-place (memory only). JSON text fields become lists.
    """
    proj.summary = proj.summary or ""
    proj.keywords = _parse_json_list_field(proj.keywords)
    proj.entity_names_normalized = _parse_json_list_field(proj.entity_names_normalized)
    proj.entity_roles = _parse_json_list_field(proj.entity_roles)
    proj.list_items_flat = proj.list_items_flat or ""
    proj.financial_items_flat = proj.financial_items_flat or ""
    proj.todo_items_flat = getattr(proj, "todo_items_flat", None) or ""
    proj.content_text_searchable = proj.content_text_searchable or ""
    proj.summary_text_searchable = proj.summary_text_searchable or ""


def _extract_query_entities_and_tokens(user_id: str, query: str) -> tuple[set, set]:
    """
    Single EntityCatalog pass: extract entity names and token HMACs from the query.
    Returns (query_entities, query_token_hmacs).
    """
    query_entities: set = set()
    query_token_hmacs: set = set()
    if not query or not query.strip():
        return query_entities, query_token_hmacs
    try:
        from src.classification.models import EntityCatalog

        from .utils import hmac_token

        query_lower = query.strip().lower()
        for row in EntityCatalog.objects.filter(
            user_id=user_id, is_active=True
        ).values_list("normalized_name", "canonical_name"):
            norm, canon = row[0], row[1] or ""
            if norm and norm in query_lower:
                query_entities.add(norm)
                if len(norm) > 2:
                    query_token_hmacs.add(hmac_token(norm))
            if canon and canon.strip():
                canon_lower = canon.strip().lower()
                if canon_lower != norm and canon_lower in query_lower:
                    query_entities.add(norm)
                    if len(norm) > 2:
                        query_token_hmacs.add(hmac_token(norm))
        for word in query_lower.split():
            w = word.strip()
            if len(w) > 2:
                query_token_hmacs.add(hmac_token(w))
        return query_entities, query_token_hmacs
    except Exception as exc:
        logger.debug("Entity/token extraction failed: %s", exc)
        return set(), set()


def _token_retrieval(
    user_id: str,
    query_token_hmacs: set,
    exclude_ids: set,
    limit: int = CANDIDATE_POOL_SIZE,
) -> List[ItemRetrievalProjection]:
    """Find entries by token_index overlap, independent of vector search.

    token_index is a JSONField (jsonb array), so we use __contains per HMAC
    combined with OR to simulate array overlap.
    """
    if not query_token_hmacs:
        return []
    from django.db.models import Q

    q = Q()
    for h in query_token_hmacs:
        q |= Q(token_index__contains=[h])
    candidates = list(
        ItemRetrievalProjection.objects
        .filter(q, user_id=user_id)
        .exclude(ingest_item_id__in=exclude_ids)[:limit * 2]
    )

    def overlap_count(proj):
        return -len(set(proj.token_index or []) & query_token_hmacs)

    candidates.sort(key=overlap_count)
    return candidates[:limit]


def _normalize_query_words(user_message: str) -> set:
    """Lowercase tokens of length > 2 from the question (alphanumeric runs)."""
    if not user_message or not user_message.strip():
        return set()
    words = re.findall(r"[a-z0-9]+", user_message.lower())
    return {w for w in words if len(w) > 2}


def _taxonomy_tails_text(proj: "ItemRetrievalProjection") -> str:
    parts: List[str] = []
    for key in (
        getattr(proj, "primary_subject_key", None) or "",
        getattr(proj, "primary_intent_key", None) or "",
        getattr(proj, "primary_context_key", None) or "",
    ):
        key = (key or "").strip()
        if key:
            parts.append(key.rsplit(".", 1)[-1].lower())
    for attr in (
        "secondary_subject_keys",
        "secondary_intent_keys",
        "secondary_context_keys",
    ):
        raw = getattr(proj, attr, None)
        if not isinstance(raw, list):
            continue
        for k in raw:
            if isinstance(k, str) and k.strip():
                parts.append(k.strip().rsplit(".", 1)[-1].lower())
    return " ".join(parts)


def _text_haystack(proj: "ItemRetrievalProjection") -> str:
    """Lowercase text used for lexical overlap scoring."""
    kw = (proj.keywords or []) if isinstance(proj.keywords, list) else []
    kw_s = " ".join(str(k).lower() for k in kw)
    summary = (proj.summary or "").lower()
    content = (proj.content_text_searchable or "")[:2000].lower()
    lists = f"{(proj.list_items_flat or '').lower()} {(proj.financial_items_flat or '').lower()}"
    return " ".join([summary, kw_s, content, lists, _taxonomy_tails_text(proj)])


def _projection_composite_score(
    proj: "ItemRetrievalProjection",
    query_words: set,
    query_entities: set,
    query_token_hmacs: set,
) -> float:
    """
    Weighted blend of vector similarity, entity overlap, token HMAC overlap,
    keyword overlap, and raw text overlap.

    Token HMAC overlap is included only when TOKEN_INDEX_ENABLED is true and
    query_token_hmacs is non-empty — matching the gate on _token_retrieval so
    ranking does not reward token_index when the token channel is disabled.
    """
    total = 0.0

    if hasattr(proj, "distance") and proj.distance is not None:
        try:
            d = float(proj.distance)
        except (TypeError, ValueError):
            d = None
        if d is not None:
            total += RANK_WEIGHT_VECTOR * max(0.0, 1.0 - d)

    if query_entities:
        proj_entities = set((e or "").strip().lower() for e in (proj.entity_names_normalized or []))
        overlap = len(proj_entities & query_entities)
        total += RANK_WEIGHT_ENTITY * (overlap / max(1, len(query_entities)))

    if TOKEN_INDEX_ENABLED and query_token_hmacs:
        proj_tokens = set(proj.token_index or [])
        overlap_t = len(proj_tokens & query_token_hmacs)
        total += RANK_WEIGHT_TOKEN * (overlap_t / max(1, len(query_token_hmacs)))

    if query_words:
        keywords = proj.keywords if isinstance(proj.keywords, list) else []
        kw_hits = 0
        for qw in query_words:
            for k in keywords:
                if qw in str(k).lower():
                    kw_hits += 1
                    break
        total += RANK_WEIGHT_KEYWORD * (kw_hits / max(1, len(query_words)))

        hay = _text_haystack(proj)
        text_hits = sum(1 for w in query_words if w in hay)
        total += RANK_WEIGHT_TEXT * (text_hits / max(1, len(query_words)))

    return total


def _dedupe_projections_by_ingest(
    results: List[ItemRetrievalProjection],
) -> List[ItemRetrievalProjection]:
    seen = set()
    out: List[ItemRetrievalProjection] = []
    for proj in results:
        iid = proj.ingest_item_id
        if iid in seen:
            continue
        seen.add(iid)
        out.append(proj)
    return out


def _select_best_projections(
    results: List[ItemRetrievalProjection],
    user_message: str,
    query_entities: set,
    query_token_hmacs: set,
    n: int = CONTEXT_TOP_N,
) -> List[ItemRetrievalProjection]:
    """
    Deterministic multi-signal ranking; return the top n projections.
    Tie-breakers: higher composite score first, then lower vector distance (if any),
    then more recent occurred_at.
    """
    if not results or n <= 0:
        return []
    query_words = _normalize_query_words(user_message)

    def sort_key(proj: ItemRetrievalProjection):
        score = _projection_composite_score(
            proj, query_words, query_entities, query_token_hmacs,
        )
        if hasattr(proj, "distance") and proj.distance is not None:
            try:
                dist = float(proj.distance)
            except (TypeError, ValueError):
                dist = float("inf")
        else:
            dist = float("inf")
        ts = 0.0
        if getattr(proj, "occurred_at", None) is not None:
            try:
                ts = proj.occurred_at.timestamp()
            except (OSError, TypeError, ValueError):
                ts = 0.0
        return (-score, dist, -ts)

    ordered = sorted(results, key=sort_key)
    return ordered[:n]


def _build_context(results: List[ItemRetrievalProjection]) -> str:
    """Format retrieved projection rows into a context string for the LLM."""
    if not results:
        return "(No relevant diary entries found.)"

    parts = []
    for i, proj in enumerate(results, 1):
        date_str = proj.occurred_at.strftime("%Y-%m-%d %H:%M") if proj.occurred_at else "unknown date"
        subject = proj.primary_subject_key.rsplit(".", 1)[-1] if proj.primary_subject_key else "general"
        block = f"[Entry {i}] Date: {date_str} | Subject: {subject}"
        if proj.primary_intent_key:
            block += f" | Intent: {proj.primary_intent_key.rsplit('.', 1)[-1]}"
        block += "\n"
        if proj.summary:
            block += f"Summary: {proj.summary}\n"
        if proj.keywords:
            block += f"Keywords: {', '.join(proj.keywords)}\n"
        content_snippet = (proj.content_text_searchable or "").strip()[:800]
        if content_snippet:
            block += f"Content: {content_snippet}\n"
        if proj.entity_names_normalized:
            block += f"Entities: {', '.join(proj.entity_names_normalized)}\n"
        if proj.list_items_flat:
            block += f"List items: {proj.list_items_flat}\n"
        if proj.financial_items_flat:
            block += f"Financial: {proj.financial_items_flat}\n"
        parts.append(block)

    return "\n".join(parts)


def _build_source_refs(results: List[ItemRetrievalProjection]) -> List[Dict[str, Any]]:
    """Build source citation dicts from projection results."""
    sources = []
    for proj in results:
        parts = []
        if proj.primary_subject_key:
            parts.append(proj.primary_subject_key.rsplit(".", 1)[-1])
        if proj.primary_intent_key:
            parts.append(proj.primary_intent_key.rsplit(".", 1)[-1])
        classification = " | ".join(parts) if parts else None

        sources.append({
            "entry_id": str(proj.ingest_item_id),
            "summary": (proj.summary[:120] + "...") if len(proj.summary) > 120 else proj.summary,
            "occurred_at": proj.occurred_at.isoformat() if proj.occurred_at else None,
            "subject": proj.primary_subject_key or "",
            "intent": proj.primary_intent_key or "",
            "classification": classification,
        })
    return sources


def get_session_messages_ordered(session: ChatSession, _user_id: Optional[int] = None) -> List[tuple]:
    """Return [(role, msg_dict), ...] ordered by sequence_index (plaintext)."""
    users = list(
        session.user_messages.order_by("sequence_index").values(
            "id", "content", "sequence_index", "created_at"
        )
    )
    assistants = list(
        session.assistant_messages.order_by("sequence_index").values(
            "id", "content", "source_entries", "metadata", "sequence_index", "created_at"
        )
    )

    for a in assistants:
        se = a.get("source_entries")
        if isinstance(se, str) and se:
            try:
                a["source_entries"] = json.loads(se)
            except json.JSONDecodeError:
                a["source_entries"] = []
        elif se is None:
            a["source_entries"] = []

    merged = (
        [("user", u) for u in users]
        + [("assistant", a) for a in assistants]
    )
    merged.sort(key=lambda x: x[1]["sequence_index"])
    return merged


def _get_conversation_history(session: ChatSession, user_id: Optional[int] = None) -> List[Dict[str, str]]:
    """Load the last N messages from a session for multi-turn context."""
    ordered = get_session_messages_ordered(session, _user_id=user_id)
    return [{"role": role, "content": m["content"]} for role, m in ordered[-HISTORY_WINDOW:]]


def _get_system_prompt(user_language: Optional[str] = None) -> str:
    """Build system prompt, optionally with explicit user language instruction."""
    prompt = SYSTEM_PROMPT
    if CONTEXT_TOP_N == 1:
        marker = "retrieved context below.\n\n"
        if marker in prompt:
            prompt = prompt.replace(marker, marker + SINGLE_BEST_CONTEXT_INSTRUCTION, 1)
        else:
            prompt = SINGLE_BEST_CONTEXT_INSTRUCTION + prompt
    if user_language:
        return (
            f"The user prefers responses in {user_language}. Always respond in that language.\n\n"
            + prompt
        )
    return prompt


def query_diary(
    user_id: str,
    user,
    session_id: Optional[str],
    user_message: str,
    user_language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main RAG pipeline: embed question, search, build context, answer.

    Returns:
        {
            "answer": str,
            "sources": [{entry_id, summary, occurred_at, subject, intent}],
            "session_id": str,
        }
    """
    api_key = _get_api_key()
    if not api_key:
        return {
            "answer": "OpenAI API key is not configured.",
            "sources": [],
            "session_id": session_id,
            "usage": {"embedding": {}, "chat": {}},
        }

    user_id: Optional[int] = getattr(user, "id", None) or getattr(user, "pk", None)

    session = None
    try:
        if session_id:
            session = ChatSession.objects.filter(
                id=session_id, user=user,
            ).first()
        if not session:
            title_plain = (user_message[:80] if user_message else "New conversation")
            session = ChatSession.objects.create(
                user=user, title=title_plain,
            )
    except Exception:
        logger.exception("Failed to get/create chat session")
        return {
            "answer": "Sorry, a database error occurred. Please try again.",
            "sources": [],
            "session_id": None,
            "usage": {"embedding": {}, "chat": {}},
        }

    def _next_sequence(sess):
        from django.db.models import Max
        umax = UserChatMessage.objects.filter(session=sess).aggregate(Max("sequence_index"))["sequence_index__max"]
        amax = AssistantChatMessage.objects.filter(session=sess).aggregate(Max("sequence_index"))["sequence_index__max"]
        return 1 + max((umax or 0), (amax or 0))

    seq = _next_sequence(session)
    try:
        UserChatMessage.objects.create(
            session=session, content=user_message, sequence_index=seq,
        )
    except Exception:
        logger.exception("Failed to store user message for session %s", session.pk)

    query_embedding, embed_usage = _embed_text(user_message, api_key)
    query_entities, query_token_hmacs = _extract_query_entities_and_tokens(
        user_id, user_message
    )

    results: List[ItemRetrievalProjection] = []
    if query_embedding:
        try:
            results = _vector_search(user_id, query_embedding)
            if not results:
                logger.info(
                    "Vector search returned 0 results for user %s (query length=%d)",
                    user_id, len(user_message),
                )
        except Exception:
            logger.exception("Vector search failed for user %s", user_id)

    if TOKEN_INDEX_ENABLED and query_token_hmacs:
        vector_ids = {proj.ingest_item_id for proj in results}
        token_results = _token_retrieval(user_id, query_token_hmacs, vector_ids)
        if token_results:
            logger.info(
                "Token retrieval added %d entries for user %s",
                len(token_results), user_id,
            )
            results.extend(token_results)

    if results:
        for proj in results:
            _hydrate_projection(proj)
        results = _dedupe_projections_by_ingest(results)
        results = results[:CANDIDATE_POOL_SIZE]
        results = _select_best_projections(
            results, user_message, query_entities, query_token_hmacs,
        )

    context = _build_context(results)
    sources = _build_source_refs(results)

    history = _get_conversation_history(session, user_id=user_id)
    if history and history[-1]["role"] == "user":
        history = history[:-1]

    system_prompt = _get_system_prompt(user_language)
    llm_messages = [{"role": "system", "content": system_prompt}]
    llm_messages.extend(history)
    llm_messages.append({
        "role": "user",
        "content": f"Context from diary entries:\n{context}\n\nQuestion: {user_message}",
    })

    model_cfg = _get_chat_model_config()
    client = OpenAI(api_key=api_key, timeout=60.0)
    delay = RETRY_DELAY
    answer = ""
    chat_usage: Dict[str, int] = {"input": 0, "output": 0, "total": 0}

    for attempt in range(MAX_RETRIES + 1):
        try:
            if attempt > 0:
                time.sleep(delay)
                delay = min(delay * 2, 30.0)

            resp = client.chat.completions.create(
                model=model_cfg.get("model", "gpt-4o"),
                messages=llm_messages,
                temperature=model_cfg.get("temperature", 0.3),
                max_tokens=model_cfg.get("max_tokens", 2048),
            )
            answer = resp.choices[0].message.content.strip()
            u = resp.usage
            if u:
                chat_usage = {
                    "input": getattr(u, "prompt_tokens", 0),
                    "output": getattr(u, "completion_tokens", 0),
                    "total": getattr(u, "total_tokens", 0),
                }
            break
        except Exception as exc:
            if attempt < MAX_RETRIES:
                logger.warning("Diary chat LLM call failed (attempt %d): %s", attempt + 1, exc)
            else:
                logger.error("Diary chat LLM failed after %d attempts: %s", MAX_RETRIES + 1, exc)
                answer = "Sorry, I could not generate an answer right now. Please try again."

    seq = _next_sequence(session)
    try:
        AssistantChatMessage.objects.create(
            session=session,
            content=answer,
            source_entries=json.dumps(sources, default=str),
            sequence_index=seq,
        )
        session.save(update_fields=["updated_at"])
    except Exception:
        logger.exception("Failed to store assistant message for session %s", session.pk)

    return {
        "answer": answer,
        "sources": sources,
        "session_id": str(session.id),
        "usage": {
            "embedding": embed_usage,
            "chat": chat_usage,
        },
    }
