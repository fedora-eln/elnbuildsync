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
import koji
import logging
import time

from cachetools import cached, TTLCache
from requests.exceptions import RequestException
from twisted.internet.threads import deferToThread

from .. import config

from .errors import KojiHelperBaseError


logger = logging.getLogger(__name__)


class BuildSysUnavailable(KojiHelperBaseError):
    pass


# Single cached session; TTL slightly less than an hour to be safe.
@cached(cache=TTLCache(maxsize=1, ttl=3550))
def get_buildsys():
    """Get an authenticated koji build system session. Caches the session
    so future calls are cheap.

    :returns: Koji session object, or None on error
    """
    if not config.main:
        logger.critical("DistroBuildSync is not configured, aborting.")
        raise BuildSysUnavailable

    profile = config.main["koji"]["profile"]
    logger.debug(
        'Initializing the koji instance with the "%s" profile.',
        profile,
    )

    bsys = None
    while not bsys:
        try:
            cfg = koji.read_config(profile_name=profile)
            bsys = koji.ClientSession(cfg["server"], opts=cfg)
        except Exception:
            logger.exception(
                'Failed initializing the koji instance with the "%s" profile, skipping.',
                profile,
            )
            bsys = None
            time.sleep(1)
    logger.debug("The koji instance initialized.")

    logger.debug("Authenticating with the koji instance.")
    while not bsys.logged_in:
        try:
            bsys.logout()
            bsys.gssapi_login()
        except koji.GSSAPIAuthError:
            logger.exception(
                "Failed authenticating against the koji instance, retrying."
            )
            time.sleep(1)
            continue

        username = bsys.getLoggedInUser()["name"]
        logger.debug(
            "Successfully authenticated with the koji instance as user %s",
            username,
        )

    return bsys


def get_koji_url():
    cfg = koji.read_config(profile_name=config.main["koji"]["profile"])
    return cfg["weburl"]


# Wrap the call to koji in a backoff decorator to handle transient errors
# and give up on 400-499 errors.
@backoff.on_exception(
    backoff.expo,
    RequestException,
    giveup=lambda e: 400 <= e.response.status_code < 500,
    max_time=60,
)
@backoff.on_exception(backoff.expo, Exception, max_time=60)
async def call_koji(method, *args, **kwargs):
    return await deferToThread(method, *args, **kwargs)
