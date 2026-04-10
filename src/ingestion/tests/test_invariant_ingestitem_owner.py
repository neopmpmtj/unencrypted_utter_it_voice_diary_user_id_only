from django.test import TestCase
from django.db import IntegrityError

from src.accounts.models import CustomUser
from src.ingestion.models import IngestItem



class IngestItemOwnershipInvariantTests(TestCase):
    """
    Guard tests:
    - Every IngestItem must have user set at creation time.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="invtest@example.com",
            password="Pass123",
        )
        self.user.is_email_verified = True
        self.user.save()

    def test_model_rejects_null_user(self):
        with self.assertRaises(IntegrityError):
            IngestItem.objects.create(
                user=None,
                item_type="text",
                content_text="should fail",
            )
