"""OAuth token encryption roundtrip (Fernet from MASTER_ENCRYPTION_KEY)."""

from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase

from src.common.utils import encryption as encryption_module
from src.common.utils.encryption import decrypt_value, encrypt_value


class OAuthCryptoRoundtripTests(TestCase):
    def test_encrypt_decrypt_token_roundtrip(self):
        fernet = Fernet(Fernet.generate_key())
        encryption_module._default_fernet.cache_clear()
        with patch.object(encryption_module, "_default_fernet", return_value=fernet):
            token = "ya29.fake-access-token"
            cipher = encrypt_value(token)
            self.assertNotEqual(cipher, token)
            self.assertEqual(decrypt_value(cipher), token)
        encryption_module._default_fernet.cache_clear()
