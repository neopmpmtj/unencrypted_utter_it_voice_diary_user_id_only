"""
Audio Chunking Service

Splits large audio files into smaller chunks for API compliance.
OpenAI Whisper API has a 25MB limit, so files larger than ~20MB
are split into overlapping chunks.
"""

import logging
from pathlib import Path
from typing import List, Optional

from pydub import AudioSegment

try:
    from src.common.config import get_config
except ImportError:
    # Fallback for standalone use
    def get_config():
        class Config:
            class Chunking:
                enabled = True
                max_chunk_size_mb = 20
                overlap_seconds = 1.0
            chunking = Chunking()
        return Config()

logger = logging.getLogger(__name__)


class AudioChunker:
    """
    Audio file chunker for handling large files.
    
    Splits audio files larger than the configured max size into
    overlapping chunks to avoid cutting words at boundaries.
    """
    
    def __init__(self):
        """Initialize the chunker with configuration."""
        self.config = get_config().chunking
    
    def needs_chunking(self, file_path: Path) -> bool:
        """
        Check if a file exceeds the max chunk size and needs splitting.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            True if file needs to be chunked
        """
        if not self.config.enabled:
            return False
        
        file_path = Path(file_path)
        if not file_path.exists():
            return False
        
        size_mb = file_path.stat().st_size / (1024 * 1024)
        return size_mb > self.config.max_chunk_size_mb
    
    def get_audio_duration(self, file_path: Path) -> float:
        """
        Get the duration of an audio file in seconds.
        
        Args:
            file_path: Path to the audio file
            
        Returns:
            Duration in seconds
        """
        try:
            audio = AudioSegment.from_file(str(file_path))
            return len(audio) / 1000.0  # pydub uses milliseconds
        except Exception as e:
            logger.error(f"Error getting audio duration: {e}")
            return 0.0
    
    def split_audio(self, input_path: Path, output_dir: Path) -> List[Path]:
        """
        Split an audio file into chunks under max_chunk_size_mb.
        
        Uses overlapping chunks to avoid cutting words at boundaries.
        
        Args:
            input_path: Path to the input audio file
            output_dir: Directory to save chunk files
            
        Returns:
            List of chunk file paths in order
        """
        input_path = Path(input_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        if not input_path.exists():
            logger.error(f"Input file does not exist: {input_path}")
            return []
        
        try:
            audio = AudioSegment.from_file(str(input_path))
            duration_ms = len(audio)
            
            # Calculate chunk duration based on file size ratio
            file_size_mb = input_path.stat().st_size / (1024 * 1024)
            target_chunk_mb = self.config.max_chunk_size_mb * 0.9  # 90% for safety margin
            chunk_ratio = target_chunk_mb / file_size_mb
            chunk_duration_ms = int(duration_ms * chunk_ratio)
            
            # Ensure reasonable chunk size (at least 30 seconds)
            min_chunk_ms = 30 * 1000
            chunk_duration_ms = max(chunk_duration_ms, min_chunk_ms)
            
            overlap_ms = int(self.config.overlap_seconds * 1000)
            
            logger.info(
                f"Splitting {file_size_mb:.1f}MB audio into ~{chunk_duration_ms/1000:.0f}s chunks "
                f"with {overlap_ms/1000:.1f}s overlap"
            )
            
            chunks = []
            start = 0
            chunk_num = 0
            
            while start < duration_ms:
                end = min(start + chunk_duration_ms, duration_ms)
                chunk = audio[start:end]
                
                # Generate chunk filename
                chunk_path = output_dir / f"chunk_{chunk_num:03d}{input_path.suffix}"
                
                # Export chunk
                chunk.export(
                    str(chunk_path),
                    format=input_path.suffix.lstrip('.')
                )
                
                chunks.append(chunk_path)
                logger.debug(f"Created chunk {chunk_num}: {start/1000:.1f}s - {end/1000:.1f}s")
                
                chunk_num += 1
                
                # Next chunk starts with overlap (to avoid cutting words)
                start = end - overlap_ms
                
                # Prevent infinite loop
                if start >= duration_ms:
                    break
            
            logger.info(f"Split audio into {len(chunks)} chunks")
            return chunks
            
        except Exception as e:
            logger.error(f"Error splitting audio: {e}")
            return []
    
    def merge_transcriptions(
        self,
        transcriptions: List[str],
        overlap_seconds: Optional[float] = None
    ) -> str:
        """
        Concatenate transcription chunks, handling overlap regions.
        
        Simple approach: join with space, rely on overlap for word boundaries.
        More sophisticated deduplication could be added if needed.
        
        Args:
            transcriptions: List of transcription text chunks in order
            overlap_seconds: Overlap duration (unused currently, for future dedup)
            
        Returns:
            Merged transcription text
        """
        if not transcriptions:
            return ""
        
        if len(transcriptions) == 1:
            return transcriptions[0]
        
        # Simple join - the overlap helps ensure words aren't cut
        # A more sophisticated approach would detect and remove duplicate
        # text at chunk boundaries, but this works well in practice
        merged = " ".join(t.strip() for t in transcriptions if t.strip())
        
        # Clean up any double spaces
        while "  " in merged:
            merged = merged.replace("  ", " ")
        
        return merged
    
    def cleanup_chunks(self, chunk_paths: List[Path]) -> None:
        """
        Remove temporary chunk files.
        
        Args:
            chunk_paths: List of chunk file paths to delete
        """
        for path in chunk_paths:
            try:
                path = Path(path)
                if path.exists():
                    path.unlink()
                    logger.debug(f"Deleted chunk: {path}")
            except Exception as e:
                logger.warning(f"Failed to delete chunk {path}: {e}")
