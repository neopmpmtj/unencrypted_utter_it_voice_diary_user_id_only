"""
Pydantic Settings Configuration

Type-safe configuration for the Voice Diary application.
Supports:
- Environment variables (e.g., RECORDER_MAX_DURATION=1200)
- Database overrides via GlobalSettings model
- IDE autocomplete via typed config classes

Usage:
    from src.common.config import get_config
    
    config = get_config()
    max_duration = config.recorder.max_duration
    threshold = config.silence.threshold_db
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices, model_validator

logger = logging.getLogger(__name__)


class RecorderConfig(BaseSettings):
    """Configuration for the audio recorder."""
    
    max_duration: int = Field(
        default=1200,
        description="Maximum recording duration in seconds (0 = unlimited)"
    )
    allow_unlimited: bool = Field(
        default=False,
        description="Allow unlimited recording duration"
    )
    sample_rate: int = Field(
        default=44100,
        description="Audio sample rate in Hz"
    )
    preferred_mime_types: List[str] = Field(
        default=["audio/webm", "audio/wav"],
        description="Preferred MIME types (WebM preferred, WAV fallback for iOS)"
    )
    max_file_size_mb: int = Field(
        default=100,
        description="Maximum upload file size in MB"
    )
    
    class Config:
        env_prefix = "RECORDER_"


class SilenceRemovalConfig(BaseSettings):
    """Configuration for silence removal processing."""
    
    enabled: bool = Field(
        default=True,
        description="Enable silence removal processing"
    )
    threshold_db: float = Field(
        default=-35.0,
        description="Silence threshold in dB"
    )
    min_duration: float = Field(
        default=0.5,
        description="Minimum silence duration to remove (seconds)"
    )
    padding_ms: float = Field(
        default=50.0,
        description="Padding around silence cuts (milliseconds)"
    )
    ffmpeg_timeout: int = Field(
        default=120,
        description="FFmpeg processing timeout in seconds"
    )
    supported_formats: List[str] = Field(
        default=[".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma", ".webm"],
        description="Supported audio formats"
    )
    output_format: str = Field(
        default=".mp3",
        description="Output format for processed audio"
    )
    mp3_quality: int = Field(
        default=2,
        description="MP3 quality (0=best, 9=worst)"
    )
    
    class Config:
        env_prefix = "SILENCE_"


class LoudnessConfig(BaseSettings):
    """Configuration for EBU R128 loudness normalization (loudnorm)."""

    enabled: bool = Field(
        default=False,
        description="Enable loudness normalization before transcription",
    )
    target_i: float = Field(
        default=-16.0,
        description="Target integrated loudness in LUFS (-70 to -5)",
    )
    target_tp: float = Field(
        default=-1.5,
        description="True peak limit in dBTP (-9 to 0)",
    )
    target_lra: float = Field(
        default=11.0,
        description="Target loudness range (1 to 50)",
    )
    ffmpeg_timeout: int = Field(
        default=180,
        description="FFmpeg processing timeout in seconds (two-pass can be slow)",
    )

    class Config:
        env_prefix = "LOUDNESS_"


class ChunkingConfig(BaseSettings):
    """Configuration for audio chunking (for large files)."""

    enabled: bool = Field(
        default=True,
        description="Enable audio chunking for large files"
    )
    max_chunk_size_mb: int = Field(
        default=20,
        description="Maximum chunk size in MB (Whisper API limit is 25MB)"
    )
    overlap_seconds: float = Field(
        default=1.0,
        description="Overlap between chunks to avoid cutting words"
    )
    
    class Config:
        env_prefix = "CHUNKING_"


class SpeechGuardConfig(BaseSettings):
    """Configuration for the speech detection guard gate (VAD before transcription)."""

    enabled: bool = Field(
        default=False,
        description="Enable the guard gate; when false, all recordings pass through",
    )
    analysis_window_seconds: float = Field(
        default=5.0,
        description="Duration of audio to analyze from the start (seconds)",
    )
    frame_duration_ms: int = Field(
        default=30,
        description="Length of each analysis frame (ms)",
    )
    frame_hop_ms: int = Field(
        default=15,
        description="Step size between consecutive frames (ms, 50% overlap)",
    )
    sample_rate: int = Field(
        default=16000,
        description="Audio sample rate for analysis (Hz)",
    )
    energy_multiplier: float = Field(
        default=2.5,
        description="Frame RMS must exceed baseline * this to be active",
    )
    min_active_ratio: float = Field(
        default=0.15,
        description="Minimum proportion of active frames required",
    )
    variance_multiplier: float = Field(
        default=4.0,
        description="Energy variance must exceed baseline variance * this",
    )
    zcr_multiplier: float = Field(
        default=1.5,
        description="ZCR variance threshold for tiebreaker check",
    )
    ambiguous_ratio_low: float = Field(
        default=0.10,
        description="Lower bound of ambiguous zone where ZCR tiebreaker is invoked",
    )
    calibration_percentile: float = Field(
        default=0.20,
        description="Bottom percentile of frames used for noise floor calibration",
    )

    class Config:
        env_prefix = "SPEECH_GUARD_"


def _default_env_path() -> str:
    """Path to .env in project root (parent of src/)."""
    return str(Path(__file__).resolve().parent.parent.parent.parent / ".env")


def _ai_defaults_from_central() -> dict:
    """Voice transcription/translation defaults from central LLM config."""
    try:
        from src.common.model_picker import get_llm_config
        trans = get_llm_config("voice_transcription")
        transla = get_llm_config("voice_translation")
        return {
            "transcription_model": trans.get("model", "gpt-4o-transcribe"),
            "translation_model": transla.get("model", "gpt-4o-mini"),
            "translation_temperature": transla.get("temperature", 0.3),
            "translation_max_tokens": transla.get("max_tokens", 4096),
        }
    except Exception:
        return {
            "transcription_model": "gpt-4o-transcribe",
            "translation_model": "gpt-4o-mini",
            "translation_temperature": 0.3,
            "translation_max_tokens": 4096,
        }


_AI_DEFAULTS = _ai_defaults_from_central()


class AIConfig(BaseSettings):
    """Configuration for AI services (OpenAI)."""
    
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (can be overridden per-user)",
        validation_alias=AliasChoices("AI_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    transcription_model: str = Field(
        default=_AI_DEFAULTS["transcription_model"],
        description="Model to use for transcription"
    )
    transcription_prompt: str = Field(
        default="",
        description="Optional prompt to guide transcription (e.g., accent hints, code-switching behavior)"
    )
    translation_model: str = Field(
        default=_AI_DEFAULTS["translation_model"],
        description="Model to use for translation"
    )
    translation_temperature: float = Field(
        default=_AI_DEFAULTS["translation_temperature"],
        description="Temperature for translation (0-1)"
    )
    translation_max_tokens: int = Field(
        default=_AI_DEFAULTS["translation_max_tokens"],
        description="Maximum tokens for translation response"
    )
    max_retries: int = Field(
        default=3,
        description="Maximum API call retries"
    )
    retry_delay: float = Field(
        default=1.0,
        description="Initial retry delay in seconds"
    )
    retry_backoff_factor: float = Field(
        default=2.0,
        description="Backoff factor for retries"
    )
    
    class Config:
        env_prefix = "AI_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"  # .env has Django/DB/other vars; only read AI_* and aliases


class StorageConfig(BaseSettings):
    """Configuration for file storage. Default: app_dir/audio (set via Django BASE_DIR)."""

    audio_temp_path: str = Field(
        default="audio",
        description="Path for temporary audio files (relative to app dir if not absolute)"
    )
    default_retention_days: int = Field(
        default=7,
        description="Default audio retention period in days"
    )
    save_attachments_to_local_filesystem: bool = Field(
        default=False,
        description=(
            "When true, persist attachments and stored recordings under local_storage_root "
            "instead of uploading attachments to Google Drive"
        ),
    )
    local_storage_root: str = Field(
        default="",
        description=(
            "Absolute root directory for local attachments/recordings "
            "(required when save_attachments_to_local_filesystem is true). "
            "Env: STORAGE_LOCAL_STORAGE_ROOT."
        ),
    )
    local_attachments_subdir: str = Field(
        default="attachments",
        description="Subdirectory under local_storage_root for attachment files",
    )
    local_recordings_subdir: str = Field(
        default="recordings",
        description="Subdirectory under local_storage_root for persisted original recordings",
    )

    @model_validator(mode="after")
    def _require_absolute_local_root_when_enabled(self):
        if not self.save_attachments_to_local_filesystem:
            return self
        root = (self.local_storage_root or "").strip()
        if not root:
            raise ValueError(
                "local_storage_root (STORAGE_LOCAL_STORAGE_ROOT) is required "
                "when save_attachments_to_local_filesystem is true"
            )
        p = Path(root).expanduser()
        if not p.is_absolute():
            raise ValueError(
                "local_storage_root must be an absolute path when save_attachments_to_local_filesystem is true"
            )
        return self

    class Config:
        env_prefix = "STORAGE_"
        # Required so STORAGE_* from project .env are applied. Unlike
        # STORAGE_AUDIO_TEMP_PATH (set on os.environ in Django base.py), other
        # storage keys are not exported to the environment by decouple.
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"


class TranscriptionRateLimitConfig(BaseSettings):
    """
    Per-tier transcription rate limits (free, pro, ultra).
    Overridable via env (TRANSCRIPTION_FREE_REQUESTS, etc.) or GlobalSettings
    (transcription_limits.free_requests, transcription_limits.free_window_seconds, ...).
    """
    free_requests: int = Field(default=10, ge=1, description="Max transcription requests per window (free tier)")
    free_window_seconds: int = Field(default=3600, ge=60, description="Rate limit window in seconds (free tier)")
    pro_requests: int = Field(default=50, ge=1, description="Max transcription requests per window (pro tier)")
    pro_window_seconds: int = Field(default=3600, ge=60, description="Rate limit window in seconds (pro tier)")
    ultra_requests: int = Field(default=200, ge=1, description="Max transcription requests per window (ultra tier)")
    ultra_window_seconds: int = Field(default=3600, ge=60, description="Rate limit window in seconds (ultra tier)")

    class Config:
        env_prefix = "TRANSCRIPTION_"

    def get_limits_for_tier(self, tier: str) -> tuple[int, int]:
        """Return (max_requests, window_seconds) for the given tier. Unknown tier defaults to free."""
        tier = (tier or "free").lower()
        if tier == "pro":
            return self.pro_requests, self.pro_window_seconds
        if tier == "ultra":
            return self.ultra_requests, self.ultra_window_seconds
        return self.free_requests, self.free_window_seconds


class TokenQuotaConfig(BaseSettings):
    """
    Per-tier daily token quotas (input_tokens + output_tokens from APIUsageLog).
    Configured via env (TOKEN_QUOTA_*). No admin panel.
    A value of 0 means unlimited.
    """
    free_tokens_per_day: int = Field(
        default=50_000, ge=0,
        description="Max tokens per 24h for free tier",
    )
    pro_tokens_per_day: int = Field(
        default=200_000, ge=0,
        description="Max tokens per 24h for pro tier",
    )
    ultra_tokens_per_day: int = Field(
        default=1_000_000, ge=0,
        description="Max tokens per 24h for ultra tier",
    )

    class Config:
        env_prefix = "TOKEN_QUOTA_"

    def get_limit_for_tier(self, tier: str) -> int:
        """Return daily token cap for the given tier. 0 = unlimited."""
        tier = (tier or "free").lower()
        if tier == "ultra":
            return self.ultra_tokens_per_day
        if tier == "pro":
            return self.pro_tokens_per_day
        return self.free_tokens_per_day


class StripeConfig(BaseSettings):
    """Configuration for Stripe payment integration."""

    secret_key: str = Field(
        default="",
        description="Stripe secret API key (sk_live_... or sk_test_...)",
        validation_alias=AliasChoices("STRIPE_SECRET_KEY"),
    )
    publishable_key: str = Field(
        default="",
        description="Stripe publishable API key (pk_live_... or pk_test_...)",
        validation_alias=AliasChoices("STRIPE_PUBLISHABLE_KEY"),
    )
    webhook_secret: str = Field(
        default="",
        description="Stripe webhook signing secret (whsec_...)",
        validation_alias=AliasChoices("STRIPE_WEBHOOK_SECRET"),
    )
    price_pro_monthly: str = Field(
        default="",
        description="Stripe Price ID for Pro Monthly plan (price_...)",
        validation_alias=AliasChoices("STRIPE_PRICE_PRO_MONTHLY"),
    )
    price_ultra_monthly: str = Field(
        default="",
        description="Stripe Price ID for Ultra Monthly plan (price_...)",
        validation_alias=AliasChoices("STRIPE_PRICE_ULTRA_MONTHLY"),
    )
    trial_days: int = Field(
        default=14,
        description="Number of trial days for new subscriptions",
        validation_alias=AliasChoices("STRIPE_TRIAL_DAYS"),
    )

    class Config:
        env_prefix = "STRIPE_"
        env_file = _default_env_path()
        env_file_encoding = "utf-8"
        extra = "ignore"


class AppConfig(BaseSettings):
    """Main application configuration combining all sub-configs."""

    recorder: RecorderConfig = Field(default_factory=RecorderConfig)
    silence: SilenceRemovalConfig = Field(default_factory=SilenceRemovalConfig)
    loudness: LoudnessConfig = Field(default_factory=LoudnessConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    speech_guard: SpeechGuardConfig = Field(default_factory=SpeechGuardConfig)
    transcription_limits: TranscriptionRateLimitConfig = Field(default_factory=TranscriptionRateLimitConfig)
    token_quotas: TokenQuotaConfig = Field(default_factory=TokenQuotaConfig)
    stripe: StripeConfig = Field(default_factory=StripeConfig)


def _apply_db_overrides(config: AppConfig) -> AppConfig:
    """
    Apply database overrides from GlobalSettings.

    Overrides are keyed by dotted path, e.g.:
    - 'recorder.max_duration' -> config.recorder.max_duration
    - 'token_quotas.free_tokens_per_day' -> config.token_quotas.free_tokens_per_day

    Args:
        config: The base AppConfig instance

    Returns:
        The config with database overrides applied
    """
    try:
        from src.accounts.models import GlobalSettings

        overrides = {s.key: s.value for s in GlobalSettings.objects.all()}

        for key, value in overrides.items():
            parts = key.split(".")
            if len(parts) == 2:
                section, attr = parts
                if hasattr(config, section):
                    section_obj = getattr(config, section)
                    if hasattr(section_obj, attr):
                        try:
                            setattr(section_obj, attr, value)
                            logger.debug(f"Applied DB override: {key} = {value}")
                        except Exception as e:
                            logger.warning(f"Failed to apply DB override {key}: {e}")

        return config

    except Exception as e:
        logger.debug(f"Could not apply DB overrides (this is normal during migrations): {e}")
        return config


@lru_cache()
def get_config() -> AppConfig:
    """
    Get the application configuration.
    
    Returns a cached AppConfig instance with:
    - Default values
    - Environment variable overrides
    - Database overrides from GlobalSettings
    
    Returns:
        AppConfig instance
    """
    config = AppConfig()
    return _apply_db_overrides(config)


def reload_config() -> AppConfig:
    """
    Reload the configuration.
    
    Clears the cache and returns a fresh config with all overrides applied.
    Use this after updating GlobalSettings to get the new values.
    
    Returns:
        Fresh AppConfig instance
    """
    get_config.cache_clear()
    return get_config()
