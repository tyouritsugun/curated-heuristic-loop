"""Configuration tests for CPU-only mode behavior."""
import os
import shutil
from pathlib import Path
import pytest


def _with_env(env: dict):
    class _Ctx:
        def __enter__(self):
            self._prev = {k: os.environ.get(k) for k in env}
            for k, v in env.items():
                if v is None and k in os.environ:
                    os.environ.pop(k, None)
                elif v is not None:
                    os.environ[k] = v
            return self

        def __exit__(self, exc_type, exc, tb):
            for k, prev in self._prev.items():
                if prev is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = prev

    return _Ctx()


def test_config_initializes_in_cpu_mode(tmp_path: Path):
    from src.common.config.config import Config
    data_dir = tmp_path / "data"
    with _with_env({
        "CHL_EXPERIENCE_ROOT": str(data_dir),
        "CHL_BACKEND": "cpu",
    }):
        cfg = Config()
        # backend is cpu
        assert cfg.backend == "cpu"
        # Helper methods work
        assert cfg.is_cpu_only() is True
        assert cfg.is_semantic_enabled() is False
        # search_mode property provides backward compat
        assert cfg.search_mode == "cpu"
        # Experience root created
        assert Path(cfg.experience_root).exists()
        # FAISS index dir is not auto-created in CPU mode
        assert not Path(cfg.faiss_index_path).exists()


def test_config_invalid_backend_raises(tmp_path: Path):
    from src.common.config.config import Config
    with _with_env({
        "CHL_EXPERIENCE_ROOT": str(tmp_path),
        "CHL_BACKEND": "bogus_mode",
    }):
        with pytest.raises(ValueError):
            Config()
