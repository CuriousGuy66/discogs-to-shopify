from __future__ import annotations

from pathlib import Path
from typing import Protocol


class EtchingReader(Protocol):
    """Interface for extracting runout/etching text from an image."""

    def read(self, image_path: Path) -> str | None:
        ...
