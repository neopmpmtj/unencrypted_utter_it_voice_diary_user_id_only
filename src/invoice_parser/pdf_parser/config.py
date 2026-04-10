"""
PDF invoice parser configuration.

Loads model/temperature/max_tokens from the centralized model picker
and defines the prompt template, trigger words, and expected JSON output schema.
"""

import re

from src.common.model_picker.config_model_picker import (
    INVOICE_PARSER_PDF,
    get_llm_config,
)

GOAL = INVOICE_PARSER_PDF

TRIGGER_WORDS_EN = ["invoice", "invoices"]
TRIGGER_WORDS_PT = ["fatura", "faturas", "recibo", "recibos"]
TRIGGER_WORDS = TRIGGER_WORDS_EN + TRIGGER_WORDS_PT

PROCESSED_LABEL_NAME = "UtterIt/InvoiceParsed"

_LANGUAGE_INSTRUCTION = (
    "IMPORTANT: Respond in the same language as the input text below. "
    "Do not translate; preserve the language of the input.\n\n"
)

INVOICE_JSON_SCHEMA = {
    "vendor_name": "str",
    "invoice_number": "str",
    "invoice_date": "str (YYYY-MM-DD)",
    "due_date": "str (YYYY-MM-DD) or null",
    "currency": "str (ISO 4217, e.g. EUR, USD)",

    "line_items": [
        {
            "description": "str",
            "quantity": "float",
            "unit_price": "float",
            "total": "float"
        }
    ],

    "summary": {
        "total": "float",          # TOTAL
        "total_to_pay": "float"    # TOTAL A PAGAR
    },

    "payments": {
        "total_paid": "float or null"   # TOTAL PAGO
    }
}

PROMPT_TEMPLATE = (
    _LANGUAGE_INSTRUCTION
    + """You are an invoice and retail receipt data extraction assistant.

Extract all structured data from the attached PDF invoice/receipt and return it as a single JSON object.

Rules:
1. Respond ONLY with valid JSON. No markdown fences, no explanation, no extra text.
2. Use null for any field that is not present in the document.
3. Dates must be in YYYY-MM-DD format.
4. Currency must be a 3-letter ISO 4217 code (e.g. EUR, USD, GBP).
5. All monetary values must be numbers, not strings.
6. line_items is an array; each item has description, quantity, unit_price, discount, and total.
7. discount is the discount/savings applied to that specific line item. Use 0 if none is shown for that line.
8. If the receipt shows discount/savings lines immediately after an item (for example 'Poupança Imediata', 'Discount', 'Savings', 'Promo'), attach that value to the preceding item when clearly applicable.
9. subtotal = total value of items before invoice-level discounts, unless an explicit subtotal field is printed.
10. discount_total = total of all discounts/savings shown on the document, including summary discount fields such as 'TOTAL POUPANÇA'.
11. total_amount = final amount actually payable after discounts. If the document contains labels such as 'TOTAL A PAGAR', 'Amount Due', 'Total Due', or similar, prefer that value over inferred arithmetic.
12. amount_paid = amount actually paid, if shown; otherwise null.
13. tax = total tax amount if explicitly shown or directly computable from tax summary lines; otherwise null.
14. Prefer summary blocks and explicitly labeled totals over inferred totals.
15. Do not confuse gross total/subtotal with the final payable total when a discount or savings field is present.
16. If both a gross total and a payable total are shown, set:
    - subtotal = gross total before discount
    - discount_total = total savings/discount
    - total_amount = payable total after discount
17. If the document is not an invoice/receipt or contains no extractable invoice data, return: {"error": "No invoice data found"}
18. If uncertain, return null for the uncertain field instead of guessing.

EXPECTED_OUTPUT_EXAMPLE = {
    "vendor_name": "Pingo Doce",
    "invoice_number": "FT FR09/123456",
    "invoice_date": "2026-03-17",
    "due_date": None,
    "currency": "EUR",

    "line_items": [
        {
            "description": "Produto Exemplo",
            "quantity": 1.0,
            "unit_price": 2.99,
            "total": 2.99
        }
    ],

    "summary": {
        "total": 28.98,
        "total_to_pay": 24.08
    },

    "payments": {
        "total_paid": 24.08
    }
}

Extraction priority for totals:
1. Explicitly labeled payable total (e.g. 'TOTAL A PAGAR')
2. Explicitly labeled paid total (e.g. 'TOTAL PAGO')
3. Explicitly labeled discount/savings total (e.g. 'TOTAL POUPANÇA')
4. Explicitly labeled gross total/subtotal (e.g. 'TOTAL')
5. Arithmetic inference only if labeled values are absent
"""
)


def get_pdf_parser_config() -> dict:
    """Return model picker config for the PDF invoice parser goal."""
    cfg = get_llm_config(GOAL)
    return {
        "model": cfg.get("model", "gpt-4o"),
        "temperature": cfg.get("temperature", 0.0),
        "max_tokens": cfg.get("max_tokens", 4096),
    }


_MD_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?", re.MULTILINE)
_MD_FENCE_END_RE = re.compile(r"\n?```\s*$", re.MULTILINE)


def strip_markdown_json_fences(text: str) -> str:
    """Remove ```json ... ``` markdown fences if present."""
    text = _MD_FENCE_RE.sub("", text)
    text = _MD_FENCE_END_RE.sub("", text)
    return text.strip()
