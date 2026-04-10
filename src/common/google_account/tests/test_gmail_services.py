"""Tests for gmail_services.search_inbox_messages, get_message, message_has_attachments."""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from src.common.google_account.gmail_services import (
    search_inbox_messages,
    get_message,
    message_has_attachments,
    get_or_create_label,
    add_label_to_message,
)

User = get_user_model()


class SearchInboxMessagesTests(TestCase):
    """Test search_inbox_messages returns message IDs from Gmail API."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="gmail@example.com",
            password="Pass123",
        )

    @patch("src.common.google_account.gmail_services.get_authenticated_service")
    def test_returns_message_ids(self, mock_get_service):
        mock_service = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "messages": [{"id": "msg1"}, {"id": "msg2"}],
        }
        mock_service.users.return_value.messages.return_value.list.return_value = mock_list
        mock_get_service.return_value = mock_service

        result = search_inbox_messages(self.user, "subject:invoice", max_results=10)

        self.assertEqual(result, ["msg1", "msg2"])
        mock_list.execute.assert_called_once()
        call_kwargs = mock_service.users.return_value.messages.return_value.list.call_args[1]
        self.assertEqual(call_kwargs["userId"], "me")
        self.assertEqual(call_kwargs["labelIds"], ["INBOX"])
        self.assertEqual(call_kwargs["q"], "subject:invoice")
        self.assertEqual(call_kwargs["maxResults"], 10)

    @patch("src.common.google_account.gmail_services.get_authenticated_service")
    def test_returns_empty_list_when_no_messages(self, mock_get_service):
        mock_service = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {}
        mock_service.users.return_value.messages.return_value.list.return_value = mock_list
        mock_get_service.return_value = mock_service

        result = search_inbox_messages(self.user, "subject:nonexistent")

        self.assertEqual(result, [])


class GetMessageTests(TestCase):
    """Test get_message fetches message by ID."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="gmail@example.com",
            password="Pass123",
        )

    @patch("src.common.google_account.gmail_services.get_authenticated_service")
    def test_returns_message(self, mock_get_service):
        mock_service = MagicMock()
        mock_get = MagicMock()
        mock_get.execute.return_value = {"id": "msg1", "threadId": "t1"}
        mock_service.users.return_value.messages.return_value.get.return_value = mock_get
        mock_get_service.return_value = mock_service

        result = get_message(self.user, "msg1", format="metadata")

        self.assertEqual(result["id"], "msg1")
        call_kwargs = mock_service.users.return_value.messages.return_value.get.call_args[1]
        self.assertEqual(call_kwargs["userId"], "me")
        self.assertEqual(call_kwargs["id"], "msg1")
        self.assertEqual(call_kwargs["format"], "metadata")


class MessageHasAttachmentsTests(TestCase):
    """Test message_has_attachments detects attachments from payload."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="gmail@example.com",
            password="Pass123",
        )

    @patch("src.common.google_account.gmail_services.get_message")
    def test_returns_true_when_part_has_filename(self, mock_get):
        mock_get.return_value = {
            "payload": {
                "parts": [
                    {"mimeType": "text/plain", "body": {}},
                    {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"attachmentId": "a1"}},
                ]
            }
        }
        self.assertTrue(message_has_attachments(self.user, "msg1"))

    @patch("src.common.google_account.gmail_services.get_message")
    def test_returns_true_when_part_has_attachment_id_only(self, mock_get):
        mock_get.return_value = {
            "payload": {
                "parts": [
                    {"mimeType": "application/pdf", "body": {"attachmentId": "a1"}},
                ]
            }
        }
        self.assertTrue(message_has_attachments(self.user, "msg1"))

    @patch("src.common.google_account.gmail_services.get_message")
    def test_returns_false_when_no_attachments(self, mock_get):
        mock_get.return_value = {
            "payload": {
                "parts": [
                    {"mimeType": "text/plain", "body": {}},
                    {"mimeType": "text/html", "body": {}},
                ]
            }
        }
        self.assertFalse(message_has_attachments(self.user, "msg1"))

    @patch("src.common.google_account.gmail_services.get_message")
    def test_returns_false_when_no_parts(self, mock_get):
        mock_get.return_value = {"payload": {}}
        self.assertFalse(message_has_attachments(self.user, "msg1"))

    @patch("src.common.google_account.gmail_services.get_message")
    def test_returns_true_for_forwarded_message_with_attachment_in_payload(self, mock_get):
        """Attachment inside message/rfc822 embedded message (payload.parts)."""
        mock_get.return_value = {
            "payload": {
                "parts": [
                    {"mimeType": "text/plain", "body": {}},
                    {
                        "mimeType": "message/rfc822",
                        "payload": {
                            "parts": [
                                {"mimeType": "text/plain", "body": {}},
                                {
                                    "mimeType": "application/pdf",
                                    "filename": "16001.pdf",
                                    "body": {"attachmentId": "a1"},
                                },
                            ]
                        },
                    },
                ]
            }
        }
        self.assertTrue(message_has_attachments(self.user, "msg1"))

    @patch("src.common.google_account.gmail_services.get_message")
    def test_returns_true_for_forwarded_message_with_attachment_in_parts(self, mock_get):
        """Attachment inside message/rfc822 embedded message (parts array)."""
        mock_get.return_value = {
            "payload": {
                "parts": [
                    {"mimeType": "text/plain", "body": {}},
                    {
                        "mimeType": "message/rfc822",
                        "parts": [
                            {"mimeType": "text/plain", "body": {}},
                            {
                                "mimeType": "application/pdf",
                                "filename": "fatura.pdf",
                                "body": {"attachmentId": "a2"},
                            },
                        ],
                    },
                ]
            }
        }
        self.assertTrue(message_has_attachments(self.user, "msg1"))


class GetOrCreateLabelTests(TestCase):
    """Test get_or_create_label returns or creates label."""

    @patch("src.common.google_account.gmail_services.get_authenticated_service")
    def test_returns_existing_label_id(self, mock_get_service):
        mock_service = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {
            "labels": [
                {"id": "Label_42", "name": "UtterIt/InvoiceParsed", "type": "user"},
            ]
        }
        mock_service.users.return_value.labels.return_value.list.return_value = mock_list
        mock_get_service.return_value = mock_service

        user = User.objects.create_user(email="label@example.com", password="Pass123")
        result = get_or_create_label(user, "UtterIt/InvoiceParsed")

        self.assertEqual(result, "Label_42")
        mock_service.users.return_value.labels.return_value.list.assert_called_once_with(userId="me")
        mock_service.users.return_value.labels.return_value.create.assert_not_called()

    @patch("src.common.google_account.gmail_services.get_authenticated_service")
    def test_creates_label_when_not_found(self, mock_get_service):
        mock_service = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {"labels": []}
        mock_create = MagicMock()
        mock_create.execute.return_value = {"id": "Label_99", "name": "UtterIt/InvoiceParsed"}
        mock_service.users.return_value.labels.return_value.list.return_value = mock_list
        mock_service.users.return_value.labels.return_value.create.return_value = mock_create
        mock_get_service.return_value = mock_service

        user = User.objects.create_user(email="label@example.com", password="Pass123")
        result = get_or_create_label(user, "UtterIt/InvoiceParsed")

        self.assertEqual(result, "Label_99")
        mock_service.users.return_value.labels.return_value.create.assert_called_once_with(
            userId="me",
            body={
                "name": "UtterIt/InvoiceParsed",
                "messageListVisibility": "show",
                "labelListVisibility": "labelShow",
            },
        )


class AddLabelToMessageTests(TestCase):
    """Test add_label_to_message calls Gmail modify API."""

    @patch("src.common.google_account.gmail_services.get_authenticated_service")
    def test_calls_modify_with_add_label_ids(self, mock_get_service):
        mock_service = MagicMock()
        mock_modify = MagicMock()
        mock_service.users.return_value.messages.return_value.modify.return_value = mock_modify
        mock_get_service.return_value = mock_service

        user = User.objects.create_user(email="label@example.com", password="Pass123")
        add_label_to_message(user, "msg1", "Label_123")

        mock_service.users.return_value.messages.return_value.modify.assert_called_once_with(
            userId="me",
            id="msg1",
            body={"addLabelIds": ["Label_123"]},
        )
        mock_modify.execute.assert_called_once()
