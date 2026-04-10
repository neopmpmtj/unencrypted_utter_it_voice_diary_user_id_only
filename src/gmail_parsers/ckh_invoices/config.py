"""
Invoice trigger words for Gmail subject search.

English and Portuguese keywords to identify invoice-related emails.
"""

# English
TRIGGER_WORDS_EN = [
    "invoice",
    "invoices",
]

# Portuguese
TRIGGER_WORDS_PT = [
    "fatura",
    "faturas",
    "recibo",
    "recibos",
]

TRIGGER_WORDS = TRIGGER_WORDS_EN + TRIGGER_WORDS_PT
