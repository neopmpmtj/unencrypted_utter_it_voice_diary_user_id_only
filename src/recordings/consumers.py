"""
WebSocket Consumers for Real-time Pipeline Status Updates

Provides real-time status updates during audio processing pipeline.
"""

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils.translation import gettext as _

logger = logging.getLogger(__name__)


class PipelineStatusConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for pipeline status updates.
    
    Clients connect with an item_id to receive real-time updates
    about the processing status of their audio file.
    """
    
    async def connect(self):
        """Handle WebSocket connection."""
        self.item_id = self.scope['url_route']['kwargs']['item_id']
        self.group_name = f'pipeline_{self.item_id}'
        
        # Join item-specific group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        
        await self.accept()
        logger.debug(f"WebSocket connected: {self.group_name}")
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection."""
        # Leave the group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )
        logger.debug(f"WebSocket disconnected: {self.group_name}")
    
    async def receive(self, text_data):
        """Handle incoming WebSocket messages (not used, but required)."""
        # We don't expect messages from the client, but handle gracefully
        pass
    
    async def pipeline_status(self, event):
        """
        Send pipeline status update to WebSocket.
        
        Called by the Celery task via channel layer.
        """
        await self.send(text_data=json.dumps({
            'type': 'status',
            'checkpoint': event.get('checkpoint', ''),
            'progress': event.get('progress', 0),
            'status': event.get('status', ''),
            'message': event.get('message', ''),
        }))
    
    async def content_ready(self, event):
        """
        Send content-ready message so user can view/edit while classification runs.
        """
        await self.send(text_data=json.dumps({
            'type': 'content.ready',
            'content_text': event.get('content_text', ''),
            'detected_language': event.get('detected_language', ''),
        }))

    async def pipeline_complete(self, event):
        """
        Send pipeline completion message.
        
        Includes the final transcription/translation result.
        """
        await self.send(text_data=json.dumps({
            'type': 'complete',
            'status': 'done',
            'progress': 100,
            'content_text': event.get('content_text', ''),
            'detected_language': event.get('detected_language', ''),
        }))
    
    async def pipeline_error(self, event):
        """
        Send pipeline error message.

        Includes error details for client-side handling.
        """
        await self.send(text_data=json.dumps({
            'type': 'error',
            'status': 'error',
            'error': event.get('error', _('Unknown error')),
            'checkpoint': event.get('checkpoint', ''),
        }))

    async def classification_status(self, event):
        """
        Forward classification status (e.g. "Classifying...", "Classified with tags: ...").
        Sent as a generic status so the client can show progress.
        """
        await self.send(text_data=json.dumps({
            'type': 'status',
            'status': event.get('status', ''),
            'message': event.get('message', ''),
        }))

    async def calendar_status(self, event):
        """
        Forward calendar parsing status (e.g. conflict with confirmation_url).
        Lets the client redirect to the conflict resolution page.
        """
        payload = {
            'type': 'calendar.status',
            'status': event.get('status', ''),
            'message': event.get('message', ''),
            'conflict': event.get('conflict', False),
            'confirmation_url': event.get('confirmation_url', ''),
            'calendar_event_id': event.get('calendar_event_id', ''),
        }
        if event.get('batch_id'):
            payload['batch_id'] = event.get('batch_id')
        await self.send(text_data=json.dumps(payload))

    async def list_status(self, event):
        """Forward list parsing status (e.g. 'Extracting list items...')."""
        await self.send(text_data=json.dumps({
            'type': 'list.status',
            'status': event.get('status', ''),
            'message': event.get('message', ''),
        }))

    async def financial_status(self, event):
        """Forward financial parsing status (e.g. 'Extracting financial entries...')."""
        await self.send(text_data=json.dumps({
            'type': 'financial.status',
            'status': event.get('status', ''),
            'message': event.get('message', ''),
        }))

    async def transcription_ready(self, event):
        """
        Send transcription ready message for review mode.
        
        Called when transcription is complete and waiting for user review.
        """
        await self.send(text_data=json.dumps({
            'type': 'transcription.ready',
            'temp_id': event.get('temp_id', ''),
            'transcribed_text': event.get('transcription_text', ''),
            'detected_language': event.get('detected_language', ''),
        }))

    async def transcription_discarded(self, event):
        """Forward guard discard for transcribe-only mode."""
        await self.send(text_data=json.dumps({
            'type': 'transcription.discarded',
            'temp_id': event.get('temp_id', ''),
            'reason': event.get('reason', 'No speech detected'),
        }))
