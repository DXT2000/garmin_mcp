"""Integration-test guards shared by every tool module."""
import pytest

from garmin_mcp import exercise_catalog


@pytest.fixture(autouse=True)
def no_exercise_catalog_download(monkeypatch, tmp_path):
    """Keep the strength exercise catalog offline and out of the user's cache.

    Tests that need a catalog mock load_catalog or resolve_strength_exercises
    themselves.
    """
    def blocked_download(config):
        raise AssertionError("integration tests must not download the exercise catalog")

    monkeypatch.setenv("GARMIN_EXERCISE_CACHE_DIR", str(tmp_path / "exercise_catalog"))
    monkeypatch.setattr(exercise_catalog, "_download", blocked_download)
    exercise_catalog.clear_memory_cache()
    yield
    exercise_catalog.clear_memory_cache()
