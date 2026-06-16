# This file is part of ELNBuildSync
# Copyright (C) 2023-2026 Stephen Gallagher <sgallagh@redhat.com>

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


import logging
import os

import sqlalchemy
import yaml
from twisted.internet.threads import deferToThread

from ..email import Email


logger = logging.getLogger(__name__)

DEFAULT_CONTENT_RESOLVER = "https://tiny.distro.builders"


def _parse_open_id_connect(oidc_raw, ConfigError):
    """Parse OpenID Connect configuration. Returns None if disabled, else a dict.
    Raises ConfigError on invalid or missing required fields.
    """
    if oidc_raw is False:
        logger.info(
            "OpenID Connect explicitly disabled - /trigger endpoint unprotected"
        )
        return None
    oidc = oidc_raw
    default_scopes = [
        "openid",
        "profile",
        "https://id.fedoraproject.org/scope/groups",
    ]
    required_fields = [
        "auth_url",
        "client_id",
        "client_secret",
        "token_endpoint",
        "admin_groups",
    ]
    for field in required_fields:
        if field not in oidc:
            raise ConfigError(f"open_id_connect.{field} missing.")
    result = {
        "auth_url": str(oidc["auth_url"]),
        "client_id": str(oidc["client_id"]),
        "client_secret": str(oidc["client_secret"]),
        "token_endpoint": str(oidc["token_endpoint"]),
        "userinfo_endpoint": str(oidc.get("userinfo_endpoint", "")),
        "scopes": list(oidc.get("scopes", default_scopes)),
        "admin_groups": list(oidc["admin_groups"]),
    }
    logger.info(
        "OpenID Connect authentication enabled; admin groups: %s",
        result["admin_groups"],
    )
    return result


def _parse_koji(cnf_koji, ConfigError):
    """Parse koji configuration. Returns dict with profile, build_target, stable_tag,
    scratch_build, fail_fast, and optionally username.
    """
    if "profile" not in cnf_koji:
        raise ConfigError("koji.profile missing.")
    result = {"profile": str(cnf_koji["profile"])}
    if "build_target" not in cnf_koji:
        raise ConfigError("koji.build_target missing.")
    result["build_target"] = str(cnf_koji["build_target"])
    if "stable_tag" not in cnf_koji:
        raise ConfigError("koji.stable_tag missing.")
    result["stable_tag"] = str(cnf_koji["stable_tag"])
    if "username" in cnf_koji:
        result["username"] = str(cnf_koji["username"])
    if "scratch_build" in cnf_koji:
        result["scratch_build"] = bool(cnf_koji["scratch_build"])
    else:
        logger.warning(
            "Configuration warning: koji.scratch_build not defined, assuming false."
        )
        result["scratch_build"] = False
    if "fail_fast" in cnf_koji:
        result["fail_fast"] = bool(cnf_koji["fail_fast"])
    else:
        logger.warning(
            "Configuration warning: koji.fail_fast not defined, assuming false."
        )
        result["fail_fast"] = False
    return result


def _parse_bodhi(cnf_bodhi, ConfigError):
    """Parse bodhi configuration. Returns dict with batch_size."""
    result = {"batch_size": 0}
    if "batch_size" in cnf_bodhi:
        try:
            result["batch_size"] = int(cnf_bodhi["batch_size"])
        except ValueError:
            raise ConfigError("bodhi.batch_size must be an integer")
    return result


def _parse_email(cnf_email, ConfigError):
    """Parse email configuration. Returns None if disabled, else a dict with
    smtp_host, smtp_port, smtp_username, from, recipients.
    Raises ConfigError on invalid or missing required fields.
    """
    if cnf_email is False:
        logger.info("Email explicitly disabled")
        return None
    required = ("smtp_host", "smtp_port", "smtp_username", "from", "recipients")
    for key in required:
        if key not in cnf_email:
            raise ConfigError(f"email.{key} missing.")
    try:
        port = int(cnf_email["smtp_port"])
    except (TypeError, ValueError):
        raise ConfigError("email.smtp_port must be an integer")
    recipients = cnf_email["recipients"]
    if not isinstance(recipients, list) or len(recipients) == 0:
        raise ConfigError("email.recipients must be a non-empty list.")
    for r in recipients:
        if not isinstance(r, str) or not r:
            raise ConfigError("email.recipients must be a list of non-empty strings.")
    return {
        "smtp_host": str(cnf_email["smtp_host"]),
        "smtp_port": port,
        "smtp_username": str(cnf_email["smtp_username"]),
        "from": str(cnf_email["from"]),
        "recipients": [str(x) for x in recipients],
    }


def _parse_db(cnf_db, ConfigError):
    """Parse database configuration. Returns dict with host, port, name, driver, user.
    All keys are mandatory.
    """
    required = ("host", "port", "name", "driver", "user")
    for key in required:
        if key not in cnf_db:
            raise ConfigError(f"db.{key} missing.")
    try:
        result = {
            "host": str(cnf_db["host"]),
            "port": int(cnf_db["port"]),
            "name": str(cnf_db["name"]),
            "driver": str(cnf_db["driver"]),
            "user": str(cnf_db["user"]),
        }
    except ValueError:
        raise ConfigError("db.port must be an integer")
    return result


def _parse_static_configuration(cnf, ConfigError):
    """Parse the static configuration block.
    Returns dict with koji, bodhi, db, open_id_connect, email.
    """
    if "control" in cnf:
        logger.warning(
            "Static configuration contains control block; use dynamic config instead."
        )

    if "koji" not in cnf:
        raise ConfigError("koji missing.")
    n = {"koji": _parse_koji(cnf["koji"], ConfigError)}

    if "bodhi" not in cnf:
        raise ConfigError("bodhi missing.")
    n["bodhi"] = _parse_bodhi(cnf["bodhi"], ConfigError)

    if "db" not in cnf:
        raise ConfigError("db missing.")
    n["db"] = _parse_db(cnf["db"], ConfigError)

    if "open_id_connect" not in cnf:
        raise ConfigError(
            "open_id_connect missing. Set open_id_connect: false to disable authentication."
        )
    n["open_id_connect"] = _parse_open_id_connect(cnf["open_id_connect"], ConfigError)

    if "email" not in cnf:
        raise ConfigError("email missing. Set email: false to disable email.")
    n["email"] = _parse_email(cnf["email"], ConfigError)

    return n


async def load_static_config(
    static_config_file,
    db_pw=None,
    *,
    config_module,
    ConfigError,
):
    """Load static configuration from a YAML file.

    Sets config.main, config.db_url (first call only), and config.emailer.
    """
    if not static_config_file:
        raise ValueError("static_config_file must be specified")

    if not os.path.isfile(static_config_file):
        raise ConfigError(f"Could not parse {static_config_file}.")

    try:
        with open(static_config_file) as f:
            y = await deferToThread(yaml.safe_load, f)
        logger.debug("%s loaded, processing static configuration.", static_config_file)
    except Exception as e:
        logger.info(e)
        raise ConfigError(f"Could not parse {static_config_file}.")

    if "configuration" not in y:
        raise ConfigError("The required configuration block is missing.")

    n = _parse_static_configuration(y["configuration"], ConfigError)
    config_module.main = n

    if not config_module.db_url:
        try:
            db_config = n["db"]
            config_module.db_url = sqlalchemy.URL.create(
                drivername=db_config["driver"],
                host=db_config["host"],
                port=db_config["port"],
                database=db_config["name"],
                username=db_config["user"],
                password=db_pw,
            )
        except KeyError as e:
            logger.exception(e)
            raise ConfigError("Missing database configuration (db block)")

    if n["email"] is not None:
        config_module.emailer = Email(n["email"], config_module.smtp_password)
    else:
        config_module.emailer = None
