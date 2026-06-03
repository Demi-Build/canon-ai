"""canon.backends — LLM, image, music, and SFX backend protocols, registry, and test utilities.

Optional concrete backends are re-exported lazily below. If their extra
dependencies are not installed the name resolves to ``None`` rather than
raising an ``ImportError`` at module load time. Downstream code that needs
the real ``ImportError`` (e.g. to surface a helpful install message) should
import directly::

    from canon.backends.image_fal import FalImageBackend    # raises ImportError if absent
    from canon.backends.image_local import LocalImageBackend  # raises ImportError if absent

Code that only needs to check availability can test for ``None``::

    from canon.backends import FalImageBackend
    if FalImageBackend is None:
        print("fal-client not installed; skipping image generation")
"""

from canon.backends.base import ImageBackend, LLMBackend, MusicBackend, SFXBackend
from canon.backends.registry import BackendRegistry
from canon.backends.testing import (
    FakeImageBackend,
    FakeLLMBackend,
    FakeMusicBackend,
    FakeSFXBackend,
)

# ---------------------------------------------------------------------------
# Optional concrete backends — only re-export if importable; lazy
# ---------------------------------------------------------------------------

try:
    from canon.backends.image_fal import FalImageBackend
except ImportError:
    FalImageBackend = None  # type: ignore[assignment,misc]

try:
    from canon.backends.image_local import LocalImageBackend
except ImportError:
    LocalImageBackend = None  # type: ignore[assignment,misc]

try:
    from canon.backends.music_lyria import LyriaMusicBackend
except ImportError:
    LyriaMusicBackend = None  # type: ignore[assignment,misc]

try:
    from canon.backends.sfx_elevenlabs import ElevenLabsSFXBackend
except ImportError:
    ElevenLabsSFXBackend = None  # type: ignore[assignment,misc]

__all__ = [
    "LLMBackend",
    "ImageBackend",
    "MusicBackend",
    "SFXBackend",
    "BackendRegistry",
    "FakeLLMBackend",
    "FakeImageBackend",
    "FakeMusicBackend",
    "FakeSFXBackend",
    "FalImageBackend",
    "LocalImageBackend",
    "LyriaMusicBackend",
    "ElevenLabsSFXBackend",
]
