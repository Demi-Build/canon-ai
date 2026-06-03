"""Tests for MusicBackend protocol, FakeMusicBackend, and registry music methods."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from canon.backends import BackendRegistry, FakeMusicBackend, MusicBackend

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestMusicBackendProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        fake = FakeMusicBackend()
        assert isinstance(fake, MusicBackend)

    def test_fake_has_all_four_methods(self) -> None:
        fake = FakeMusicBackend()
        assert callable(getattr(fake, "generate", None))
        assert callable(getattr(fake, "generate_async", None))
        assert callable(getattr(fake, "generate_and_save", None))
        assert callable(getattr(fake, "generate_and_save_async", None))

    def test_object_missing_methods_does_not_satisfy_protocol(self) -> None:
        class NotAMusicBackend:
            pass

        assert not isinstance(NotAMusicBackend(), MusicBackend)

    def test_partial_implementation_does_not_satisfy_protocol(self) -> None:
        class PartialBackend:
            def generate(self, prompt: str, duration_seconds: int) -> bytes:
                return b""

        # Missing generate_async, generate_and_save, generate_and_save_async
        assert not isinstance(PartialBackend(), MusicBackend)

    def test_custom_full_implementation_satisfies_protocol(self) -> None:
        class MyMusicBackend:
            def generate(self, prompt: str, duration_seconds: int) -> bytes:
                return b"music"

            async def generate_async(self, prompt: str, duration_seconds: int) -> bytes:
                return b"music"

            def generate_and_save(
                self, prompt: str, filepath: str, duration_seconds: int
            ) -> bool:
                return True

            async def generate_and_save_async(
                self, prompt: str, filepath: str, duration_seconds: int
            ) -> bool:
                return True

        assert isinstance(MyMusicBackend(), MusicBackend)


# ---------------------------------------------------------------------------
# FakeMusicBackend — generate
# ---------------------------------------------------------------------------


class TestFakeMusicBackendGenerate:
    def test_generate_returns_bytes(self) -> None:
        fake = FakeMusicBackend()
        result = fake.generate("epic battle music", 30)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_records_call(self) -> None:
        fake = FakeMusicBackend()
        fake.generate("epic battle music", 30)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "epic battle music"
        assert call["duration_seconds"] == 30

    def test_calls_starts_empty(self) -> None:
        fake = FakeMusicBackend()
        assert fake.calls == []

    def test_multiple_generates_accumulate_calls(self) -> None:
        fake = FakeMusicBackend()
        fake.generate("ambient dungeon", 60)
        fake.generate("victory fanfare", 10)
        assert len(fake.calls) == 2
        assert fake.calls[0]["prompt"] == "ambient dungeon"
        assert fake.calls[1]["prompt"] == "victory fanfare"

    def test_different_prompts_return_same_bytes(self) -> None:
        """Placeholder is deterministic regardless of prompt."""
        fake = FakeMusicBackend()
        b1 = fake.generate("spooky music", 30)
        b2 = fake.generate("happy music", 60)
        assert b1 == b2


# ---------------------------------------------------------------------------
# FakeMusicBackend — generate_and_save
# ---------------------------------------------------------------------------


class TestFakeMusicBackendGenerateAndSave:
    def test_generate_and_save_creates_file(self, tmp_path: Path) -> None:
        fake = FakeMusicBackend()
        filepath = str(tmp_path / "track.mp3")
        result = fake.generate_and_save("ambient music", filepath, 30)
        assert result is True
        assert Path(filepath).exists()

    def test_generate_and_save_writes_non_empty_bytes(self, tmp_path: Path) -> None:
        fake = FakeMusicBackend()
        filepath = str(tmp_path / "track.mp3")
        fake.generate_and_save("ambient music", filepath, 30)
        assert Path(filepath).stat().st_size > 0

    def test_generate_and_save_records_call(self, tmp_path: Path) -> None:
        fake = FakeMusicBackend()
        filepath = str(tmp_path / "track.mp3")
        fake.generate_and_save("forest ambience", filepath, 45)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "forest ambience"
        assert call["filepath"] == filepath
        assert call["duration_seconds"] == 45

    def test_generate_and_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        fake = FakeMusicBackend()
        nested = tmp_path / "music" / "ambient" / "track.mp3"
        fake.generate_and_save("cave drip", str(nested), 30)
        assert nested.exists()

    def test_generate_and_save_bytes_match_generate(self, tmp_path: Path) -> None:
        """File content should equal what generate() returns."""
        fake = FakeMusicBackend()
        filepath = str(tmp_path / "track.mp3")
        expected = fake.generate("test", 30)
        fake.calls.clear()
        fake.generate_and_save("test", filepath, 30)
        written = Path(filepath).read_bytes()
        assert written == expected


# ---------------------------------------------------------------------------
# FakeMusicBackend — async variants
# ---------------------------------------------------------------------------


class TestFakeMusicBackendAsync:
    def test_generate_async_returns_bytes(self) -> None:
        fake = FakeMusicBackend()
        result = asyncio.run(fake.generate_async("tavern music", 30))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_async_same_bytes_as_sync(self) -> None:
        fake = FakeMusicBackend()
        sync_result = fake.generate("tavern music", 30)
        fake.calls.clear()
        async_result = asyncio.run(fake.generate_async("tavern music", 30))
        assert sync_result == async_result

    def test_generate_and_save_async_creates_file(self, tmp_path: Path) -> None:
        fake = FakeMusicBackend()
        filepath = str(tmp_path / "async_track.mp3")
        result = asyncio.run(
            fake.generate_and_save_async("battle theme", filepath, 60)
        )
        assert result is True
        assert Path(filepath).exists()
        assert Path(filepath).stat().st_size > 0

    def test_generate_and_save_async_creates_parent_dirs(self, tmp_path: Path) -> None:
        fake = FakeMusicBackend()
        nested = tmp_path / "async" / "music" / "track.mp3"
        asyncio.run(
            fake.generate_and_save_async("wind ambience", str(nested), 30)
        )
        assert nested.exists()


# ---------------------------------------------------------------------------
# FakeMusicBackend — custom placeholder
# ---------------------------------------------------------------------------


class TestFakeMusicBackendPlaceholder:
    def test_uses_placeholder_file_when_provided(self, tmp_path: Path) -> None:
        placeholder = tmp_path / "custom.mp3"
        placeholder.write_bytes(b"FAKEMP3DATA")
        fake = FakeMusicBackend(placeholder=placeholder)
        result = fake.generate("anything", 30)
        assert result == b"FAKEMP3DATA"

    def test_uses_asset_dir_mp3_when_provided(self, tmp_path: Path) -> None:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        (asset_dir / "track.mp3").write_bytes(b"DIRMP3DATA")
        fake = FakeMusicBackend(asset_dir=asset_dir)
        result = fake.generate("anything", 30)
        assert result == b"DIRMP3DATA"

    def test_placeholder_takes_precedence_over_asset_dir(self, tmp_path: Path) -> None:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        (asset_dir / "track.mp3").write_bytes(b"DIRMP3")
        placeholder = tmp_path / "custom.mp3"
        placeholder.write_bytes(b"PLACEHOLDERMP3")
        fake = FakeMusicBackend(asset_dir=asset_dir, placeholder=placeholder)
        result = fake.generate("anything", 30)
        assert result == b"PLACEHOLDERMP3"

    def test_falls_back_to_min_mp3_when_no_assets(self) -> None:
        from canon.backends.testing import _MIN_MP3_BYTES

        fake = FakeMusicBackend()
        result = fake.generate("anything", 30)
        assert result == _MIN_MP3_BYTES
        # Valid silent MP3: starts with an MPEG-1 Layer III frame sync.
        assert result[:2] == b"\xff\xfb"


# ---------------------------------------------------------------------------
# Registry — music
# ---------------------------------------------------------------------------


class TestRegistryMusic:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_register_and_retrieve(self) -> None:
        BackendRegistry.register_music("fake", lambda: FakeMusicBackend())
        backend = BackendRegistry.music("fake")
        assert isinstance(backend, FakeMusicBackend)

    def test_instance_is_cached(self) -> None:
        BackendRegistry.register_music("fake", lambda: FakeMusicBackend())
        first = BackendRegistry.music("fake")
        second = BackendRegistry.music("fake")
        assert first is second

    def test_unknown_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="lyria"):
            BackendRegistry.music("lyria")

    def test_key_error_message_is_descriptive(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            BackendRegistry.music("mymusic")
        assert "mymusic" in str(exc_info.value)

    def test_re_register_clears_cached_instance(self) -> None:
        BackendRegistry.register_music("fake", lambda: FakeMusicBackend())
        first = BackendRegistry.music("fake")
        BackendRegistry.register_music("fake", lambda: FakeMusicBackend())
        second = BackendRegistry.music("fake")
        assert first is not second

    def test_reset_clears_music_state(self) -> None:
        BackendRegistry.register_music("fake", lambda: FakeMusicBackend())
        _ = BackendRegistry.music("fake")
        BackendRegistry.reset()
        with pytest.raises(KeyError):
            BackendRegistry.music("fake")

    def test_factory_called_once(self) -> None:
        call_count = [0]

        def factory() -> FakeMusicBackend:
            call_count[0] += 1
            return FakeMusicBackend()

        BackendRegistry.register_music("counted", factory)
        BackendRegistry.music("counted")
        BackendRegistry.music("counted")
        assert call_count[0] == 1

    def test_music_and_image_backends_coexist(self) -> None:
        from canon.backends import FakeImageBackend

        BackendRegistry.register_music("mus", lambda: FakeMusicBackend())
        BackendRegistry.register_image("img", lambda: FakeImageBackend())
        assert isinstance(BackendRegistry.music("mus"), FakeMusicBackend)
        assert isinstance(BackendRegistry.image("img"), FakeImageBackend)
