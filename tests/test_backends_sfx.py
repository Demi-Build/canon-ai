"""Tests for SFXBackend protocol, FakeSFXBackend, and registry sfx methods."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from canon.backends import BackendRegistry, FakeSFXBackend, SFXBackend

# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestSFXBackendProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        fake = FakeSFXBackend()
        assert isinstance(fake, SFXBackend)

    def test_fake_has_all_four_methods(self) -> None:
        fake = FakeSFXBackend()
        assert callable(getattr(fake, "generate", None))
        assert callable(getattr(fake, "generate_async", None))
        assert callable(getattr(fake, "generate_and_save", None))
        assert callable(getattr(fake, "generate_and_save_async", None))

    def test_object_missing_methods_does_not_satisfy_protocol(self) -> None:
        class NotASFXBackend:
            pass

        assert not isinstance(NotASFXBackend(), SFXBackend)

    def test_partial_implementation_does_not_satisfy_protocol(self) -> None:
        class PartialBackend:
            def generate(
                self, prompt: str, duration_seconds: float, loop: bool
            ) -> bytes:
                return b""

        # Missing generate_async, generate_and_save, generate_and_save_async
        assert not isinstance(PartialBackend(), SFXBackend)

    def test_custom_full_implementation_satisfies_protocol(self) -> None:
        class MySFXBackend:
            def generate(
                self, prompt: str, duration_seconds: float, loop: bool
            ) -> bytes:
                return b"sfx"

            async def generate_async(
                self, prompt: str, duration_seconds: float, loop: bool
            ) -> bytes:
                return b"sfx"

            def generate_and_save(
                self,
                prompt: str,
                filepath: str,
                duration_seconds: float,
                loop: bool,
            ) -> bool:
                return True

            async def generate_and_save_async(
                self,
                prompt: str,
                filepath: str,
                duration_seconds: float,
                loop: bool,
            ) -> bool:
                return True

        assert isinstance(MySFXBackend(), SFXBackend)


# ---------------------------------------------------------------------------
# FakeSFXBackend — generate
# ---------------------------------------------------------------------------


class TestFakeSFXBackendGenerate:
    def test_generate_returns_bytes(self) -> None:
        fake = FakeSFXBackend()
        result = fake.generate("sword clashing", 2.5, False)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_records_call(self) -> None:
        fake = FakeSFXBackend()
        fake.generate("sword clashing", 2.5, False)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "sword clashing"
        assert call["duration_seconds"] == 2.5
        assert call["loop"] is False

    def test_generate_records_loop_true(self) -> None:
        fake = FakeSFXBackend()
        fake.generate("rain ambience", 5.0, True)
        assert fake.calls[0]["loop"] is True

    def test_calls_starts_empty(self) -> None:
        fake = FakeSFXBackend()
        assert fake.calls == []

    def test_multiple_generates_accumulate_calls(self) -> None:
        fake = FakeSFXBackend()
        fake.generate("footsteps", 1.0, False)
        fake.generate("thunder", 3.0, True)
        assert len(fake.calls) == 2
        assert fake.calls[0]["prompt"] == "footsteps"
        assert fake.calls[1]["prompt"] == "thunder"

    def test_different_prompts_return_same_bytes(self) -> None:
        """Placeholder is deterministic regardless of prompt."""
        fake = FakeSFXBackend()
        b1 = fake.generate("explosion", 1.0, False)
        b2 = fake.generate("footsteps", 2.0, True)
        assert b1 == b2

    def test_generate_accepts_float_duration(self) -> None:
        fake = FakeSFXBackend()
        result = fake.generate("click", 0.25, False)
        assert isinstance(result, bytes)
        assert fake.calls[0]["duration_seconds"] == 0.25


# ---------------------------------------------------------------------------
# FakeSFXBackend — generate_and_save
# ---------------------------------------------------------------------------


class TestFakeSFXBackendGenerateAndSave:
    def test_generate_and_save_creates_file(self, tmp_path: Path) -> None:
        fake = FakeSFXBackend()
        filepath = str(tmp_path / "sfx.mp3")
        result = fake.generate_and_save("door creak", filepath, 1.5, False)
        assert result is True
        assert Path(filepath).exists()

    def test_generate_and_save_writes_non_empty_bytes(self, tmp_path: Path) -> None:
        fake = FakeSFXBackend()
        filepath = str(tmp_path / "sfx.mp3")
        fake.generate_and_save("door creak", filepath, 1.5, False)
        assert Path(filepath).stat().st_size > 0

    def test_generate_and_save_records_call(self, tmp_path: Path) -> None:
        fake = FakeSFXBackend()
        filepath = str(tmp_path / "sfx.mp3")
        fake.generate_and_save("fire crackle", filepath, 4.0, True)
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["prompt"] == "fire crackle"
        assert call["filepath"] == filepath
        assert call["duration_seconds"] == 4.0
        assert call["loop"] is True

    def test_generate_and_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        fake = FakeSFXBackend()
        nested = tmp_path / "sfx" / "ambient" / "sound.mp3"
        fake.generate_and_save("wind howl", str(nested), 5.0, True)
        assert nested.exists()

    def test_generate_and_save_bytes_match_generate(self, tmp_path: Path) -> None:
        """File content should equal what generate() returns."""
        fake = FakeSFXBackend()
        filepath = str(tmp_path / "sfx.mp3")
        expected = fake.generate("test", 1.0, False)
        fake.calls.clear()
        fake.generate_and_save("test", filepath, 1.0, False)
        written = Path(filepath).read_bytes()
        assert written == expected


# ---------------------------------------------------------------------------
# FakeSFXBackend — async variants
# ---------------------------------------------------------------------------


class TestFakeSFXBackendAsync:
    def test_generate_async_returns_bytes(self) -> None:
        fake = FakeSFXBackend()
        result = asyncio.run(fake.generate_async("arrow whoosh", 0.5, False))
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_generate_async_same_bytes_as_sync(self) -> None:
        fake = FakeSFXBackend()
        sync_result = fake.generate("arrow whoosh", 0.5, False)
        fake.calls.clear()
        async_result = asyncio.run(fake.generate_async("arrow whoosh", 0.5, False))
        assert sync_result == async_result

    def test_generate_and_save_async_creates_file(self, tmp_path: Path) -> None:
        fake = FakeSFXBackend()
        filepath = str(tmp_path / "async_sfx.mp3")
        result = asyncio.run(
            fake.generate_and_save_async("magic spell", filepath, 2.0, False)
        )
        assert result is True
        assert Path(filepath).exists()
        assert Path(filepath).stat().st_size > 0

    def test_generate_and_save_async_creates_parent_dirs(self, tmp_path: Path) -> None:
        fake = FakeSFXBackend()
        nested = tmp_path / "async" / "sfx" / "effect.mp3"
        asyncio.run(
            fake.generate_and_save_async("rain drop", str(nested), 0.1, True)
        )
        assert nested.exists()


# ---------------------------------------------------------------------------
# FakeSFXBackend — custom placeholder
# ---------------------------------------------------------------------------


class TestFakeSFXBackendPlaceholder:
    def test_uses_placeholder_file_when_provided(self, tmp_path: Path) -> None:
        placeholder = tmp_path / "custom.mp3"
        placeholder.write_bytes(b"FAKESFXDATA")
        fake = FakeSFXBackend(placeholder=placeholder)
        result = fake.generate("anything", 1.0, False)
        assert result == b"FAKESFXDATA"

    def test_uses_asset_dir_mp3_when_provided(self, tmp_path: Path) -> None:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        (asset_dir / "effect.mp3").write_bytes(b"DIRSFXDATA")
        fake = FakeSFXBackend(asset_dir=asset_dir)
        result = fake.generate("anything", 1.0, False)
        assert result == b"DIRSFXDATA"

    def test_placeholder_takes_precedence_over_asset_dir(self, tmp_path: Path) -> None:
        asset_dir = tmp_path / "assets"
        asset_dir.mkdir()
        (asset_dir / "effect.mp3").write_bytes(b"DIRSFX")
        placeholder = tmp_path / "custom.mp3"
        placeholder.write_bytes(b"PLACEHOLDERSFX")
        fake = FakeSFXBackend(asset_dir=asset_dir, placeholder=placeholder)
        result = fake.generate("anything", 1.0, False)
        assert result == b"PLACEHOLDERSFX"

    def test_falls_back_to_min_mp3_when_no_assets(self) -> None:
        from canon.backends.testing import _MIN_MP3_BYTES

        fake = FakeSFXBackend()
        result = fake.generate("anything", 1.0, False)
        assert result == _MIN_MP3_BYTES
        # Verify it starts with ID3 magic
        assert result[:3] == b"ID3"


# ---------------------------------------------------------------------------
# Registry — sfx
# ---------------------------------------------------------------------------


class TestRegistrySFX:
    def setup_method(self) -> None:
        BackendRegistry.reset()

    def teardown_method(self) -> None:
        BackendRegistry.reset()

    def test_register_and_retrieve(self) -> None:
        BackendRegistry.register_sfx("fake", lambda: FakeSFXBackend())
        backend = BackendRegistry.sfx("fake")
        assert isinstance(backend, FakeSFXBackend)

    def test_instance_is_cached(self) -> None:
        BackendRegistry.register_sfx("fake", lambda: FakeSFXBackend())
        first = BackendRegistry.sfx("fake")
        second = BackendRegistry.sfx("fake")
        assert first is second

    def test_unknown_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError, match="elevenlabs"):
            BackendRegistry.sfx("elevenlabs")

    def test_key_error_message_is_descriptive(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            BackendRegistry.sfx("mysfx")
        assert "mysfx" in str(exc_info.value)

    def test_re_register_clears_cached_instance(self) -> None:
        BackendRegistry.register_sfx("fake", lambda: FakeSFXBackend())
        first = BackendRegistry.sfx("fake")
        BackendRegistry.register_sfx("fake", lambda: FakeSFXBackend())
        second = BackendRegistry.sfx("fake")
        assert first is not second

    def test_reset_clears_sfx_state(self) -> None:
        BackendRegistry.register_sfx("fake", lambda: FakeSFXBackend())
        _ = BackendRegistry.sfx("fake")
        BackendRegistry.reset()
        with pytest.raises(KeyError):
            BackendRegistry.sfx("fake")

    def test_factory_called_once(self) -> None:
        call_count = [0]

        def factory() -> FakeSFXBackend:
            call_count[0] += 1
            return FakeSFXBackend()

        BackendRegistry.register_sfx("counted", factory)
        BackendRegistry.sfx("counted")
        BackendRegistry.sfx("counted")
        assert call_count[0] == 1

    def test_sfx_music_image_backends_all_coexist(self) -> None:
        from canon.backends import FakeImageBackend, FakeMusicBackend

        BackendRegistry.register_sfx("sfx", lambda: FakeSFXBackend())
        BackendRegistry.register_music("mus", lambda: FakeMusicBackend())
        BackendRegistry.register_image("img", lambda: FakeImageBackend())
        assert isinstance(BackendRegistry.sfx("sfx"), FakeSFXBackend)
        assert isinstance(BackendRegistry.music("mus"), FakeMusicBackend)
        assert isinstance(BackendRegistry.image("img"), FakeImageBackend)
