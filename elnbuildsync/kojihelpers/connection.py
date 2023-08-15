# This file is part of ELNBuildSync
# Copyright (C) 2023  Stephen Gallagher <sgallagh@redhat.com>

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


import koji
import logging
import time

from cachetools import cached, TTLCache
from enum import auto, Enum

from .. import config

from .errors import KojiHelperBaseError


logger = logging.getLogger(__name__)


class BuildSystemType(Enum):
    source = auto()
    destination = auto()


class BuildSysUnavailable(KojiHelperBaseError):
    pass


# Set the cache size to be equal to the number of build system types
# multiplied by the possible values for force_login (2)
#
# Set TTL to slightly less than an hour, to be safe
@cached(cache=TTLCache(maxsize=len(BuildSystemType.__members__) * 2, ttl=3550))
def get_buildsys(which, force_login=False):
    """Get a koji build system session for either the source or the
    destination.  Caches the sessions so future calls are cheap.
    Destination sessions are authenticated, source sessions are not.

    :param which: Session to select, source or destination
    :param bool force_login: Login also on source instance.
    :returns: Koji session object, or None on error
    """
    if not config.main:
        logger.critical("DistroBuildSync is not configured, aborting.")
        raise BuildSysUnavailable

    try:
        bsys_type = BuildSystemType(which)
    except ValueError as e:
        # If we were not passed an integer/BuildSystemType for `which`,
        # See if we recognize it as a string. This is mostly to avoid
        # needing to replace all the places this is called, but still
        # Guarantee that it must be a known value.
        try:
            bsys_type = BuildSystemType[which]
        except KeyError as e:
            logger.error("Cannot get {} build system.".format(which))
            return None

    logger.debug(
        'Initializing the %s koji instance with the "%s" profile.',
        bsys_type.name,
        config.main[bsys_type.name]["profile"],
    )

    bsys = None
    while not bsys:
        try:
            cfg = koji.read_config(profile_name=config.main[bsys_type.name]["profile"])
            bsys = koji.ClientSession(cfg["server"], opts=cfg)
        except Exception:
            logger.exception(
                'Failed initializing the %s koji instance with the "%s" profile, skipping.',
                bsys_type.name,
                config.main[bsys_type.name]["profile"],
            )
            bsys = None
            time.sleep(1)
    logger.debug("The %s koji instance initialized.", bsys_type.name)
    if bsys_type is BuildSystemType.destination or force_login:
        logger.debug("Authenticating with the %s koji instance." % bsys_type.name)

        while not bsys.logged_in:
            try:
                # It's safe to always log out. It's a no-op if not currently logged in,
                # but we want to make sure the gssapi_login() runs.
                bsys.logout()
                bsys.gssapi_login()
            except koji.GSSAPIAuthError as e:
                logger.exception(
                    "Failed authenticating against the %s koji instance, retrying."
                    % bsys_type.name
                )
                time.sleep(1)
                continue

            username = bsys.getLoggedInUser()["name"]
            logger.debug(
                f"Successfully authenticated with the %s koji instance as user {username}"
                % bsys_type.name
            )

    return bsys


def get_koji_url():
    cfg = koji.read_config(profile_name=config.main["destination"]["profile"])
    return cfg["weburl"]
