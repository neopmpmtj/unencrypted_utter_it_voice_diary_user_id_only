# Invoice Parser Module

Extracts structured invoice data from PDF attachments in Gmail, using OpenAI's Responses API for vision-based extraction. Persists results into the financial parser models (FinancialRecord, FinancialItem, HypermarketLineItem) and the central IngestItem fact table.

---

## Overview

| Aspect | Details |
|--------|---------|
| **Purpose** | Parse PDF invoices from Gmail, extract structured data, persist to DB |
| **Trigger** | Manual: Admin Test Page (`/test/`) → "Parse Invoices" button; Automatic: Celery Beat every 10 min for all users with Gmail |
| **API** | `POST /invoice-parser/api/parse-pdf/` |
| **LLM** | OpenAI GPT-4o (Responses API) via model picker goal `invoice_parser_pdf` |
| **Auth** | `@app_admin_required`; Gmail permissions required |

---

## Architecture

```
Gmail inbox (subject: invoice|fatura|recibo..., excluding UtterIt/InvoiceParsed)
  → get_or_create_label("UtterIt/InvoiceParsed") [idempotent]
  → get_pdf_attachments()
  → parse_pdf_invoice() [OpenAI Responses API]
  → persist_invoice_to_db()
  → add_label_to_message() [marks as processed, prevents duplicates]
       → IngestItem (provider=GMAIL, item_type=EMAIL)
       → route_utterance() [intent triage]
       → FinancialRecord + FinancialItem + HypermarketLineItem
```

---

## Module Structure

```
src/invoice_parser/
├── pdf_parser/
│   ├── config.py      # TRIGGER_WORDS, PROMPT_TEMPLATE, get_pdf_parser_config()
│   ├── services.py    # parse_pdf_invoice(), process_invoice_messages()
│   ├── persistence.py # persist_invoice_to_db()
│   └── views.py       # parse_pdf_api
├── tasks.py           # process_invoices_for_all_users_task (Celery Beat, 10 min)
├── image_parser/      # Future: image-based invoice parsing (INVOICE_PARSER_IMAGE)
│   └── config.py
├── urls.py
├── apps.py
└── tests/
    ├── test_pdf_parser_services.py
    ├── test_pdf_parser_views.py
    └── test_persistence.py
```

---

## Components

### PDF Parser (`pdf_parser/`)

- **Gmail search**: Uses trigger words in subject (`invoice`, `invoices`, `fatura`, `faturas`, `recibo`, `recibos`), excludes messages with label `UtterIt/InvoiceParsed` (already processed)
- **Processed label**: After successful persistence, adds `UtterIt/InvoiceParsed` to the message so it is not reprocessed. Label is created on first use (idempotent).
- **Extraction**: Sends PDF to OpenAI Responses API (`client.responses.create`) with `input_file` + prompt
- **Output schema**: `vendor_name`, `invoice_number`, `invoice_date`, `due_date`, `currency`, `line_items[]`, `subtotal`, `tax`, `total_amount`
- **Persistence**: Creates IngestItem, runs intent router (finance route), creates FinancialRecord, FinancialItem, HypermarketLineItem

### Image Parser (`image_parser/`)

Placeholder for future image-based parsing (photos, screenshots) via vision capabilities. Uses model picker goal `INVOICE_PARSER_IMAGE`.

---

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/invoice-parser/api/parse-pdf/` | Search Gmail for invoice emails, download PDFs, parse each, persist to DB |

**Response**:
```json
{
  "results": [
    {
      "message_id": "...",
      "filename": "invoice.pdf",
      "parsed": { "vendor_name": "...", "total_amount": 123.45, ... },
      "usage": { "input_tokens": 500, "output_tokens": 200, "total_tokens": 700 }
    }
  ],
  "errors": [],
  "summary": { "messages_found": 5, "pdfs_parsed": 3 }
}
```

**Error responses**:
- `403` — Gmail not connected (`gmail_not_connected`)
- `503` — Parse failed (`parse_failed`)

---

## Trigger (UI)

The pipeline is triggered manually from the **Admin Test Page** at `/test/`:

1. User must be an app admin (`is_app_admin=True`)
2. User must have Gmail connected (OAuth with `gmail.modify` scope)
3. Click "Parse Invoices" → POST to `/invoice-parser/api/parse-pdf/`
4. UI shows summary (messages found, PDFs parsed) and full JSON response

## Trigger (Automatic)

Celery Beat runs `process_invoices_for_all_users_task` every 10 minutes. The task processes Gmail for **all users** with `UserSecret.has_gmail_permission()`. No admin UI required; runs in the background. Requires Celery worker and Celery Beat to be running.

---

## Configuration

- **Model picker**: `invoice_parser_pdf` goal in [config_model_picker.py](../common/model_picker/config_model_picker.py)
  - Default: `gpt-4o`, temperature 0, max_tokens 4096
- **OpenAI API key**: `AI_OPENAI_API_KEY` or `OPENAI_API_KEY`
- **Trigger words**: Configurable in [pdf_parser/config.py](pdf_parser/config.py)
- **Processed label**: `UtterIt/InvoiceParsed` in [pdf_parser/config.py](pdf_parser/config.py); requires `gmail.labels` scope for label creation

---

## Dependencies

- `openai>=1.66.0` (Responses API)
- Gmail OAuth (via `src.common.google_account`)
- Financial parser models (`FinancialRecord`, `FinancialItem`, `HypermarketLineItem`)
- Intent router (`route_utterance`)

---

## Tests

```bash
python manage.py test src.invoice_parser
```

Covers: `_build_invoice_query`, `parse_pdf_invoice`, `process_invoice_messages`, `persist_invoice_to_db`, `parse_pdf_api` view.
