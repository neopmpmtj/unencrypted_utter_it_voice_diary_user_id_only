"""
Ingestion Celery Tasks

Tasks for processing ingested content including audio transcription,
language detection, and translation pipeline.
"""

import json
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from decouple import config
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.utils.translation import gettext as _

from src.common.utils import ensure_directory
from src.common.utils.rate_limiter import check_transcription_rate_limit
from .models import IngestJob, IngestItem, JobStatus, TemplateType

# Redis TTL for pending transcriptions (15 minutes)
PENDING_TRANSCRIPTION_TTL = 15 * 60

logger = logging.getLogger(__name__)

# Keys in checkpoint_data that contain user speech/transcript; removed on success.
CHECKPOINT_SENSITIVE_KEYS = frozenset({"transcription", "final_text"})


def get_redis_client():
    """Get Redis client for pending transcription storage (isolated DB to avoid contention with Celery/Channels)."""
    try:
        import redis
        db = int(config('PENDING_TRANSCRIPTION_REDIS_DB', default='3'))
        host = config('PENDING_TRANSCRIPTION_REDIS_HOST', default='127.0.0.1')
        port = int(config('PENDING_TRANSCRIPTION_REDIS_PORT', default='6379'))
        url = f"redis://{host}:{port}/{db}"
        return redis.from_url(url)
    except ImportError:
        logger.error("redis-py not installed, pending transcription storage unavailable")
        return None
    except Exception as e:
        logger.error(f"Could not connect to Redis: {e}")
        return None


def get_pending_transcription_key(temp_id: str) -> str:
    """Get Redis key for pending transcription."""
    return f"pending_transcription:{temp_id}"


def store_pending_transcription(temp_id: str, data: dict, ttl: int | None = None) -> bool:
    """Store pending transcription in Redis with TTL."""
    redis_client = get_redis_client()
    if not redis_client:
        return False
    ttl = ttl if ttl is not None else PENDING_TRANSCRIPTION_TTL
    try:
        key = get_pending_transcription_key(temp_id)
        redis_client.setex(key, ttl, json.dumps(data))
        logger.info(f"Stored pending transcription: {temp_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to store pending transcription: {e}")
        return False


def get_pending_transcription(temp_id: str) -> dict | None:
    """Retrieve pending transcription from Redis."""
    redis_client = get_redis_client()
    if not redis_client:
        return None
    try:
        key = get_pending_transcription_key(temp_id)
        data = redis_client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.error(f"Failed to get pending transcription: {e}")
        return None


def delete_pending_transcription(temp_id: str) -> bool:
    """Delete pending transcription from Redis."""
    redis_client = get_redis_client()
    if not redis_client:
        return False
    try:
        key = get_pending_transcription_key(temp_id)
        redis_client.delete(key)
        logger.info(f"Deleted pending transcription: {temp_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete pending transcription: {e}")
        return False


def strip_sensitive_checkpoint_data(checkpoint_data):
    """Return a copy of checkpoint_data without sensitive keys (transcription, final_text)."""
    if not checkpoint_data:
        return checkpoint_data
    return {k: v for k, v in checkpoint_data.items() if k not in CHECKPOINT_SENSITIVE_KEYS}


def get_channel_layer():
    """Get the channel layer for WebSocket broadcasts."""
    try:
        from channels.layers import get_channel_layer as channels_get_layer
        return channels_get_layer()
    except ImportError:
        logger.warning("channels not available, WebSocket broadcasts disabled")
        return None


def broadcast_status(channel_layer, item_id, checkpoint, progress, status, message=''):
    """Send status update via WebSocket."""
    if not channel_layer:
        return
    
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{item_id}",
            {
                "type": "pipeline.status",
                "checkpoint": checkpoint,
                "progress": progress,
                "status": status,
                "message": message,
            }
        )
    except Exception as e:
        logger.debug(f"Could not broadcast status: {e}")


def broadcast_content_ready(channel_layer, item_id, content_text, detected_language):
    """Send content-ready message via WebSocket so user can view/edit while classification runs."""
    if not channel_layer:
        return
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{item_id}",
            {
                "type": "content.ready",
                "content_text": content_text,
                "detected_language": detected_language,
            }
        )
    except Exception as e:
        logger.debug(f"Could not broadcast content ready: {e}")


def broadcast_complete(channel_layer, item_id, content_text, detected_language):
    """Send completion message via WebSocket."""
    if not channel_layer:
        return
    
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{item_id}",
            {
                "type": "pipeline.complete",
                "content_text": content_text,
                "detected_language": detected_language,
            }
        )
    except Exception as e:
        logger.debug(f"Could not broadcast completion: {e}")


def broadcast_error(channel_layer, item_id, error, checkpoint):
    """Send error message via WebSocket."""
    if not channel_layer:
        return
    
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{item_id}",
            {
                "type": "pipeline.error",
                "error": str(error),
                "checkpoint": checkpoint,
            }
        )
    except Exception as e:
        logger.debug(f"Could not broadcast error: {e}")


def broadcast_transcription_discarded(channel_layer, temp_id, reason: str):
    """WebSocket: transcription.discarded for review mode when guard rejects."""
    if not channel_layer:
        return
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{temp_id}",
            {
                "type": "transcription.discarded",
                "temp_id": temp_id,
                "reason": reason,
            },
        )
    except Exception as e:
        logger.debug(f"Could not broadcast transcription discarded: {e}")


def broadcast_transcription_ready(channel_layer, temp_id, transcription_text, detected_language):
    """Send transcription ready message via WebSocket for review mode."""
    if not channel_layer:
        return
    
    try:
        from asgiref.sync import async_to_sync
        async_to_sync(channel_layer.group_send)(
            f"pipeline_{temp_id}",
            {
                "type": "transcription.ready",
                "temp_id": temp_id,
                "transcription_text": transcription_text,
                "detected_language": detected_language,
            }
        )
    except Exception as e:
        logger.debug(f"Could not broadcast transcription ready: {e}")


def log_api_usage(user, service, usage_type, amount, ingest_item=None, origin=None):
    """Log API usage for cost tracking."""
    try:
        from src.accounts.models import APIUsageLog
        APIUsageLog.objects.create(
            user=user,
            service=service,
            usage_type=usage_type,
            amount=amount,
            ingest_item=ingest_item,
            origin=origin,
        )
    except Exception as e:
        logger.warning(f"Could not log API usage: {e}")


def cleanup_temp_files(item):
    """Delete temporary audio files for an item."""
    try:
        for item_file in item.files.filter(role='original'):
            file_path = Path(item_file.storage_url)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Deleted audio file: {file_path}")
        
        # Also clean up chunk directory if it exists
        if item.files.filter(role='original').exists():
            original_file = item.files.filter(role='original').first()
            chunk_dir = Path(original_file.storage_url).parent / 'chunks'
            if chunk_dir.exists():
                shutil.rmtree(chunk_dir)
                logger.info(f"Deleted chunk directory: {chunk_dir}")
    except Exception as e:
        logger.error(f"Error cleaning up files for item {item.id}: {e}")


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=10, retry_kwargs={"max_retries": 3})
def fetch_gmail_ingest(self, job_id: str):
    """
    Fetch Gmail message + attachments.
    FACTS ONLY. No AI, no pipeline logic.
    """
    job = IngestJob.objects.select_related("item").get(id=job_id)

    job.status = JobStatus.RUNNING
    job.started_at = timezone.now()
    job.attempt_count += 1
    job.save(update_fields=["status", "started_at", "attempt_count"])

    try:
        # TODO:
        # - Load user's Google credentials via item.user
        # - Fetch Gmail message
        # - Store raw payload in GmailRawMessage
        # - Create ItemFile rows for attachments
        # - Populate item.content_text
        # - Update item.status if appropriate

        job.status = JobStatus.DONE
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])

    except Exception as exc:
        job.status = JobStatus.ERROR
        job.last_error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "last_error", "finished_at"])
        raise


@shared_task(bind=True, max_retries=3)
def process_audio_ingest(self, job_id: str):
    """
    Full audio processing pipeline with checkpoint resume capability.
    
    Pipeline steps:
    1. Audio chunking (if file > 20MB)
    2. Silence removal
    3. Loudness normalization (EBU R128)
    4. Transcription (Whisper)
    5. Language detection
    6. Translation (if needed)
    7. Save result and schedule cleanup
    
    Each step saves a checkpoint so the pipeline can resume from the
    last successful step on retry (avoiding repeated API calls).
    """
    from src.common.config import get_config
    from src.ingestion.audio_services import SilenceRemover, AudioChunker, LoudnessNormalizer
    from src.transcription.services import transcribe_audio
    from src.translation.services import translate_text
    from src.lang_detect.services import detect_language_keywords, is_same_language
    from src.text_input.utils import normalize_list_text
    
    config = get_config()
    job = IngestJob.objects.select_related("item").get(id=job_id)
    item = job.item
    channel_layer = get_channel_layer()
    
    # Define checkpoint order
    CHECKPOINT_ORDER = ['chunking', 'silence_removal', 'loudness_normalization', 'transcription', 'lang_detect', 'translation', 'cleanup']
    
    def get_completed_checkpoints():
        """Get list of completed checkpoints from job state."""
        if not job.checkpoint:
            return []
        try:
            idx = CHECKPOINT_ORDER.index(job.checkpoint)
            return CHECKPOINT_ORDER[:idx + 1]
        except ValueError:
            return []
    
    def save_checkpoint(checkpoint_name, data=None):
        """Save checkpoint progress."""
        job.checkpoint = checkpoint_name
        if data:
            job.checkpoint_data.update(data)
        job.save(update_fields=['checkpoint', 'checkpoint_data'])
        logger.info(f"Checkpoint saved: {checkpoint_name}")
    
    try:
        job.status = JobStatus.RUNNING
        job.started_at = timezone.now()
        job.attempt_count += 1
        job.save(update_fields=["status", "started_at", "attempt_count"])
        
        completed = get_completed_checkpoints()
        logger.info(f"Starting audio pipeline for job {job_id}, completed checkpoints: {completed}")
        
        # Get original audio file
        original_file = item.files.filter(role='original').first()
        if not original_file:
            raise ValueError("No original audio file found")
        
        original_path = Path(original_file.storage_url)
        if not original_path.exists():
            raise FileNotFoundError(f"Audio file not found: {original_path}")

        # Guard gate: discard if no speech (before quota debit)
        from src.speech_guard.services import should_proceed_to_transcription, run_calibration
        proceed, reason = should_proceed_to_transcription(original_path, item.user_id)
        if not proceed:
            run_calibration(original_path, item.user_id)
            cleanup_temp_files(item)
            item_id_str = str(item.id)
            item.delete()
            broadcast_status(channel_layer, item_id_str, 'guard_discard', 0, 'done', reason or 'No speech detected')
            return

        # Calibration on raw audio (before silence removal / loudness alter the file)
        if item.user_id:
            run_calibration(original_path, item.user_id)

        # Store audio metadata if not already done (quota debited at upload time)
        if not item.audio_duration_seconds:
            try:
                chunker = AudioChunker()
                item.audio_duration_seconds = chunker.get_audio_duration(original_path)
                item.audio_format = original_path.suffix.lstrip('.')
                item.original_file_size = original_path.stat().st_size
                item.save(update_fields=['audio_duration_seconds', 'audio_format', 'original_file_size'])
            except Exception as e:
                logger.warning(f"Could not get audio metadata: {e}")

        # ===== CHECKPOINT 1: Audio Chunking =====
        if 'chunking' not in completed:
            broadcast_status(channel_layer, str(item.id), 'chunking', 10, 'running', 'Preparing audio...')
            
            chunker = AudioChunker()
            if chunker.needs_chunking(original_path):
                chunk_dir = original_path.parent / 'chunks'
                ensure_directory(chunk_dir)
                chunk_paths = chunker.split_audio(original_path, chunk_dir)
                save_checkpoint('chunking', {'chunk_paths': [str(p) for p in chunk_paths]})
            else:
                save_checkpoint('chunking', {'chunk_paths': [str(original_path)]})
        
        chunk_paths = [Path(p) for p in job.checkpoint_data.get('chunk_paths', [str(original_path)])]
        
        # ===== CHECKPOINT 2: Silence Removal =====
        if 'silence_removal' not in completed:
            broadcast_status(channel_layer, str(item.id), 'silence_removal', 25, 'running', 'Removing silence...')
            
            silence_remover = SilenceRemover()
            processed_chunks = []
            
            for i, chunk_path in enumerate(chunk_paths):
                logger.info(f"Processing silence removal for chunk {i+1}/{len(chunk_paths)}")
                processed = silence_remover.process_file(chunk_path)
                if processed:
                    processed_chunks.append(processed)
                else:
                    # Fall back to original if silence removal fails
                    processed_chunks.append(chunk_path)
            
            save_checkpoint('silence_removal', {'processed_chunks': [str(p) for p in processed_chunks]})
        
        processed_chunks = [Path(p) for p in job.checkpoint_data.get('processed_chunks', [])]
        if not processed_chunks:
            processed_chunks = chunk_paths
        
        # ===== CHECKPOINT 2b: Loudness Normalization =====
        if 'loudness_normalization' not in completed:
            broadcast_status(channel_layer, str(item.id), 'loudness_normalization', 30, 'running', 'Normalizing loudness...')
            
            normalizer = LoudnessNormalizer()
            for i, chunk_path in enumerate(processed_chunks):
                logger.info(f"Normalizing loudness for chunk {i+1}/{len(processed_chunks)}")
                result = normalizer.process_file(chunk_path)
                if not result:
                    logger.warning(f"Loudness normalization failed for {chunk_path}, using original")
            save_checkpoint('loudness_normalization', {})
        
        # ===== CHECKPOINT 3: Transcription =====
        if 'transcription' not in completed:
            user = item.user
            if user is not None:
                allowed, info = check_transcription_rate_limit(user)
                if not allowed:
                    retry_after = info.get('retry_after_seconds', 0)
                    job.status = JobStatus.ERROR
                    job.save(update_fields=['status'])
                    broadcast_error(
                        channel_layer, str(item.id),
                        _("Transcription rate limit exceeded. Try again in %(seconds)s seconds.") % {'seconds': retry_after},
                        'transcription',
                    )
                    return
            broadcast_status(channel_layer, str(item.id), 'transcription', 50, 'running', 'Transcribing audio...')
            
            transcriptions = []
            total_audio_minutes = 0
            detected_lang_from_whisper = None
            
            for i, chunk_path in enumerate(processed_chunks):
                logger.info(f"Transcribing chunk {i+1}/{len(processed_chunks)}")
                
                result = transcribe_audio(chunk_path, user=item.user)
                transcriptions.append(result.text)
                
                # Track audio duration for usage logging
                if result.duration:
                    total_audio_minutes += result.duration / 60.0
                
                # Keep detected language from first chunk
                if not detected_lang_from_whisper and result.language:
                    detected_lang_from_whisper = result.language
            
            # Log Whisper usage
            if item.user:
                log_api_usage(item.user, config.ai.transcription_model, 'audio_minutes', total_audio_minutes, item, origin='process_audio_ingest')
            
            # Merge transcriptions if chunked
            if len(transcriptions) > 1:
                chunker = AudioChunker()
                transcription = chunker.merge_transcriptions(transcriptions, config.chunking.overlap_seconds)
            else:
                transcription = transcriptions[0] if transcriptions else ""
            
            save_checkpoint('transcription', {
                'transcription': transcription,
                'whisper_detected_lang': detected_lang_from_whisper
            })
        
        transcription = job.checkpoint_data.get('transcription', '')
        user_prefs = None
        if item.user:
            try:
                user_prefs = item.user.preferences
            except ObjectDoesNotExist:
                pass
        preferred_lang = user_prefs.preferred_language if user_prefs else 'en'

        # ===== Apply list normalization if template_type is 'list' =====
        if item.template_type == TemplateType.LIST:
            transcription = normalize_list_text(transcription)
            logger.info(f"Applied list normalization to audio transcription for item {item.id}")

        # ===== CHECKPOINT 4: Language Detection (keyword-based) =====
        if 'lang_detect' not in completed:
            broadcast_status(channel_layer, str(item.id), 'lang_detect', 65, 'running', 'Detecting language...')
            detected_lang = detect_language_keywords(transcription, fallback_lang=preferred_lang)
            item.detected_language = detected_lang
            item.save(update_fields=['detected_language'])
            save_checkpoint('lang_detect', {'detected_lang': detected_lang})

        detected_lang = job.checkpoint_data.get('detected_lang', 'en')

        # ===== CHECKPOINT 5: Translation (if needed) =====
        
        if 'translation' not in completed:
            enable_translation = user_prefs and getattr(user_prefs, 'enable_translation', True)
            if enable_translation and not is_same_language(detected_lang, preferred_lang):
                broadcast_status(channel_layer, str(item.id), 'translation', 80, 'running', 'Translating...')
                
                final_text, token_usage = translate_text(
                    transcription,
                    detected_lang,
                    preferred_lang,
                    user=item.user
                )
                
                # Log GPT usage
                if item.user:
                    log_api_usage(item.user, config.ai.translation_model, 'input_tokens', token_usage.get('input', 0), item, origin='process_audio_ingest')
                    log_api_usage(item.user, config.ai.translation_model, 'output_tokens', token_usage.get('output', 0), item, origin='process_audio_ingest')
            else:
                final_text = transcription
            
            save_checkpoint('translation', {'final_text': final_text})
        
        final_text = job.checkpoint_data.get('final_text', transcription)
        
        # ===== CHECKPOINT 6: Save and Schedule Cleanup =====
        broadcast_status(channel_layer, str(item.id), 'cleanup', 95, 'running', 'Finalizing...')

        item.content_text = final_text
        item.summary_text = item.summary_text or ""
        item.title = item.title or ""
        item.status = 'processed'

        # Schedule audio deletion based on admin-configurable retention
        from src.accounts.audio_retention_config import get_audio_retention_hours
        retention_hours = get_audio_retention_hours()
        if retention_hours == 0:
            cleanup_temp_files(item)
        else:
            item.audio_deletion_scheduled_at = timezone.now() + timedelta(hours=retention_hours)

        item.save()

        try:
            from src.gigo.services import record_entry
            record_entry(
                user=item.user,
                item=item,
                content_text=final_text,
                item_type="audio",
            )
        except Exception as e:
            logger.warning(f"Could not record GIGO entry: {e}")

        job.checkpoint = 'cleanup'
        job.status = JobStatus.DONE
        job.finished_at = timezone.now()
        job.checkpoint_data = strip_sensitive_checkpoint_data(job.checkpoint_data)
        job.save(update_fields=['checkpoint', 'status', 'finished_at', 'checkpoint_data'])

        logger.info(f"Audio pipeline completed for job {job_id}")

        broadcast_content_ready(channel_layer, str(item.id), final_text, detected_lang or '')

        # Defer completion broadcast until after classification/calendar; pass plaintext for final UI
        try:
            from src.classification.tasks import classify_item_task
            classify_item_task.delay(str(item.id), final_text, detected_lang or '')
            logger.info(f"Queued classification task for item {item.id}")
        except Exception as e:
            logger.warning(f"Could not queue classification task for item {item.id}: {e}")
        
    except Exception as exc:
        logger.error(f"Audio pipeline failed for job {job_id}: {exc}")
        
        job.status = JobStatus.ERROR
        job.last_error = str(exc)
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "last_error", "finished_at"])
        
        # Broadcast error
        broadcast_error(channel_layer, str(item.id), str(exc), job.checkpoint)
        
        # Retry with backoff
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def speech_guard_calibrate_task(audio_path: str, user_id: int | None):
    """
    Async calibration for speech guard. Updates baseline from recording.
    """
    if not user_id:
        return
    try:
        from src.speech_guard.services import run_calibration
        run_calibration(Path(audio_path), user_id)
    except Exception as e:
        logger.warning(f"Speech guard calibration failed: {e}")


@shared_task
def cleanup_expired_audio_files():
    """
    Celery beat task to delete audio files past their retention period.
    
    Run daily via Celery beat schedule.
    """
    expired_items = IngestItem.objects.filter(
        audio_deletion_scheduled_at__lte=timezone.now(),
        status='processed'
    ).exclude(
        audio_deletion_scheduled_at__isnull=True
    )
    
    deleted_count = 0
    for item in expired_items:
        try:
            cleanup_temp_files(item)
            item.audio_deletion_scheduled_at = None
            item.save(update_fields=['audio_deletion_scheduled_at'])
            deleted_count += 1
            logger.info(f"Cleaned up audio files for IngestItem {item.id}")
        except Exception as e:
            logger.error(f"Failed to cleanup IngestItem {item.id}: {e}")
    
    logger.info(f"Audio cleanup task completed: {deleted_count} items cleaned")
    return deleted_count


@shared_task(bind=True, max_retries=3)
def transcribe_only_task(self, temp_id: str, audio_path: str, user_id: int, template_type: str = 'plain'):
    """
    Transcription-only task for review mode.
    
    This task performs chunking, silence removal, loudness normalization, and transcription,
    then stores the result in Redis for user review. No IngestItem is created
    until the user approves/edits the transcription.
    
    Pipeline steps:
    1. Audio chunking (if file > 20MB)
    2. Silence removal
    3. Loudness normalization (EBU R128)
    4. Transcription (Whisper)
    5. Store result in Redis with TTL
    6. Broadcast transcription.ready via WebSocket
    
    Args:
        temp_id: Temporary ID for this pending transcription
        audio_path: Path to the audio file
        user_id: ID of the user who initiated the recording
        template_type: 'plain' or 'list' - used to apply list normalization later
    """
    from src.common.config import get_config
    from src.ingestion.audio_services import SilenceRemover, AudioChunker, LoudnessNormalizer
    from src.transcription.services import transcribe_audio
    from src.accounts.models import CustomUser
    
    config = get_config()
    channel_layer = get_channel_layer()
    original_path = Path(audio_path)
    
    try:
        # Get user for API key usage
        user = None
        if user_id:
            try:
                user = CustomUser.objects.get(id=user_id)
            except CustomUser.DoesNotExist:
                logger.warning(f"User {user_id} not found for transcription task")
        
        # Validate audio file exists
        if not original_path.exists():
            raise FileNotFoundError(f"Audio file not found: {original_path}")

        # Guard gate
        from src.speech_guard.services import should_proceed_to_transcription, run_calibration
        proceed, reason = should_proceed_to_transcription(original_path, user_id)
        if not proceed:
            run_calibration(original_path, user_id)
            store_pending_transcription(
                temp_id,
                {'status': 'discarded', 'reason': reason or 'No speech detected'},
                ttl=120,
            )
            try:
                if original_path.exists():
                    original_path.unlink()
                    logger.info(f"Cleaned up audio file after guard discard: {original_path}")
            except Exception as e:
                logger.warning(f"Could not clean up audio file after guard discard: {e}")
            broadcast_transcription_discarded(channel_layer, temp_id, reason or "No speech detected")
            return

        # Calibration on raw audio (before silence removal / loudness alter the file)
        if user_id:
            run_calibration(original_path, user_id)

        logger.info(f"Starting transcription-only task for temp_id: {temp_id}")
        
        # Broadcast initial status
        broadcast_status(channel_layer, temp_id, 'chunking', 10, 'running', 'Preparing audio...')
        
        # ===== Step 1: Audio Chunking =====
        chunker = AudioChunker()
        if chunker.needs_chunking(original_path):
            chunk_dir = original_path.parent / 'chunks'
            ensure_directory(chunk_dir)
            chunk_paths = chunker.split_audio(original_path, chunk_dir)
            if not chunk_paths:
                raise ValueError("Audio chunking failed: no chunks produced")
        else:
            chunk_paths = [original_path]
        
        # ===== Step 2: Silence Removal =====
        broadcast_status(channel_layer, temp_id, 'silence_removal', 25, 'running', 'Removing silence...')
        
        silence_remover = SilenceRemover()
        processed_chunks = []
        
        for i, chunk_path in enumerate(chunk_paths):
            logger.info(f"Processing silence removal for chunk {i+1}/{len(chunk_paths)}")
            processed = silence_remover.process_file(chunk_path)
            if processed:
                processed_chunks.append(processed)
            else:
                processed_chunks.append(chunk_path)
        
        # ===== Step 2b: Loudness Normalization =====
        broadcast_status(channel_layer, temp_id, 'loudness_normalization', 35, 'running', 'Normalizing loudness...')
        normalizer = LoudnessNormalizer()
        for i, chunk_path in enumerate(processed_chunks):
            logger.info(f"Normalizing loudness for chunk {i+1}/{len(processed_chunks)}")
            result = normalizer.process_file(chunk_path)
            if not result:
                logger.warning(f"Loudness normalization failed for {chunk_path}, using original")
        
        # ===== Step 3: Transcription =====
        if user is not None:
            allowed, info = check_transcription_rate_limit(user)
            if not allowed:
                retry_after = info.get('retry_after_seconds', 0)
                err_msg = _("Transcription rate limit exceeded. Try again in %(seconds)s seconds.") % {'seconds': retry_after}
                store_pending_transcription(
                    temp_id,
                    {'status': 'error', 'error': err_msg},
                    ttl=120,
                )
                broadcast_error(
                    channel_layer, temp_id,
                    err_msg,
                    'transcription',
                )
                return
        
        broadcast_status(channel_layer, temp_id, 'transcription', 50, 'running', 'Transcribing audio...')
        
        transcriptions = []
        total_audio_minutes = 0
        detected_lang_from_whisper = None
        
        for i, chunk_path in enumerate(processed_chunks):
            logger.info(f"Transcribing chunk {i+1}/{len(processed_chunks)}")
            
            result = transcribe_audio(chunk_path, user=user)
            transcriptions.append(result.text)
            
            if result.duration:
                total_audio_minutes += result.duration / 60.0
            
            if not detected_lang_from_whisper and result.language:
                detected_lang_from_whisper = result.language
        
        # Log Whisper usage
        if user:
            log_api_usage(user, config.ai.transcription_model, 'audio_minutes', total_audio_minutes, ingest_item=None, origin='transcribe_only_task')
        
        # Merge transcriptions if chunked
        if len(transcriptions) > 1:
            transcription = chunker.merge_transcriptions(transcriptions, config.chunking.overlap_seconds)
        else:
            transcription = transcriptions[0] if transcriptions else ""
        
        # Get audio metadata (quota debited at upload time)
        audio_duration = None
        audio_format = original_path.suffix.lstrip('.')
        file_size = original_path.stat().st_size
        try:
            audio_duration = chunker.get_audio_duration(original_path)
        except Exception as e:
            logger.warning(f"Could not get audio duration: {e}")

        # ===== Step 4: Store in Redis =====
        pending_data = {
            'transcription': transcription,
            'detected_language': detected_lang_from_whisper or '',
            'audio_path': str(original_path),
            'user_id': user_id,
            'template_type': template_type,
            'audio_duration_seconds': audio_duration,
            'audio_format': audio_format,
            'original_file_size': file_size,
            'created_at': timezone.now().isoformat(),
        }
        
        if not store_pending_transcription(temp_id, pending_data):
            raise RuntimeError("Failed to store pending transcription in Redis")
        
        # ===== Step 5: Broadcast transcription ready =====
        logger.info(f"Transcription complete for temp_id: {temp_id}, length: {len(transcription)} chars")
        broadcast_transcription_ready(channel_layer, temp_id, transcription, detected_lang_from_whisper or '')
        
    except Exception as exc:
        logger.error(f"Transcription-only task failed for temp_id {temp_id}: {exc}")
        store_pending_transcription(
            temp_id,
            {'status': 'error', 'error': str(exc)},
            ttl=120,
        )
        broadcast_error(channel_layer, temp_id, str(exc), 'transcription')
        
        # Clean up audio file on failure
        try:
            if original_path.exists():
                original_path.unlink()
                logger.info(f"Cleaned up audio file after failure: {original_path}")
        except Exception as e:
            logger.warning(f"Could not clean up audio file: {e}")
        
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@shared_task
def cleanup_expired_pending_transcriptions():
    """
    Celery beat task to clean up expired pending transcriptions.
    
    Scans Redis for pending transcriptions and deletes their associated
    audio files. Redis TTL handles the key expiry, but audio files need
    explicit cleanup.
    
    Run every 5 minutes via Celery beat schedule.
    """
    redis_client = get_redis_client()
    if not redis_client:
        logger.warning("Redis not available, skipping pending transcription cleanup")
        return 0
    
    deleted_count = 0
    
    try:
        # Scan for all pending transcription keys
        cursor = 0
        pattern = "pending_transcription:*"
        
        while True:
            cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
            
            for key in keys:
                try:
                    # Get data to check if audio file exists
                    data = redis_client.get(key)
                    if data:
                        pending_data = json.loads(data)
                        created_at_str = pending_data.get('created_at')
                        
                        if created_at_str:
                            from datetime import datetime
                            created_at = datetime.fromisoformat(created_at_str)
                            age_seconds = (timezone.now() - created_at).total_seconds()
                            
                            # Only clean up if older than TTL (key should be expired anyway)
                            # This is a safety net for orphaned audio files
                            if age_seconds > PENDING_TRANSCRIPTION_TTL:
                                audio_path = Path(pending_data.get('audio_path', ''))
                                if audio_path.exists():
                                    try:
                                        audio_path.unlink()
                                        logger.info(f"Cleaned up expired pending audio: {audio_path}")
                                        deleted_count += 1
                                        
                                        # Also clean chunk directory
                                        chunk_dir = audio_path.parent / 'chunks'
                                        if chunk_dir.exists():
                                            shutil.rmtree(chunk_dir)
                                    except Exception as e:
                                        logger.error(f"Failed to delete expired audio file: {e}")
                except Exception as e:
                    logger.warning(f"Error processing pending transcription key {key}: {e}")
            
            if cursor == 0:
                break
        
        logger.info(f"Pending transcription cleanup completed: {deleted_count} files cleaned")
        
    except Exception as e:
        logger.error(f"Error during pending transcription cleanup: {e}")
    
    return deleted_count
