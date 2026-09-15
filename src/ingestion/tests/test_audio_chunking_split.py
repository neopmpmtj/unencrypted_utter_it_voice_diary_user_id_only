"""Regression tests for AudioChunker.split_audio.

Covers the 2026-09-15 incident: once the final chunk was reached the split loop
kept re-issuing `start = end - overlap_ms` (which points back inside the file),
producing one-second chunks forever and filling the entire disk. The split must
always terminate after the final chunk (the one that ends at the audio duration).
"""

import tempfile
from pathlib import Path

from django.test import SimpleTestCase
from pydub import AudioSegment

from src.ingestion.audio_services.audio_chunking import AudioChunker


class SplitAudioTerminationTests(SimpleTestCase):
    def test_split_terminates_with_final_chunk(self):
        chunker = AudioChunker()
        old_max = chunker.config.max_chunk_size_mb
        old_overlap = chunker.config.overlap_seconds
        # Force a low size ratio so a small synthetic file yields several chunks.
        chunker.config.max_chunk_size_mb = 0.5
        chunker.config.overlap_seconds = 1.0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                audio_path = Path(tmp) / 'long.wav'
                AudioSegment.silent(duration=60_000, frame_rate=8000).export(audio_path, format='wav')
                out = Path(tmp) / 'chunks'
                chunks = chunker.split_audio(audio_path, out)

                # 60s @ ~29s chunks with 1s overlap → ~3 chunks; the old bug looped forever.
                self.assertGreaterEqual(len(chunks), 2)
                self.assertLessEqual(len(chunks), 5)
                for c in chunks:
                    self.assertTrue(c.exists(), f'missing chunk file: {c}')
        finally:
            chunker.config.max_chunk_size_mb = old_max
            chunker.config.overlap_seconds = old_overlap

    def test_split_returns_empty_for_missing_input(self):
        chunker = AudioChunker()
        with tempfile.TemporaryDirectory() as tmp:
            result = chunker.split_audio(Path(tmp) / 'nope.wav', Path(tmp) / 'out')
            self.assertEqual(result, [])
