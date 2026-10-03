import os
import tempfile
from pathlib import Path
from threading import RLock

from app.rag.handbook import HandbookIndex


class HandbookStore:
    """Persist validated updates and refresh each worker from the shared file."""

    def __init__(self, path: Path, chunk_chars: int):
        if path.suffix != ".ts":
            raise ValueError("HANDBOOK_PATH must point to a .ts file")
        self.path = path
        self.chunk_chars = chunk_chars
        self._lock = RLock()
        self._version = None
        self._index = None
        self.get_index()

    @staticmethod
    def _signature(stat):
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def get_index(self) -> HandbookIndex:
        with self._lock:
            if self._signature(self.path.stat()) != self._version:
                with self.path.open(encoding="utf-8") as source:
                    version = self._signature(os.fstat(source.fileno()))
                    index = HandbookIndex.from_source(
                        source.read(), self.path.name, self.chunk_chars
                    )
                self._index, self._version = index, version
            return self._index

    def update(self, content: bytes) -> HandbookIndex:
        # Build before touching the live file or index; never execute TypeScript.
        index = HandbookIndex.from_source(
            content.decode("utf-8-sig"), self.path.name, self.chunk_chars
        )
        with self._lock:
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as output:
                    temporary_path = Path(output.name)
                    output.write(content)
                    output.flush()
                    os.fchmod(output.fileno(), self.path.stat().st_mode & 0o777)
                    os.fsync(output.fileno())
                    version = self._signature(os.fstat(output.fileno()))
                os.replace(temporary_path, self.path)
                self._index, self._version = index, version
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        return index
