"""
tests/test_config.py
---------------------
Unit tests for core/config.py — Config.load(), default values, YAML
merging, and the _deep_merge helper.
"""

from __future__ import annotations

import os
import textwrap

import pytest


class TestDefaultConfig:
    def test_loads_without_file(self):
        """Config.load() with a non-existent path uses built-in defaults."""
        from core.config import Config
        cfg = Config.load(path="/nonexistent/config.yaml")
        assert cfg.blocking_enabled is True
        assert cfg.log_backup_count == 5
        assert cfg.log_max_bytes == 10 * 1024 * 1024

    def test_log_level_default(self):
        from core.config import Config
        cfg = Config.load(path="/nonexistent/config.yaml")
        assert cfg.log_level == "INFO"

    def test_ipc_timeout_default(self):
        from core.config import Config
        cfg = Config.load(path="/nonexistent/config.yaml")
        assert cfg.ipc_timeout == 5


class TestYamlMerge:
    def test_override_blocking_enabled(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            blocking_enabled: false
        """)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_text)

        from core.config import Config
        cfg = Config.load(path=str(cfg_file))
        assert cfg.blocking_enabled is False

    def test_override_log_level(self, tmp_path):
        yaml_text = textwrap.dedent("""\
            log:
              level: DEBUG
        """)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_text)

        from core.config import Config
        cfg = Config.load(path=str(cfg_file))
        assert cfg.log_level == "DEBUG"

    def test_override_allowlist_db(self, tmp_path):
        db_path = str(tmp_path / "custom.db")
        yaml_text = f"allowlist_db: {db_path}\n"
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_text)

        from core.config import Config
        cfg = Config.load(path=str(cfg_file))
        assert cfg.allowlist_db == db_path

    def test_partial_override_preserves_defaults(self, tmp_path):
        """Overriding only log.level should preserve log.max_bytes default."""
        yaml_text = textwrap.dedent("""\
            log:
              level: WARNING
        """)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_text)

        from core.config import Config
        cfg = Config.load(path=str(cfg_file))
        assert cfg.log_level == "WARNING"
        assert cfg.log_max_bytes == 10 * 1024 * 1024   # default preserved


class TestDeepMerge:
    def test_flat_merge(self):
        from core.config import _deep_merge
        result = _deep_merge({"a": 1, "b": 2}, {"b": 99, "c": 3})
        assert result == {"a": 1, "b": 99, "c": 3}

    def test_nested_merge(self):
        from core.config import _deep_merge
        base     = {"log": {"level": "INFO", "max_bytes": 1000}}
        override = {"log": {"level": "DEBUG"}}
        result   = _deep_merge(base, override)
        assert result["log"]["level"]     == "DEBUG"
        assert result["log"]["max_bytes"] == 1000

    def test_does_not_mutate_base(self):
        from core.config import _deep_merge
        base = {"x": {"y": 1}}
        _deep_merge(base, {"x": {"y": 2}})
        assert base["x"]["y"] == 1   # original unchanged


class TestBlockingEnabledSetter:
    def test_setter(self):
        from core.config import Config
        cfg = Config.load(path="/nonexistent/config.yaml")
        cfg.blocking_enabled = False
        assert cfg.blocking_enabled is False
        cfg.blocking_enabled = True
        assert cfg.blocking_enabled is True
