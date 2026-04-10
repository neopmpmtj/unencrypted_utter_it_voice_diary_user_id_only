"""
Loudness Normalization Service

Applies EBU R128 loudness normalization using FFmpeg's loudnorm filter (two-pass).
Ensures consistent perceived loudness across recordings for better transcription quality.
"""

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

try:
    from src.common.config import get_config
except ImportError:

    def get_config():
        class Config:
            class Loudness:
                enabled = True
                target_i = -16.0
                target_tp = -1.5
                target_lra = 11.0
                ffmpeg_timeout = 180

            class Silence:
                supported_formats = [".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma", ".webm"]
                output_format = ".mp3"
                mp3_quality = 2

            loudness = Loudness()
            silence = Silence()

        return Config()

logger = logging.getLogger(__name__)


def _get_codec_args(suffix: str, mp3_quality: int = 2) -> List[str]:
    """Get FFmpeg codec arguments for the specified output format."""
    output_ext = suffix.lower()
    if output_ext == ".mp3":
        return ["-codec:a", "libmp3lame", "-q:a", str(mp3_quality)]
    if output_ext == ".wav":
        return ["-codec:a", "pcm_s16le"]
    if output_ext == ".ogg":
        return ["-codec:a", "libvorbis", "-q:a", "4"]
    if output_ext == ".flac":
        return ["-codec:a", "flac"]
    if output_ext in (".aac", ".m4a"):
        return ["-codec:a", "aac", "-b:a", "192k"]
    if output_ext == ".webm":
        return ["-codec:a", "libopus", "-b:a", "128k"]
    return []


class LoudnessNormalizer:
    """
    EBU R128 loudness normalizer using FFmpeg loudnorm (two-pass).
    Targets consistent perceived loudness for voice diary recordings.
    """

    def __init__(self):
        self.config = get_config().loudness
        self.silence_config = get_config().silence
        self.ffmpeg_available = False
        self.ffmpeg_path = None
        self._check_ffmpeg_available()

        if not self.ffmpeg_available:
            logger.warning("FFmpeg is not available - loudness normalization will be disabled")

    def _check_ffmpeg_available(self) -> bool:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            self.ffmpeg_path = ffmpeg_path
            try:
                result = subprocess.run(
                    [ffmpeg_path, "-version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    self.ffmpeg_available = True
                    return True
            except Exception as e:
                logger.error(f"Error checking ffmpeg: {e}")
        self.ffmpeg_available = False
        return False

    def _is_audio_file(self, file_path: Path) -> bool:
        if not file_path.exists() or not file_path.is_file():
            return False
        return file_path.suffix.lower() in self.silence_config.supported_formats

    def _parse_loudnorm_stats(self, data: dict) -> Optional[dict]:
        """Convert loudnorm JSON to second-pass params. Returns dict or None."""
        try:
            input_i = data.get("input_i", data.get("input_I", "-24"))
            input_tp = data.get("input_tp", data.get("input_TP", "-2"))
            input_lra = data.get("input_lra", data.get("input_LRA", "7"))
            input_thresh = data.get("input_thresh", "-30")
            target_offset = data.get("target_offset", data.get("offset", "0"))
            return {
                "measured_I": float(input_i),
                "measured_TP": float(input_tp),
                "measured_LRA": float(input_lra),
                "measured_thresh": float(input_thresh),
                "offset": float(target_offset),
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse loudnorm stats: {e}")
            return None

    def process_file(self, audio_file_path: Path, output_path: Optional[Path] = None) -> Optional[Path]:
        """
        Apply EBU R128 loudness normalization (two-pass) to an audio file.

        Args:
            audio_file_path: Path to the input audio file
            output_path: Optional output path (defaults to replacing original)

        Returns:
            Path to the processed file, or None if processing failed
        """
        audio_file_path = Path(audio_file_path)

        if not audio_file_path.exists():
            logger.error(f"Audio file does not exist: {audio_file_path}")
            return None

        if not self._is_audio_file(audio_file_path):
            logger.warning(f"File is not a supported audio format: {audio_file_path}")
            return audio_file_path

        if not self.config.enabled:
            logger.debug("Loudness normalization is disabled in configuration")
            return audio_file_path

        if not self.ffmpeg_available:
            logger.error("FFmpeg is not available - cannot normalize audio")
            return None

        suffix = (output_path or audio_file_path).suffix
        codec_args = _get_codec_args(suffix, self.silence_config.mp3_quality)
        timeout = self.config.ffmpeg_timeout

        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as stats_file:
                stats_path = Path(stats_file.name)

            pass1_cmd = [
                self.ffmpeg_path,
                "-i",
                str(audio_file_path),
                "-af",
                (
                    f"loudnorm=I={self.config.target_i}:TP={self.config.target_tp}:"
                    f"LRA={self.config.target_lra}:print_format=json:stats_file={stats_path}"
                ),
                "-f",
                "null",
                "-",
            ]

            result = subprocess.run(
                pass1_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            stats_data = None
            if stats_path.exists():
                try:
                    with open(stats_path) as f:
                        raw = json.load(f)
                    stats_data = self._parse_loudnorm_stats(raw)
                except json.JSONDecodeError as e:
                    logger.warning(f"Loudnorm stats file invalid JSON: {e}")
                finally:
                    stats_path.unlink(missing_ok=True)

            if not stats_data:
                logger.warning("Could not obtain loudnorm stats; skipping normalization")
                return audio_file_path

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                temp_path = Path(tmp.name)

            pass2_cmd = [
                self.ffmpeg_path,
                "-i",
                str(audio_file_path),
                "-af",
                (
                    f"loudnorm=I={self.config.target_i}:TP={self.config.target_tp}:"
                    f"LRA={self.config.target_lra}:"
                    f"measured_I={stats_data['measured_I']}:"
                    f"measured_TP={stats_data['measured_TP']}:"
                    f"measured_LRA={stats_data['measured_LRA']}:"
                    f"measured_thresh={stats_data['measured_thresh']}:"
                    f"offset={stats_data['offset']}:linear=true"
                ),
                *codec_args,
                "-y",
                str(temp_path),
            ]

            result = subprocess.run(
                pass2_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg loudness normalization failed: {result.stderr}")
                temp_path.unlink(missing_ok=True)
                return None

            if not temp_path.exists() or temp_path.stat().st_size == 0:
                logger.error("Output file was not created or is empty")
                temp_path.unlink(missing_ok=True)
                return None

            final_path = output_path or audio_file_path
            shutil.move(str(temp_path), str(final_path))
            logger.info(f"Successfully normalized loudness for: {audio_file_path}")
            return final_path

        except subprocess.TimeoutExpired:
            logger.error(f"Loudness normalization timed out after {timeout} seconds")
            return None
        except Exception as e:
            logger.error(f"Error normalizing audio file: {e}")
            return None
