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


import backoff
import git
import json
import logging
import os
import re
import requests.exceptions
import sqlalchemy
from txrequests import Session
import tempfile
import twisted.internet.utils
import yaml

from twisted.internet.threads import deferToThread

from . import config

# Global logger
logger = logging.getLogger(__name__)

DEFAULT_CONTENT_RESOLVER = "https://tiny.distro.builders"
DEFAULT_DISTRO_VIEWS = ["eln"]

# A special Deferred for terminating the program
terminator = None

# The URL for connecting to the database
db_url = None

# Configuration options
config_timer = 15 * 60  # 15 minutes
cleanup_timer = 12 * 60 * 60  # 12 hours
status_timer = 10 * 60  # 10 minutes
task_check_timer = 5 * 60  # 5 minutes
tag_check_timer = 5 * 60  # 5 minutes
task_timeout = 24 * 60 * 60  # 24 hours
tag_timeout = 1 * 60 * 60  # 1 hour
message_batch_timer = 60  # 1 minute
koji_batch = 500
configuration = None
config_ref = None
distrogitsync = None
dry_run = False
do_untagging = False
retry = 3
scmurl = None
main = None
comps = None
# If we haven't gotten the repoInit message within 10 minutes, assume we missed it
waitrepo_init_timeout = 10 * 60

# The actual generation can take up to 20 minutes
waitrepo_timeout = 20 * 60

# Process state
cleanup_processor = None
status_processor = None
tmpdir = None


class ConfigError(Exception):
    pass


class UnknownRefError(ConfigError):
    pass


def loglevel(val=None):
    """Gets or, optionally, sets the logging level of the module.
    Standard numeric levels are accepted.

    :param val: The logging level to use, optional
    :returns: The current logging level
    """
    if val is not None:
        try:
            logger.setLevel(val)
        except ValueError:
            logger.warning(
                "Invalid log level passed to DistroBuildSync logger: %s", val
            )
        except Exception:
            logger.exception("Unable to set log level: %s", val)
    return logger.getEffectiveLevel()


def is_debug():
    """
    Determines if we are in debug logging mode.

    This is useful for enabling/disabling third-party logging such as the
    sqlalchemy logger.
    """
    return loglevel() <= logging.DEBUG


def retries(val=None):
    """Gets or, optionally, sets the number of retries for various
    operational failures.  Typically used for handling dist-git requests.

    :param val: The number of retries to attept, optional
    :returns: The current value of retries
    """
    global retry
    if val is not None:
        retry = val
    return retry


def split_scmurl(url):
    """Splits a `link#ref` style URLs into the link and ref parts.  While
    generic, many code paths in DistroBuildSync expect these to be branch names.
    `link` forms are also accepted, in which case the returned `ref` is None.

    It also attempts to extract the namespace and component, where applicable.
    These can only be detected if the link matches the standard dist-git
    pattern; in other cases the results may be bogus or None.

    :param url: A link#ref style URL, with #ref being optional
    :returns: A dictionary with `link`, `ref`, `ns` and `comp` keys
    """
    scm = url.split("#", 1)
    nscomp = scm[0].split("/")
    return {
        "link": scm[0],
        "ref": scm[1] if len(scm) >= 2 else None,
        "ns": nscomp[-2] if nscomp and len(nscomp) >= 2 else None,
        "comp": nscomp[-1] if nscomp else None,
    }


def split_module(comp):
    """Splits modules component name into name and stream pair.  Expects the
    name to be in the `name:stream` format.  Defaults to stream=master if the
    split fails.

    :param comp: The component name
    :returns: Dictionary with name and stream
    """
    ms = comp.split(":")
    return {
        "name": ms[0],
        "stream": ms[1] if len(ms) > 1 and ms[1] else "master",
    }


async def get_config_ref(url):
    """Gets the ref for the config SCMURL

    Returns the actual ref for a symbolic ref possibly used in the
    config SCMURL.  Used by the update function to check whether the
    config should be resync'd.

    :param url: Config SCMURL
    :returns: Remote ref or None on error
    """
    scm = split_scmurl(url)
    logger.info(f"Getting config ref for {scm['link']} {scm['ref']}")

    if scm["ref"]:
        output = await twisted.internet.utils.getProcessOutput(
            executable="/usr/bin/git",
            args=("ls-remote", "--branches", scm["link"], scm["ref"]),
            errortoo=True,
        )
    else:
        output = await twisted.internet.utils.getProcessOutput(
            executable="/usr/bin/git",
            args=("ls-remote", "--branches", scm["link"]),
            errortoo=True,
        )

    if not output:
        scmref = scm["ref"]
        scmlink = scm["link"]
        raise UnknownRefError(f"{scmref} not found in {scmlink}")

    return output.split(b"\t", 1)[0]


async def update_config():
    global main
    global comps
    global scmurl
    global config_ref

    if not scmurl:
        logger.info("Config URL not provided.")
        return

    logger.critical(f"Updating configuration")

    try:
        ref = await get_config_ref(scmurl)
    except UnknownRefError as e:
        logger.info(e)
        logger.critical(
            f"The configuration repository is unavailable, skipping update.  Checking again in {config_timer} seconds."
        )
        return

    try:
        await load_config(config_git_url=scmurl)
        config_ref = ref
    except ConfigError as e:
        logger.info(e)
        logger.critical(
            f"The configuration is invalid, skipping update.  Checking again in {config_timer} seconds."
        )
        return


async def get_distro_packages(
    distro_url,
    distro_view=DEFAULT_DISTRO_VIEWS,
    arches=None,
    which_source=None,
):
    """
    Fetches the list of desired sources from Content Resolver
    for each of the given 'arches'.
    """
    if not arches:
        arches = ["aarch64", "ppc64le", "s390x", "x86_64"]
    if not which_source:
        which_source = ["source", "buildroot-source"]

    merged_packages = dict()

    for view in reversed(distro_view):
        for this_source in reversed(which_source):
            url = (
                "{distro_url}/view-{this_source}-package-name-list--view-{view}.txt"
            ).format(
                distro_url=distro_url,
                this_source=this_source,
                view=view,
            )

            logger.debug("downloading {url}".format(url=url))

            with Session() as session:
                r = await session.get(url, allow_redirects=True)
                for line in r.text.splitlines():
                    merged_packages[line] = {
                        "view": view,
                        "content_type": this_source,
                    }

    # There may be an empty line in the file, ignore it.
    if "" in merged_packages:
        del merged_packages[""]

    logger.debug("Found a total of {} packages".format(len(merged_packages)))

    return {"rpms": merged_packages}


@backoff.on_exception(backoff.expo, Exception, max_time=60)
async def get_rawhide_tag():
    """
    Queries Bodhi for the current tag associated with Rawhide
    """

    # Retrieve the list of "pending" (aka development) releases
    url = "https://bodhi.fedoraproject.org/releases?state=pending"
    with Session() as session:
        try:
            r = await session.get(url, allow_redirects=True)
            r.raise_for_status()
            releases = json.loads(r.text)
            logger.debug(releases)
        except json.decoder.JSONDecodeError as e:
            raise ConfigError("Could not parse JSON from Bodhi releases") from e

        except requests.exceptions.HTTPError as e:
            raise ConfigError("HTTP Error") from e

    # Get the stable tag corresponding to the rawhide branch
    stable_tag = None
    for release in releases["releases"]:
        # Get the stable tag associated with this release
        if release["branch"] == "rawhide":
            stable_tag = release["stable_tag"]
            break

    # Shouldn't ever happen, but...
    if not stable_tag:
        raise ConfigError("Unexpectedly received no valid Fedora rawhide release")

    return stable_tag


def _parse_open_id_connect(oidc_raw):
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


def _parse_build(cnf_build):
    """Parse build configuration. Returns dict with target, scratch, fail_fast."""
    if "target" not in cnf_build:
        raise ConfigError("build.target missing.")
    result = {"target": str(cnf_build["target"])}
    if "scratch" in cnf_build:
        result["scratch"] = bool(cnf_build["scratch"])
    else:
        logger.warning(
            "Configuration warning: build.scratch not defined, assuming false."
        )
        result["scratch"] = False
    if "fail_fast" in cnf_build:
        result["fail_fast"] = bool(cnf_build["fail_fast"])
    else:
        logger.warning(
            "Configuration warning: build.fail_fast not defined, assuming false."
        )
        result["fail_fast"] = False
    return result


def _parse_control(cnf_control):
    """Parse control configuration. Returns dict with pause, strict, db, etc."""
    result = dict()
    for k in ("pause", "strict"):
        if k in cnf_control:
            result[k] = bool(cnf_control[k])
        else:
            raise ConfigError(f"control.{k} missing.")

    result["update_batch_size"] = 0
    if "update_batch_size" in cnf_control:
        try:
            result["update_batch_size"] = int(cnf_control["update_batch_size"])
        except ValueError:
            raise ConfigError("control.update_batch_size must be an integer")

    result["autopackagelist"] = None
    if "autopackagelist" in cnf_control:
        result["autopackagelist"] = cnf_control["autopackagelist"]

    result["skip_tag"] = {"rpms": set(), "modules": set()}
    if "skip_tag" in cnf_control:
        for cns in ("rpms", "modules"):
            if cns in cnf_control["skip_tag"]:
                result["skip_tag"][cns].update(cnf_control["skip_tag"][cns])

    result["exclude"] = {"rpms": set(), "modules": set()}
    if "exclude" in cnf_control:
        for cns in ("rpms", "modules"):
            if cns in cnf_control["exclude"]:
                result["exclude"][cns].update(cnf_control["exclude"][cns])

    try:
        result["db"] = cnf_control["db"]
    except KeyError as e:
        logger.exception(e)
        raise ConfigError("Missing database configuration")

    for cns in ("rpms", "modules"):
        if result["exclude"][cns]:
            logger.info(
                "Excluding %d component(s) from the %s namespace.",
                len(result["exclude"][cns]),
                cns,
            )
        else:
            logger.info(
                "Not excluding any components from the %s namespace.",
                cns,
            )
    result["ordering"] = {"rpms": dict(), "modules": dict()}
    if "ordering" in cnf_control:
        for cns in ("rpms", "modules"):
            if cns in cnf_control["ordering"]:
                result["ordering"][cns].update(cnf_control["ordering"][cns])
    return result


def _parse_defaults(cnf_defaults):
    """Parse defaults configuration. Returns dict with cache, rpms, modules."""
    result = dict()
    for dk in ("cache", "rpms", "modules"):
        if dk in cnf_defaults:
            result[dk] = dict()
            for dkk in ("source", "destination"):
                if dkk in cnf_defaults[dk]:
                    result[dk][dkk] = str(cnf_defaults[dk][dkk])
                else:
                    logger.error(
                        "Configuration error: defaults.%s.%s missing.",
                        dk,
                        dkk,
                    )
        else:
            raise ConfigError(f"defaults.{dk} missing.")
    return result


def _parse_configuration_block(cnf):
    """Parse the full configuration block (no rawhide resolution, no components).
    Returns dict n with koji_profile, trigger_tag, open_id_connect, build, control, defaults.
    """
    if "koji_profile" not in cnf:
        raise ConfigError("koji_profile missing.")
    n = {"koji_profile": str(cnf["koji_profile"])}

    if "trigger_tag" not in cnf:
        raise ConfigError("trigger_tag missing.")
    n["trigger_tag"] = str(cnf["trigger_tag"])

    if "open_id_connect" not in cnf:
        raise ConfigError(
            "open_id_connect missing. Set open_id_connect: false to disable authentication."
        )
    n["open_id_connect"] = _parse_open_id_connect(cnf["open_id_connect"])

    if "build" not in cnf:
        raise ConfigError("build missing.")
    n["build"] = _parse_build(cnf["build"])

    if "control" not in cnf:
        raise ConfigError("control missing.")
    n["control"] = _parse_control(cnf["control"])

    if "defaults" not in cnf:
        raise ConfigError("defaults missing.")
    n["defaults"] = _parse_defaults(cnf["defaults"])

    return n


# FIXME: This needs even more error checking, e.g.
#         - check if blocks are actual dictionaries
#         - check if certain values are what we expect
async def load_config(db_pw=None, config_git_url=None, config_file=None):
    """Loads or updates the global configuration from the provided URL in
    the `link#branch` format.  If no branch is provided, assumes `master`.

    The operation is atomic and the function can be safely called to update
    the configuration without the danger of clobbering the current one.

    :returns: The configuration dictionary, or None on error
    """
    global main
    global comps
    global scmurl
    global db_url

    if not (config_git_url or config_file):
        raise ValueError("One of 'config_git_url' or 'config_file' must be specified")

    y = None

    with tempfile.TemporaryDirectory(prefix="distrobaker-") as cdir:
        if config_git_url:
            scmurl = config_git_url

            logger.info(f"Fetching configuration from {scmurl} to {cdir}")
            scm = split_scmurl(scmurl)
            if scm["ref"] is None:
                scm["ref"] = "main"
            for attempt in range(retry):
                try:
                    repo = await deferToThread(git.Repo.clone_from, scm["link"], cdir)
                    await deferToThread(repo.git.checkout, scm["ref"])
                except Exception:
                    logger.warning(
                        "Failed to fetch configuration, retrying (#%d).",
                        attempt + 1,
                        exc_info=True,
                    )
                    continue
                else:
                    logger.info("Configuration fetched successfully.")
                    break
            else:
                raise ConfigError("Failed to fetch configuration, giving up.")

            if os.path.isfile(os.path.join(cdir, "distrobaker.yaml")):
                config_file = os.path.join(cdir, "distrobaker.yaml")
            else:
                raise ConfigError(
                    "Configuration repository does not contain distrobaker.yaml."
                )

        try:
            with open(config_file) as f:
                y = await deferToThread(yaml.safe_load, f)
            logger.debug(f"{config_file} loaded, processing.")

        except Exception as e:
            logger.info(e)
            raise ConfigError(f"Could not parse {config_file}.")

    if "configuration" not in y:
        raise ConfigError("The required configuration block is missing.")
    cnf = y["configuration"]
    n = _parse_configuration_block(cnf)

    if n["trigger_tag"] == "rawhide":
        n["trigger_tag"] = await get_rawhide_tag()
        logger.info(f"Detected rawhide tag {n['trigger_tag']}")

    components = 0
    nc = {"rpms": dict(), "modules": dict()}
    if "components" in y or "autopackagelist" in n["control"]:
        cnf = {}

        if n["control"].get("autopackagelist"):
            resolver = DEFAULT_CONTENT_RESOLVER
            views = list()

            if "content_resolver" in n["control"]["autopackagelist"]:
                resolver = n["control"]["autopackagelist"]["content_resolver"]

            if type(n["control"]["autopackagelist"]["view"]) == list:
                views = n["control"]["autopackagelist"]["view"]
            else:
                views = [
                    n["control"]["autopackagelist"]["view"],
                ]

            cnf = await get_distro_packages(
                distro_url=resolver,
                distro_view=views,
            )

        if "components" in y:
            if "rpms" in cnf:
                cnf["rpms"].update(y["components"]["rpms"])
            if "modules" in cnf:
                cnf["modules"].update(y["components"]["modules"])

        for k in ("rpms", "modules"):
            if k in cnf:
                for p in cnf[k].keys():
                    components += 1
                    if k in cnf and p in cnf[k]:
                        nc[k][p] = cnf[k][p]
                    else:
                        nc[k][p] = dict()
                    cname = p
                    sname = ""
                    if k == "modules":
                        ms = split_module(p)
                        cname = ms["name"]
                        sname = ms["stream"]
                    nc[k][p]["source"] = n["defaults"][k]["source"] % {
                        "component": cname,
                        "stream": sname,
                    }
                    nc[k][p]["destination"] = n["defaults"][k]["destination"] % {
                        "component": cname,
                        "stream": sname,
                    }
                    nc[k][p]["cache"] = {
                        "source": n["defaults"]["cache"]["source"]
                        % {"component": cname, "stream": sname},
                        "destination": n["defaults"]["cache"]["destination"]
                        % {"component": cname, "stream": sname},
                    }
                    if cnf[k][p] is None:
                        cnf[k][p] = dict()
                    for ck in ("source", "destination", "target"):
                        if ck in cnf[k][p]:
                            nc[k][p][ck] = str(cnf[k][p][ck])
                    if "cache" in cnf[k][p]:
                        for ck in ("source", "destination"):
                            if ck in cnf[k][p]["cache"]:
                                nc[k][p]["cache"][ck] = str(cnf[k][p]["cache"][ck])
            logger.info(
                "Found %d configured component(s) in the %s namespace.",
                len(nc[k]),
                k,
            )
    if n["control"]["strict"]:
        logger.info(
            "Running in the strict mode.  Only configured components will be processed."
        )
    else:
        logger.info(
            "Running in the non-strict mode.  All trigger components will be processed."
        )
    if not components:
        if n["control"]["strict"]:
            logger.warning(
                "No components configured while running in the strict mode.  Nothing to do."
            )
        else:
            logger.info("No components explicitly configured.")
    main = n
    comps = nc

    # Configure the database credentials
    if not db_url:
        # Unlike other settings, the DB cannot be changed during a basic
        # config file edit. To change DB settings, the process must be
        # restarted.
        try:
            db_config = n["control"]["db"]
            db_url = sqlalchemy.URL.create(
                drivername=db_config["driver"],
                host=db_config["host"],
                port=db_config["port"],
                database=db_config["name"],
                username=db_config["user"],
                password=db_pw,
            )

        except KeyError as e:
            logger.exception(e)
            raise ConfigError("Missing database configuration")


def is_eligible(ns, comp):
    # Check whether this component is meaningful to us
    if config.main["control"]["strict"] and comp not in config.comps[ns]:
        logger.debug(f"{comp} is not an approved component, ignoring")
        return False

    for pattern in config.main["control"]["exclude"][ns]:
        if re.search(pattern, comp):
            logger.debug(f"{ns}/{comp} is on the exclude list, skipping")
            return False

    return True


def skip_tag(ns, comp):
    for pattern in config.main["control"]["skip_tag"][ns]:
        if re.search(pattern, comp):
            logger.debug(f"{ns}/{comp} is on the skip_tag list, building immediately")
            return True
    return False


def get_order(ns, comp):
    for pattern in config.main["control"]["ordering"][ns]:
        if re.search(pattern, comp):
            return config.main["control"]["ordering"][ns][pattern]

    # If we don't have a specific pattern, return a high number (1000)
    # so we always build them late in the cycle
    return 1000


def is_paused():
    return config.main["control"]["pause"]
