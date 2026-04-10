"""
Image-based invoice parser configuration (future implementation).

Will use the INVOICE_PARSER_IMAGE goal from the model picker for
parsing invoice images (photos, screenshots) via vision capabilities.
"""

from src.common.model_picker.config_model_picker import INVOICE_PARSER_IMAGE

GOAL = INVOICE_PARSER_IMAGE
