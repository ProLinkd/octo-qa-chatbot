import os
import tempfile
from pathlib import Path
from threading import RLock

from app.rag.handbook import HandbookIndex


class HandbookStore:
    """Persist validated updates and refresh each worker from the shared file."""

    def __init__(self, path: Path, chunk_chars: int, training_path: Path | None = None):
        if path.suffix != ".ts":
            raise ValueError("HANDBOOK_PATH must point to a .ts file")
        self.path = path
        self.chunk_chars = chunk_chars
        self.training_path = training_path
        self._lock = RLock()
        self._version = None
        self._index = None
        self.get_index()

    @staticmethod
    def _signature(stat):
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def _read_training(self):
        if self.training_path is None:
            return None, None
        with self.training_path.open(encoding="utf-8-sig") as source:
            version = self._signature(os.fstat(source.fileno()))
            return source.read(), version

    def _build_index(self, source, training_source):
        return HandbookIndex.from_source(
            source, self.path.name, self.chunk_chars,
            training_source=training_source,
            training_filename=self.training_path.name if self.training_path else "trainingvideo.txt",
        )

    def get_index(self) -> HandbookIndex:
        with self._lock:
            version = (
                self._signature(self.path.stat()),
                self._signature(self.training_path.stat()) if self.training_path else None,
            )
            if version != self._version:
                with self.path.open(encoding="utf-8") as source:
                    training_source, training_version = self._read_training()
                    version = (self._signature(os.fstat(source.fileno())), training_version)
                    index = self._build_index(source.read(), training_source)
                self._index, self._version = index, version
            return self._index

    def update(self, content: bytes) -> HandbookIndex:
        # Build before touching the live file or index; never execute TypeScript.
        with self._lock:
            training_source, training_version = self._read_training()
            index = self._build_index(content.decode("utf-8-sig"), training_source)
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
                    temporary_path = Path(output.name)
                    output.write(content)
                    output.flush()
                    os.fchmod(output.fileno(), self.path.stat().st_mode & 0o777)
                    os.fsync(output.fileno())
                    version = (self._signature(os.fstat(output.fileno())), training_version)
                os.replace(temporary_path, self.path)
                self._index, self._version = index, version
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        return index
