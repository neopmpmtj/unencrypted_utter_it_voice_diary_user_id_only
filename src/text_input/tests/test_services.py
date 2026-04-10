"""
Tests for src/text_input/services.py

Tests critical text input service functions:
- ingest_text_entry()
- _get_user_preferred_language()
- _create_text_ingest_item()
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from src.accounts.models import APIUsageLog, UserPreferences
from src.ingestion.models import IngestItem, IngestStatus, TemplateType
from src.text_input.services import (
    ingest_text_entry,
    _get_user_preferred_language,
    _create_text_ingest_item,
    EmptyTextError,
    InvalidTemplateTypeError,
)

User = get_user_model()


class IngestTextEntryTestCase(TestCase):
    """Test ingest_text_entry() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        
        UserPreferences.objects.update_or_create(
            user=self.user,
            defaults={'preferred_language': 'en'}
        )
    
    @patch('src.text_input.services.detect_language_keywords')
    @patch('src.classification.tasks.classify_item_task')
    def test_ingest_plain_text_success(self, mock_classify, mock_detect):
        """Test successful plain text ingestion."""
        mock_detect.return_value = 'en'
        
        text = "This is a test diary entry"
        
        item, metadata = ingest_text_entry(
            user=self.user,
            text=text,
            template_type='plain',
            title='Test Entry'
        )
        
        self.assertIsNotNone(item)
        self.assertEqual(item.item_type, 'text')
        self.assertEqual(item.template_type, 'plain')
        self.assertEqual(metadata['detected_language'], 'en')
        self.assertFalse(metadata['translated'])
        self.assertEqual(item.user, self.user)
        self.assertEqual(item.content_text, text)
    
    @patch('src.text_input.services.detect_language_keywords')
    @patch('src.text_input.services.normalize_list_text')
    @patch('src.classification.tasks.classify_item_task')
    def test_ingest_list_template(self, mock_classify, mock_normalize, mock_detect):
        """Test ingesting text with list template."""
        mock_normalize.return_value = "- item 1\n- item 2\n- item 3"
        mock_detect.return_value = 'en'
        
        text = "item 1, item 2, item 3"
        
        item, metadata = ingest_text_entry(
            user=self.user,
            text=text,
            template_type='list'
        )
        
        self.assertEqual(item.template_type, 'list')
        mock_normalize.assert_called_once()
    
    def test_ingest_empty_text_raises_error(self):
        """Test that empty text raises EmptyTextError."""
        with self.assertRaises(EmptyTextError):
            ingest_text_entry(
                user=self.user,
                text="",
                template_type='plain'
            )
    
    def test_ingest_whitespace_only_raises_error(self):
        """Test that whitespace-only text raises EmptyTextError."""
        with self.assertRaises(EmptyTextError):
            ingest_text_entry(
                user=self.user,
                text="   \n\t  ",
                template_type='plain'
            )
    
    def test_ingest_invalid_template_type_raises_error(self):
        """Test that invalid template_type raises InvalidTemplateTypeError."""
        with self.assertRaises(InvalidTemplateTypeError):
            ingest_text_entry(
                user=self.user,
                text="valid text content",
                template_type='invalid_type'
            )
    
    @patch('src.text_input.services.get_config')
    @patch('src.text_input.services.detect_language_keywords')
    @patch('src.text_input.services.translate_text')
    @patch('src.text_input.services.is_same_language')
    @patch('src.classification.tasks.classify_item_task')
    def test_ingest_with_translation(self, mock_classify, mock_same_lang, mock_translate,
                                    mock_detect, mock_get_config):
        """Test text ingestion with language translation."""
        mock_config = MagicMock()
        mock_config.ai = MagicMock(translation_model='test-translation-model')
        mock_get_config.return_value = mock_config
        mock_detect.return_value = 'pt'
        mock_same_lang.return_value = False
        mock_translate.return_value = ("Translated text", {'input': 50, 'output': 100, 'total': 150})
        
        text = "Este é um teste de tradução para o português"
        initial_log_count = APIUsageLog.objects.filter(user=self.user).count()
        
        item, metadata = ingest_text_entry(
            user=self.user,
            text=text,
            template_type='plain'
        )
        
        self.assertEqual(metadata['detected_language'], 'pt')
        self.assertTrue(metadata['translated'])
        mock_translate.assert_called_once()
        logs = list(APIUsageLog.objects.filter(user=self.user).order_by('id'))
        self.assertEqual(len(logs), initial_log_count + 2)
        new_logs = logs[initial_log_count:]
        input_log = next(l for l in new_logs if l.usage_type == 'input_tokens')
        output_log = next(l for l in new_logs if l.usage_type == 'output_tokens')
        self.assertEqual(input_log.service, 'test-translation-model')
        self.assertEqual(input_log.amount, 50)
        self.assertEqual(input_log.origin, 'ingest_text_entry')
        self.assertEqual(output_log.service, 'test-translation-model')
        self.assertEqual(output_log.amount, 100)
        self.assertEqual(output_log.origin, 'ingest_text_entry')
    
    @patch('src.text_input.services.detect_language_keywords')
    @patch('src.text_input.services.translate_text')
    @patch('src.text_input.services.is_same_language')
    @patch('src.classification.tasks.classify_item_task')
    def test_ingest_translation_disabled_skips_translation(self, mock_classify, mock_same_lang,
                                                          mock_translate, mock_detect):
        """Test that when enable_translation=False, translate_text is not called even if languages differ."""
        UserPreferences.objects.update_or_create(
            user=self.user,
            defaults={'preferred_language': 'en', 'enable_translation': False}
        )
        mock_detect.return_value = 'pt'
        mock_same_lang.return_value = False
        mock_translate.return_value = ("Translated text", {'total': 150})

        text = "Este é um teste em português"

        item, metadata = ingest_text_entry(
            user=self.user,
            text=text,
            template_type='plain'
        )

        self.assertEqual(metadata['detected_language'], 'pt')
        self.assertFalse(metadata['translated'])
        mock_translate.assert_not_called()

    @patch('src.text_input.services.detect_language_keywords')
    @patch('src.text_input.services.translate_text')
    @patch('src.text_input.services.is_same_language')
    @patch('src.classification.tasks.classify_item_task')
    def test_ingest_translation_failure_uses_original(self, mock_classify, mock_same_lang,
                                                      mock_translate, mock_detect):
        """Test that translation failure falls back to original text."""
        mock_detect.return_value = 'pt'
        mock_same_lang.return_value = False
        mock_translate.side_effect = Exception("Translation service error")
        
        text = "Portuguese text"
        
        item, metadata = ingest_text_entry(
            user=self.user,
            text=text,
            template_type='plain'
        )
        
        self.assertEqual(metadata['detected_language'], 'pt')
        self.assertFalse(metadata['translated'])
    
    @patch('src.text_input.services.detect_language_keywords')
    @patch('src.classification.tasks.classify_item_task')
    def test_ingest_with_title_and_occurred_at(self, mock_classify, mock_detect):
        """Test ingesting with optional title and occurred_at."""
        mock_detect.return_value = 'en'
        
        text = "Entry with metadata"
        title = "My Diary Entry"
        occurred_at = timezone.now() - timezone.timedelta(hours=2)
        
        item, metadata = ingest_text_entry(
            user=self.user,
            text=text,
            template_type='plain',
            title=title,
            occurred_at=occurred_at
        )
        
        self.assertIsNotNone(item)
        self.assertEqual(item.title, title)
        self.assertEqual(item.occurred_at, occurred_at)


class GetUserPreferredLanguageTestCase(TestCase):
    """Test _get_user_preferred_language() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
    
    def test_get_user_preferred_language_set(self):
        """Test retrieving explicitly set preferred language."""
        UserPreferences.objects.update_or_create(
            user=self.user,
            defaults={'preferred_language': 'pt'}
        )
        
        result = _get_user_preferred_language(self.user)
        
        self.assertEqual(result, 'pt')
    
    def test_get_user_preferred_language_default(self):
        """Test that default language is 'en' when not set."""
        result = _get_user_preferred_language(self.user)
        
        self.assertEqual(result, 'en')
    
    def test_get_user_preferred_language_empty_string(self):
        """Test that empty string preference returns default."""
        UserPreferences.objects.update_or_create(
            user=self.user,
            defaults={'preferred_language': ''}
        )
        
        result = _get_user_preferred_language(self.user)
        
        self.assertEqual(result, 'en')


class CreateTextIngestItemTestCase(TestCase):
    """Test _create_text_ingest_item() function."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
    
    def test_create_text_ingest_item_success(self):
        """Test successful creation of text IngestItem."""
        content = "Test content"
        title = "Test Title"
        
        item = _create_text_ingest_item(
            user=self.user,
            content_text=content,
            template_type='plain',
            detected_language='en',
            title=title
        )
        
        self.assertEqual(item.item_type, 'text')
        self.assertEqual(item.template_type, 'plain')
        self.assertEqual(item.detected_language, 'en')
        self.assertEqual(item.user, self.user)
        self.assertEqual(item.status, IngestStatus.PROCESSED)
        self.assertEqual(item.content_text, content)
        self.assertEqual(item.title, title)
    
    def test_create_with_empty_title(self):
        """Test creation with empty title."""
        item = _create_text_ingest_item(
            user=self.user,
            content_text="content",
            template_type='plain',
            detected_language='en',
            title=""
        )
        
        self.assertIsNotNone(item)
