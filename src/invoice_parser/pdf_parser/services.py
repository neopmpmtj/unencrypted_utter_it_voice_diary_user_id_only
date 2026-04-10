"""
PDF invoice parsing service.

Downloads PDF attachments from Gmail invoice emails and sends them
to OpenAI's Responses API for structured data extraction.
"""

import base64
import json

from openai import OpenAI
from decouple import config as env_config

from src.common.google_account.auth import verify_gmail_permissions
from src.common.google_account.gmail_services import (
    search_inbox_messages,
    message_has_attachments,
    get_pdf_attachments,
    get_or_create_label,
    add_label_to_message,
)
from src.common.logging_utils.logging_config import get_logger
from src.ingestion.tasks import log_api_usage

from .config import (
    PROMPT_TEMPLATE,
    PROCESSED_LABEL_NAME,
    TRIGGER_WORDS,
    get_pdf_parser_config,
    strip_markdown_json_fences,
)
from .persistence import persist_invoice_to_db

logger = get_logger("invoice_parser.pdf")


def _build_invoice_query() -> str:
    """Build Gmail search query from trigger words, excluding already-processed messages."""
    parts = [f"subject:{w}" for w in TRIGGER_WORDS]
    base = " OR ".join(parts)
    return f"({base}) -label:{PROCESSED_LABEL_NAME}"


def _get_openai_client() -> OpenAI:
    api_key = env_config("AI_OPENAI_API_KEY", default="") or env_config("OPENAI_API_KEY", default="")
    return OpenAI(
        api_key=api_key,
        timeout=120.0,
    )


def parse_pdf_invoice(pdf_data: bytes, filename: str) -> dict:
    """
    Send a PDF to OpenAI Responses API and extract structured invoice data.

    Args:
        pdf_data: Raw PDF bytes
        filename: Original filename (for logging)

    Returns:
        {
            "parsed": dict (invoice JSON or error),
            "usage": {"input_tokens": int, "output_tokens": int, "total_tokens": int},
        }
    """
    cfg = get_pdf_parser_config()
    client = _get_openai_client()

    b64 = base64.standard_b64encode(pdf_data).decode("utf-8")

    logger.info(f"Parsing PDF invoice: {filename} with model={cfg['model']}")

    response = client.responses.create(
        model=cfg["model"],
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_file",
                        "filename": filename,
                        "file_data": f"data:application/pdf;base64,{b64}",
                    },
                    {
                        "type": "input_text",
                        "text": PROMPT_TEMPLATE,
                    },
                ],
            },
        ],
        temperature=cfg["temperature"],
        max_output_tokens=cfg["max_tokens"],
    )

    raw_text = response.output_text.strip()
    cleaned = strip_markdown_json_fences(raw_text)

    usage = response.usage
    usage_dict = {
        "input_tokens": usage.input_tokens if usage else 0,
        "output_tokens": usage.output_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse LLM JSON for {filename}: {exc}")
        parsed = {"error": "LLM returned invalid JSON", "raw_response": raw_text[:500]}

    return {"parsed": parsed, "usage": usage_dict}


def process_invoice_messages(user) -> dict:
    """
    Full pipeline: search Gmail for invoice emails, download PDFs, parse each.

    Args:
        user: Django User instance

    Returns:
        {
            "results": [
                {
                    "message_id": str,
                    "filename": str,
                    "parsed": dict,
                    "usage": dict,
                },
                ...
            ],
            "errors": [str, ...],
            "summary": {"messages_found": int, "pdfs_parsed": int, "ingest_items_created": int, "ingest_items_skipped": int},
        }
    """
    results = []
    errors = []

    if not verify_gmail_permissions(user):
        return {
            "results": [],
            "errors": ["User has not granted Gmail permissions."],
            "summary": {
                "messages_found": 0,
                "pdfs_parsed": 0,
                "ingest_items_created": 0,
                "ingest_items_skipped": 0,
            },
        }

    try:
        label_id = get_or_create_label(user, PROCESSED_LABEL_NAME)
    except Exception as exc:
        logger.warning(
            "Could not get or create processed label; skipping run to avoid duplicate processing: %s",
            exc,
        )
        err_msg = str(exc)
        if "invalid_scope" in err_msg.lower():
            err_msg = f"{err_msg} Please log out and log in again to refresh Google permissions."
        return {
            "results": [],
            "errors": [f"Label setup failed: {err_msg}"],
            "summary": {
                "messages_found": 0,
                "pdfs_parsed": 0,
                "ingest_items_created": 0,
                "ingest_items_skipped": 0,
            },
        }

    query = _build_invoice_query()
    logger.info(f"Searching inbox with query: {query}")

    try:
        msg_ids = search_inbox_messages(user, query, max_results=20)
    except Exception as exc:
        logger.error(f"Gmail search failed: {exc}")
        return {
            "results": [],
            "errors": [f"Gmail search failed: {exc}"],
            "summary": {
                "messages_found": 0,
                "pdfs_parsed": 0,
                "ingest_items_created": 0,
                "ingest_items_skipped": 0,
            },
        }

    log_api_usage(
        user, "gmail", "gmail_messages_read", len(msg_ids),
        ingest_item=None, origin="invoice_parser_gmail",
    )

    if not msg_ids:
        return {
            "results": [],
            "errors": [],
            "summary": {
                "messages_found": 0,
                "pdfs_parsed": 0,
                "ingest_items_created": 0,
                "ingest_items_skipped": 0,
            },
        }

    pdfs_parsed = 0
    ingest_items_created = 0
    ingest_items_skipped = 0

    for msg_id in msg_ids:
        try:
            if not message_has_attachments(user, msg_id):
                continue

            pdf_attachments = get_pdf_attachments(user, msg_id)
            if not pdf_attachments:
                continue

            for att in pdf_attachments:
                try:
                    result = parse_pdf_invoice(att["data"], att["filename"])
                    parsed = result["parsed"]
                    usage = result["usage"]
                    cfg = get_pdf_parser_config()
                    model = cfg.get("model", "gpt-4o")

                    ingest_item = None
                    if not parsed.get("error"):
                        ingest_item = persist_invoice_to_db(
                            user, parsed, msg_id, att["filename"],
                        )
                        if ingest_item:
                            ingest_items_created += 1
                            try:
                                add_label_to_message(user, msg_id, label_id)
                            except Exception as exc:
                                logger.warning(
                                    "Could not add processed label to message %s: %s",
                                    msg_id,
                                    exc,
                                )
                        else:
                            ingest_items_skipped += 1
                            warning = (
                                f"Persistence skipped for {att['filename']} (msg {msg_id}): "
                                "invoice parsed but no IngestItem was created"
                            )
                            logger.warning(warning)
                            errors.append(warning)

                    log_api_usage(
                        user, model, "input_tokens", usage.get("input_tokens", 0),
                        ingest_item=ingest_item, origin="invoice_parser_pdf",
                    )
                    log_api_usage(
                        user, model, "output_tokens", usage.get("output_tokens", 0),
                        ingest_item=ingest_item, origin="invoice_parser_pdf",
                    )

                    results.append({
                        "message_id": msg_id,
                        "filename": att["filename"],
                        "parsed": parsed,
                        "usage": usage,
                    })
                    pdfs_parsed += 1
                except Exception as exc:
                    err = f"Parse failed for {att['filename']} (msg {msg_id}): {exc}"
                    logger.error(err)
                    errors.append(err)

        except Exception as exc:
            err = f"Error processing message {msg_id}: {exc}"
            logger.error(err)
            errors.append(err)

    return {
        "results": results,
        "errors": errors,
        "summary": {
            "messages_found": len(msg_ids),
            "pdfs_parsed": pdfs_parsed,
            "ingest_items_created": ingest_items_created,
            "ingest_items_skipped": ingest_items_skipped,
        },
    }
