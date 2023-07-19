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

from . import KojiHelperBaseError
from .connection import get_buildsys
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread

logger = logging.getLogger(__name__)


class BuildInfoUnavailable(KojiHelperBaseError):
    pass


def get_scmurl(build_id):
    """Get the SCMURL that the build was created from

    :param build_id: The ID of the build (likely retrieved from a tagging message)
    :returns: A string containing the full, dereferenced SCMURL for the build
    """

    yield get_buildinfo("source", build_id)


@inlineCallbacks
def get_buildinfo(which_bsys, build_id, **kwargs):
    """
    Get all information about a particular build

    :param build_id: The ID of the build (likely retrieved from a tagging message)
    :returns: A dictionary of information about the build
    """
    bsys = get_buildsys(which_bsys)

    try:
        buildinfo = yield deferToThread(bsys.getBuild, build_id, **kwargs)
    except koji.GenericError as e:
        logger.exception(f"Could not retrieve information for build {build_id}")
        raise BuildInfoUnavailable(
            f"Could not retrieve information for build {build_id}"
        ) from e

    return buildinfo
