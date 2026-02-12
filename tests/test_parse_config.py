# This file is part of ELNBuildSync
# Copyright (C) 2026 Stephen Gallagher <sgallagh@redhat.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

# SPDX-License-Identifier: 	GPL-3.0-or-later

import json
import logging
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests.exceptions

import elnbuildsync.config as config_mod
from elnbuildsync.config import (
    ConfigError,
    UnknownRefError,
    _parse_build,
    _parse_configuration_block,
    _parse_control,
    _parse_defaults,
    _parse_open_id_connect,
    get_config_ref,
    get_order,
    get_rawhide_tag,
    is_debug,
    is_eligible,
    is_paused,
    load_config,
    loglevel,
    retries,
    skip_tag,
    split_module,
    split_scmurl,
)


# Minimal valid OIDC config for tests
MINIMAL_OIDC = {
    "auth_url": "https://id.example.com/auth",
    "client_id": "client",
    "client_secret": "secret",
    "token_endpoint": "https://id.example.com/token",
    "admin_groups": ["admins"],
}


class TestParseOpenIdConnect:
    def test_disabled_returns_none(self):
        assert _parse_open_id_connect(False) is None

    def test_valid_minimal_returns_parsed_dict(self):
        result = _parse_open_id_connect(MINIMAL_OIDC)
        assert result is not None
        assert result["auth_url"] == "https://id.example.com/auth"
        assert result["client_id"] == "client"
        assert result["client_secret"] == "secret"
        assert result["token_endpoint"] == "https://id.example.com/token"
        assert result["admin_groups"] == ["admins"]
        assert result["userinfo_endpoint"] == ""
        assert "openid" in result["scopes"]
        assert "profile" in result["scopes"]

    def test_missing_required_field_raises(self):
        for key in MINIMAL_OIDC:
            bad = {k: v for k, v in MINIMAL_OIDC.items() if k != key}
            with pytest.raises(ConfigError, match=f"open_id_connect.{key} missing"):
                _parse_open_id_connect(bad)

    def test_optional_userinfo_and_scopes_reflected(self):
        config = {
            **MINIMAL_OIDC,
            "userinfo_endpoint": "https://id.example.com/userinfo",
            "scopes": ["openid", "custom"],
        }
        result = _parse_open_id_connect(config)
        assert result["userinfo_endpoint"] == "https://id.example.com/userinfo"
        assert result["scopes"] == ["openid", "custom"]


class TestParseBuild:
    def test_minimal_target_only(self):
        result = _parse_build({"target": "eln"})
        assert result["target"] == "eln"
        assert result["scratch"] is False
        assert result["fail_fast"] is False

    def test_scratch_and_fail_fast_true(self):
        result = _parse_build(
            {
                "target": "eln",
                "scratch": True,
                "fail_fast": True,
            }
        )
        assert result["target"] == "eln"
        assert result["scratch"] is True
        assert result["fail_fast"] is True

    def test_missing_target_raises(self):
        with pytest.raises(ConfigError, match="build.target missing"):
            _parse_build({})


# Minimal valid control config
MINIMAL_CONTROL = {
    "pause": False,
    "strict": True,
    "db": {
        "driver": "postgresql+asyncpg",
        "host": "localhost",
        "port": 5432,
        "name": "testdb",
        "user": "testuser",
    },
}


class TestParseControl:
    def test_minimal_required(self):
        result = _parse_control(MINIMAL_CONTROL)
        assert result["pause"] is False
        assert result["strict"] is True
        assert result["db"] == MINIMAL_CONTROL["db"]
        assert result["update_batch_size"] == 0
        assert result["autopackagelist"] is None
        assert result["skip_tag"] == {"rpms": set(), "modules": set()}
        assert result["exclude"] == {"rpms": set(), "modules": set()}
        assert result["ordering"] == {"rpms": {}, "modules": {}}

    def test_update_batch_size(self):
        result = _parse_control({**MINIMAL_CONTROL, "update_batch_size": 750})
        assert result["update_batch_size"] == 750

    def test_skip_tag_exclude_ordering_autopackagelist(self):
        result = _parse_control(
            {
                **MINIMAL_CONTROL,
                "skip_tag": {"rpms": ["^kernel$"], "modules": set()},
                "exclude": {"rpms": ["^foo$"], "modules": set()},
                "ordering": {"rpms": {"^ocaml$": 0}, "modules": {}},
                "autopackagelist": {"view": "eln"},
            }
        )
        assert result["skip_tag"]["rpms"] == {"^kernel$"}
        assert result["exclude"]["rpms"] == {"^foo$"}
        assert result["ordering"]["rpms"] == {"^ocaml$": 0}
        assert result["autopackagelist"] == {"view": "eln"}

    def test_missing_pause_raises(self):
        with pytest.raises(ConfigError, match="control.pause missing"):
            _parse_control({k: v for k, v in MINIMAL_CONTROL.items() if k != "pause"})

    def test_missing_strict_raises(self):
        with pytest.raises(ConfigError, match="control.strict missing"):
            _parse_control({k: v for k, v in MINIMAL_CONTROL.items() if k != "strict"})

    def test_missing_db_raises(self):
        with pytest.raises(ConfigError, match="Missing database configuration"):
            _parse_control({k: v for k, v in MINIMAL_CONTROL.items() if k != "db"})

    def test_invalid_update_batch_size_raises(self):
        with pytest.raises(
            ConfigError, match="control.update_batch_size must be an integer"
        ):
            _parse_control({**MINIMAL_CONTROL, "update_batch_size": "not-an-int"})


# Minimal valid defaults config
MINIMAL_DEFAULTS = {
    "cache": {"source": "%(component)s", "destination": "%(component)s"},
    "rpms": {
        "source": "%(component)s.git#rawhide",
        "destination": "%(component)s.git#rawhide",
    },
    "modules": {
        "source": "%(component)s.git#%(stream)s",
        "destination": "%(component)s.git#%(stream)s",
    },
}


class TestParseDefaults:
    def test_valid_defaults(self):
        result = _parse_defaults(MINIMAL_DEFAULTS)
        assert result["cache"]["source"] == "%(component)s"
        assert result["cache"]["destination"] == "%(component)s"
        assert result["rpms"]["source"] == "%(component)s.git#rawhide"
        assert result["modules"]["destination"] == "%(component)s.git#%(stream)s"

    def test_missing_section_raises(self):
        for key in MINIMAL_DEFAULTS:
            bad = {k: v for k, v in MINIMAL_DEFAULTS.items() if k != key}
            with pytest.raises(ConfigError, match=f"defaults.{key} missing"):
                _parse_defaults(bad)


def _minimal_cnf(open_id_connect=None):
    """Build minimal configuration block for _parse_configuration_block tests."""
    if open_id_connect is None:
        open_id_connect = MINIMAL_OIDC
    return {
        "koji_profile": "koji",
        "trigger_tag": "f40",
        "open_id_connect": open_id_connect,
        "build": {"target": "eln", "scratch": False, "fail_fast": False},
        "control": MINIMAL_CONTROL,
        "defaults": MINIMAL_DEFAULTS,
    }


class TestParseConfigurationBlock:
    def test_full_valid_cnf_returns_n(self):
        cnf = _minimal_cnf()
        n = _parse_configuration_block(cnf)
        assert n["koji_profile"] == "koji"
        assert n["trigger_tag"] == "f40"
        assert n["open_id_connect"] is not None
        assert n["open_id_connect"]["auth_url"] == MINIMAL_OIDC["auth_url"]
        assert n["build"]["target"] == "eln"
        assert n["control"]["pause"] is False
        assert n["defaults"]["cache"]["source"] == "%(component)s"

    def test_oidc_disabled(self):
        cnf = _minimal_cnf(open_id_connect=False)
        n = _parse_configuration_block(cnf)
        assert n["open_id_connect"] is None

    def test_missing_koji_profile_raises(self):
        cnf = _minimal_cnf()
        del cnf["koji_profile"]
        with pytest.raises(ConfigError, match="koji_profile missing"):
            _parse_configuration_block(cnf)

    def test_missing_trigger_tag_raises(self):
        cnf = _minimal_cnf()
        del cnf["trigger_tag"]
        with pytest.raises(ConfigError, match="trigger_tag missing"):
            _parse_configuration_block(cnf)

    def test_missing_open_id_connect_raises(self):
        cnf = _minimal_cnf()
        del cnf["open_id_connect"]
        with pytest.raises(ConfigError, match="open_id_connect missing"):
            _parse_configuration_block(cnf)

    def test_missing_build_raises(self):
        cnf = _minimal_cnf()
        del cnf["build"]
        with pytest.raises(ConfigError, match="build missing"):
            _parse_configuration_block(cnf)

    def test_missing_control_raises(self):
        cnf = _minimal_cnf()
        del cnf["control"]
        with pytest.raises(ConfigError, match="control missing"):
            _parse_configuration_block(cnf)

    def test_missing_defaults_raises(self):
        cnf = _minimal_cnf()
        del cnf["defaults"]
        with pytest.raises(ConfigError, match="defaults missing"):
            _parse_configuration_block(cnf)


# Minimal YAML for load_config integration test (no components, no autopackagelist)
MINIMAL_LOAD_CONFIG_YAML = """
configuration:
  koji_profile: koji
  trigger_tag: f40
  open_id_connect: false
  build:
    target: eln
    scratch: false
    fail_fast: false
  control:
    pause: false
    strict: true
    db:
      driver: postgresql+asyncpg
      host: localhost
      port: 5432
      name: testdb
      user: testuser
  defaults:
    cache:
      source: "%(component)s"
      destination: "%(component)s"
    rpms:
      source: "%(component)s.git#rawhide"
      destination: "%(component)s.git#rawhide"
    modules:
      source: "%(component)s.git#%(stream)s"
      destination: "%(component)s.git#%(stream)s"
"""


async def _fake_defer_to_thread(fn, *args, **kwargs):
    """Run fn synchronously and return result; used so load_config works under asyncio."""
    return fn(*args, **kwargs)


class TestLoadConfig:
    @pytest.mark.asyncio
    async def test_load_config_from_file_sets_main_and_comps(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(MINIMAL_LOAD_CONFIG_YAML)
            path = f.name
        try:
            with patch(
                "elnbuildsync.config.deferToThread", side_effect=_fake_defer_to_thread
            ):
                with patch(
                    "elnbuildsync.config.get_rawhide_tag", new_callable=AsyncMock
                ) as mock_rawhide:
                    with patch(
                        "elnbuildsync.config.get_distro_packages",
                        new_callable=AsyncMock,
                    ) as mock_distro:
                        await load_config(config_file=path, db_pw="testpw")
                        mock_rawhide.assert_not_called()
                        mock_distro.assert_not_called()
            assert config_mod.main is not None
            assert config_mod.main["koji_profile"] == "koji"
            assert config_mod.main["trigger_tag"] == "f40"
            assert config_mod.main["open_id_connect"] is None
            assert config_mod.main["build"]["target"] == "eln"
            assert config_mod.comps is not None
            assert config_mod.comps["rpms"] == {}
            assert config_mod.comps["modules"] == {}
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_load_config_trigger_tag_rawhide_resolved_via_bodhi(self):
        """When trigger_tag is 'rawhide', load_config calls get_rawhide_tag() which queries Bodhi; we mock the Bodhi HTTP call."""
        yaml_with_rawhide = MINIMAL_LOAD_CONFIG_YAML.replace(
            "trigger_tag: f40", "trigger_tag: rawhide"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_with_rawhide)
            path = f.name
        try:
            # Mock Bodhi response so get_rawhide_tag() gets rawhide -> f41 without real HTTP
            bodhi_response = MagicMock()
            bodhi_response.text = _bodhi_releases_json("f41")
            bodhi_response.raise_for_status = MagicMock()
            mock_get = AsyncMock(return_value=bodhi_response)
            mock_session = MagicMock()
            mock_session.get = mock_get
            mock_session.__enter__ = MagicMock(return_value=mock_session)
            mock_session.__exit__ = MagicMock(return_value=False)

            with patch(
                "elnbuildsync.config.deferToThread", side_effect=_fake_defer_to_thread
            ):
                with patch("elnbuildsync.config.Session", return_value=mock_session):
                    with patch(
                        "elnbuildsync.config.get_distro_packages",
                        new_callable=AsyncMock,
                    ):
                        await load_config(config_file=path, db_pw="testpw")
            assert config_mod.main["trigger_tag"] == "f41"
            mock_get.assert_called_once()
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_load_config_missing_file_raises(self):
        with pytest.raises(ConfigError, match="Could not parse"):
            await load_config(
                config_file="/nonexistent/path/distrobaker.yaml", db_pw=""
            )

    @pytest.mark.asyncio
    async def test_load_config_invalid_yaml_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("not: valid: yaml: [")
            path = f.name
        try:
            with pytest.raises(ConfigError, match="Could not parse"):
                await load_config(config_file=path, db_pw="")
        finally:
            os.unlink(path)


class TestSplitScmurl:
    def test_link_only_ref_none(self):
        result = split_scmurl("https://git.example.com/ns/comp")
        assert result["link"] == "https://git.example.com/ns/comp"
        assert result["ref"] is None
        assert result["ns"] == "ns"
        assert result["comp"] == "comp"

    def test_link_with_ref(self):
        result = split_scmurl("https://git.example.com/ns/comp#main")
        assert result["link"] == "https://git.example.com/ns/comp"
        assert result["ref"] == "main"
        assert result["ns"] == "ns"
        assert result["comp"] == "comp"

    def test_single_segment_link(self):
        result = split_scmurl("https://git.example.com/repo")
        assert result["link"] == "https://git.example.com/repo"
        assert result["ref"] is None
        assert result["comp"] == "repo"

    def test_link_ref_ns_comp(self):
        result = split_scmurl("https://src.fedoraproject.org/rpms/kernel#rawhide")
        assert result["link"] == "https://src.fedoraproject.org/rpms/kernel"
        assert result["ref"] == "rawhide"
        assert result["ns"] == "rpms"
        assert result["comp"] == "kernel"


class TestSplitModule:
    def test_name_stream(self):
        result = split_module("nodejs:18")
        assert result["name"] == "nodejs"
        assert result["stream"] == "18"

    def test_name_only_defaults_master(self):
        result = split_module("nodejs")
        assert result["name"] == "nodejs"
        assert result["stream"] == "master"

    def test_empty_stream_defaults_master(self):
        result = split_module("name:")
        assert result["name"] == "name"
        assert result["stream"] == "master"


class TestLoglevel:
    def test_get_current_level(self):
        level = loglevel()
        assert level in (
            logging.NOTSET,
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        )

    def test_set_and_get(self, caplog):
        logger = config_mod.logger
        original = logger.getEffectiveLevel()
        try:
            loglevel(logging.INFO)
            assert loglevel() == logging.INFO
        finally:
            loglevel(original)

    def test_invalid_level_does_not_crash(self):
        original = config_mod.logger.getEffectiveLevel()
        try:
            loglevel(99999)
            assert loglevel() >= 0
        finally:
            config_mod.logger.setLevel(original)


class TestIsDebug:
    def test_true_when_debug(self):
        original = config_mod.logger.getEffectiveLevel()
        try:
            config_mod.logger.setLevel(logging.DEBUG)
            assert is_debug() is True
        finally:
            config_mod.logger.setLevel(original)

    def test_false_when_info(self):
        original = config_mod.logger.getEffectiveLevel()
        try:
            config_mod.logger.setLevel(logging.INFO)
            assert is_debug() is False
        finally:
            config_mod.logger.setLevel(original)


class TestRetries:
    def test_get_and_set(self, monkeypatch):
        original = config_mod.retry
        try:
            monkeypatch.setattr(config_mod, "retry", 5)
            assert retries() == 5
            retries(3)
            assert retries() == 3
        finally:
            config_mod.retry = original


class TestGetConfigRef:
    @pytest.mark.asyncio
    async def test_returns_ref_when_output(self):
        with patch(
            "elnbuildsync.config.twisted.internet.utils.getProcessOutput",
            new_callable=AsyncMock,
            return_value=b"abc123\trefs/heads/main",
        ):
            ref = await get_config_ref("https://git.example.com/repo#main")
            assert ref == b"abc123"

    @pytest.mark.asyncio
    async def test_unknown_ref_raises(self):
        with patch(
            "elnbuildsync.config.twisted.internet.utils.getProcessOutput",
            new_callable=AsyncMock,
            return_value=b"",
        ):
            with pytest.raises(UnknownRefError, match="not found"):
                await get_config_ref("https://git.example.com/repo#nonexistent")


class TestIsEligible:
    def test_strict_and_comp_not_in_comps_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "strict": True,
                    "exclude": {"rpms": set(), "modules": set()},
                },
            },
        )
        monkeypatch.setattr(config_mod, "comps", {"rpms": {"ipa": {}}, "modules": {}})
        assert is_eligible("rpms", "kernel") is False

    def test_strict_and_comp_in_comps_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "strict": True,
                    "exclude": {"rpms": set(), "modules": set()},
                },
            },
        )
        monkeypatch.setattr(config_mod, "comps", {"rpms": {"ipa": {}}, "modules": {}})
        assert is_eligible("rpms", "ipa") is True

    def test_exclude_pattern_matches_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "strict": False,
                    "exclude": {"rpms": {"^kernel$"}, "modules": set()},
                },
            },
        )
        monkeypatch.setattr(config_mod, "comps", {"rpms": {}, "modules": {}})
        assert is_eligible("rpms", "kernel") is False

    def test_not_excluded_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "strict": False,
                    "exclude": {"rpms": set(), "modules": set()},
                },
            },
        )
        monkeypatch.setattr(config_mod, "comps", {"rpms": {}, "modules": {}})
        assert is_eligible("rpms", "ipa") is True


class TestSkipTag:
    def test_pattern_matches_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "skip_tag": {"rpms": {"^kernel$"}, "modules": set()},
                },
            },
        )
        assert skip_tag("rpms", "kernel") is True

    def test_no_match_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "skip_tag": {"rpms": set(), "modules": set()},
                },
            },
        )
        assert skip_tag("rpms", "ipa") is False


class TestGetOrder:
    def test_pattern_matches_returns_order(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "ordering": {
                        "rpms": {"^ocaml$": 0},
                        "modules": {},
                    },
                },
            },
        )
        assert get_order("rpms", "ocaml") == 0

    def test_no_pattern_returns_1000(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {
                "control": {
                    "ordering": {"rpms": {}, "modules": {}},
                },
            },
        )
        assert get_order("rpms", "ipa") == 1000


class TestIsPaused:
    def test_paused_true(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {"control": {"pause": True}},
        )
        assert is_paused() is True

    def test_paused_false(self, monkeypatch):
        monkeypatch.setattr(
            config_mod,
            "main",
            {"control": {"pause": False}},
        )
        assert is_paused() is False


class TestConfigError:
    def test_config_error_is_exception(self):
        with pytest.raises(ConfigError):
            raise ConfigError("test")

    def test_unknown_ref_error_subclass(self):
        assert issubclass(UnknownRefError, ConfigError)
        with pytest.raises(UnknownRefError):
            raise UnknownRefError("ref not found")


# --- get_rawhide_tag() tests (Bodhi mocked) ---


def _get_rawhide_tag_impl():
    """Use unwrapped function for tests that expect ConfigError so backoff doesn't retry."""
    f = get_rawhide_tag
    while hasattr(f, "__wrapped__"):
        f = f.__wrapped__
    return f


def _bodhi_releases_json(stable_tag="f41"):
    """Minimal Bodhi releases response with rawhide."""
    return json.dumps(
        {
            "releases": [
                {"branch": "rawhide", "stable_tag": stable_tag},
                {"branch": "f40", "stable_tag": "f40"},
            ]
        }
    )


class TestGetRawhideTag:
    """Tests for get_rawhide_tag() with Bodhi HTTP call mocked."""

    @pytest.mark.asyncio
    async def test_returns_stable_tag_when_rawhide_in_releases(self):
        mock_response = MagicMock()
        mock_response.text = _bodhi_releases_json("f41")
        mock_response.raise_for_status = MagicMock()

        mock_get = AsyncMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("elnbuildsync.config.Session", return_value=mock_session):
            tag = await get_rawhide_tag()
        assert tag == "f41"
        mock_get.assert_called_once()
        mock_response.raise_for_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_when_no_rawhide_in_releases(self):
        no_rawhide = json.dumps(
            {
                "releases": [
                    {"branch": "f40", "stable_tag": "f40"},
                ]
            }
        )
        mock_response = MagicMock()
        mock_response.text = no_rawhide
        mock_response.raise_for_status = MagicMock()

        mock_get = AsyncMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        get_tag = _get_rawhide_tag_impl()
        with patch("elnbuildsync.config.Session", return_value=mock_session):
            with pytest.raises(ConfigError, match="no valid Fedora rawhide release"):
                await get_tag()

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        mock_response = MagicMock()
        mock_response.text = "not json at all"
        mock_response.raise_for_status = MagicMock()

        mock_get = AsyncMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        get_tag = _get_rawhide_tag_impl()
        with patch("elnbuildsync.config.Session", return_value=mock_session):
            with pytest.raises(ConfigError, match="Could not parse JSON from Bodhi"):
                await get_tag()

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError("404")
        )

        mock_get = AsyncMock(return_value=mock_response)
        mock_session = MagicMock()
        mock_session.get = mock_get
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        get_tag = _get_rawhide_tag_impl()
        with patch("elnbuildsync.config.Session", return_value=mock_session):
            with pytest.raises(ConfigError, match="HTTP Error"):
                await get_tag()
