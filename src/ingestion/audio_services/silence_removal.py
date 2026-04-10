"""
Silence Removal Service

Removes silence from audio files using ffmpeg.
Adapted from src_sample/audio_rm_silence for use in the ingestion pipeline.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple
import re

try:
    from src.common.config import get_config
except ImportError:
    # Fallback for standalone use
    def get_config():
        class Config:
            class Silence:
                enabled = True
                threshold_db = -35.0
                min_duration = 0.5
                padding_ms = 50.0
                ffmpeg_timeout = 120
                supported_formats = [".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".wma", ".webm"]
                output_format = ".mp3"
                mp3_quality = 2
            silence = Silence()
        return Config()

logger = logging.getLogger(__name__)


class SilenceRemover:
    """
    Audio silence removal processor using ffmpeg.
    
    Removes silence gaps from the middle and end of audio files
    while preserving the beginning (including any intentional initial
    silence and opening speech).
    """
    
    def __init__(self):
        """Initialize the silence remover with configuration."""
        self.config = get_config().silence
        self.ffmpeg_available = False
        self.ffmpeg_path = None
        
        self._check_ffmpeg_available()
        
        if not self.ffmpeg_available:
            logger.warning("FFmpeg is not available - silence removal will be disabled")
    
    def _check_ffmpeg_available(self) -> bool:
        """Check if ffmpeg is installed and accessible."""
        # Check if ffmpeg is in PATH
        ffmpeg_path = shutil.which('ffmpeg')
        if ffmpeg_path:
            self.ffmpeg_path = ffmpeg_path
            logger.debug(f"Found ffmpeg in PATH: {ffmpeg_path}")
        else:
            # Search common installation directories
            common_paths = [
                'C:\\ffmpeg\\bin\\ffmpeg.exe',
                'C:\\Program Files\\ffmpeg\\bin\\ffmpeg.exe',
                '/usr/bin/ffmpeg',
                '/usr/local/bin/ffmpeg',
                '/opt/homebrew/bin/ffmpeg',
            ]
            
            for path in common_paths:
                if Path(path).exists():
                    self.ffmpeg_path = path
                    logger.debug(f"Found ffmpeg at: {path}")
                    break
        
        if not self.ffmpeg_path:
            self.ffmpeg_available = False
            return False
        
        # Verify ffmpeg can execute
        try:
            result = subprocess.run(
                [self.ffmpeg_path, '-version'],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                logger.info(f"FFmpeg is available: {self.ffmpeg_path}")
                self.ffmpeg_available = True
                return True
            else:
                logger.error(f"FFmpeg execution failed: {result.stderr}")
                self.ffmpeg_available = False
                return False
                
        except Exception as e:
            logger.error(f"Error checking ffmpeg: {e}")
            self.ffmpeg_available = False
            return False
    
    def _is_audio_file(self, file_path: Path) -> bool:
        """Check if a file is a supported audio format."""
        if not file_path.exists() or not file_path.is_file():
            return False
        
        file_ext = file_path.suffix.lower()
        return file_ext in self.config.supported_formats
    
    def _get_codec_args(self, output_format: str) -> List[str]:
        """Get FFmpeg codec arguments for the specified output format."""
        output_ext = output_format.lower()
        
        if output_ext == '.mp3':
            return ['-codec:a', 'libmp3lame', '-q:a', str(self.config.mp3_quality)]
        elif output_ext == '.wav':
            return ['-codec:a', 'pcm_s16le']
        elif output_ext == '.ogg':
            return ['-codec:a', 'libvorbis', '-q:a', '4']
        elif output_ext == '.flac':
            return ['-codec:a', 'flac']
        elif output_ext in ('.aac', '.m4a'):
            return ['-codec:a', 'aac', '-b:a', '192k']
        elif output_ext == '.webm':
            return ['-codec:a', 'libopus', '-b:a', '128k']
        else:
            return []
    
    def process_file(self, audio_file_path: Path, output_path: Optional[Path] = None) -> Optional[Path]:
        """
        Process an audio file to remove silence gaps.
        
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
            return audio_file_path  # Return original
        
        if not self.config.enabled:
            logger.debug("Silence removal is disabled in configuration")
            return audio_file_path
        
        if not self.ffmpeg_available:
            logger.error("FFmpeg is not available - cannot process audio file")
            return None
        
        try:
            # Create temporary output file
            suffix = output_path.suffix if output_path else audio_file_path.suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                temp_path = Path(temp_file.name)
            
            # Get codec arguments
            codec_args = self._get_codec_args(suffix)
            
            # Build ffmpeg command for silence removal
            # start_periods=0 preserves the beginning
            # stop_periods=-1 removes all silence gaps from middle/end
            cmd = [
                self.ffmpeg_path,
                '-i', str(audio_file_path),
                '-af', (
                    f'silenceremove='
                    f'start_periods=0:'
                    f'stop_periods=-1:'
                    f'start_threshold={self.config.threshold_db}dB:'
                    f'stop_threshold={self.config.threshold_db}dB:'
                    f'start_duration={self.config.min_duration}:'
                    f'stop_duration={self.config.min_duration}:'
                    f'detection=peak'
                ),
                *codec_args,
                '-y',
                str(temp_path)
            ]
            
            logger.debug(f"Running ffmpeg command: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.config.ffmpeg_timeout
            )
            
            if result.returncode != 0:
                logger.error(f"FFmpeg silence removal failed: {result.stderr}")
                temp_path.unlink(missing_ok=True)
                return None
            
            # Verify output file
            if not temp_path.exists() or temp_path.stat().st_size == 0:
                logger.error("Output file was not created or is empty")
                temp_path.unlink(missing_ok=True)
                return None
            
            # Move to final location
            final_path = output_path or audio_file_path
            shutil.move(str(temp_path), str(final_path))
            
            logger.info(f"Successfully removed silence from: {audio_file_path}")
            return final_path
            
        except subprocess.TimeoutExpired:
            logger.error(f"Silence removal timed out after {self.config.ffmpeg_timeout} seconds")
            return None
        except Exception as e:
            logger.error(f"Error processing audio file: {e}")
            return None
    
    def process_bytes(
        self,
        input_bytes: bytes,
        input_format: str = '.webm',
        output_format: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Process audio bytes to remove silence.
        
        Args:
            input_bytes: Raw audio file bytes
            input_format: Input file extension (e.g., '.webm', '.wav')
            output_format: Output file extension (defaults to config)
            
        Returns:
            Processed audio as bytes, or None if processing failed
        """
        if not self.ffmpeg_available:
            logger.error("FFmpeg is not available - cannot process audio")
            return None
        
        if not self.config.enabled:
            logger.debug("Silence removal is disabled")
            return input_bytes  # Return unchanged
        
        output_format = output_format or self.config.output_format
        
        # Ensure formats have leading dot
        if not input_format.startswith('.'):
            input_format = f'.{input_format}'
        if not output_format.startswith('.'):
            output_format = f'.{output_format}'
        
        input_temp = None
        output_temp = None
        
        try:
            # Write input to temp file
            with tempfile.NamedTemporaryFile(suffix=input_format, delete=False) as f:
                f.write(input_bytes)
                input_temp = Path(f.name)
            
            # Create output temp file
            with tempfile.NamedTemporaryFile(suffix=output_format, delete=False) as f:
                output_temp = Path(f.name)
            
            # Process
            result_path = self.process_file(input_temp, output_temp)
            
            if result_path and result_path.exists():
                with open(result_path, 'rb') as f:
                    return f.read()
            
            return None
            
        finally:
            # Cleanup
            if input_temp and input_temp.exists():
                input_temp.unlink(missing_ok=True)
            if output_temp and output_temp.exists():
                output_temp.unlink(missing_ok=True)
