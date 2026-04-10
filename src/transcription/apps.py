import logging
import shutil

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class TranscriptionConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'src.transcription'
    verbose_name = 'Audio Transcription'

    def ready(self):
        """Check ffmpeg availability on startup."""
        ffmpeg_path = shutil.which('ffmpeg')
        if ffmpeg_path:
            logger.info(f"FFmpeg found at: {ffmpeg_path}")
        else:
            logger.warning(
                "FFmpeg not found in PATH. Audio processing features may be limited. "
                "Install ffmpeg for full functionality."
            )
