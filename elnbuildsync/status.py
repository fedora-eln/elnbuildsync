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

import json
import koji
import logging
import os
import re
import rpm

from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum, auto

from . import config
from . import kojihelpers
from .kojihelpers.connection import call_koji

logger = logging.getLogger(__name__)

encoded_json_data = None


class BuildStatus(Enum):
    UNKNOWN = auto()
    ERRORED = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    BUILDING = auto()


async def create_status_page():
    global encoded_json_data

    try:
        logger.info("Refreshing status page")

        # Get the list of desired package names
        desired_pkgs = [
            component
            for component in sorted(
                config.comps["downstream_components"], key=str.lower
            )
        ]

        bsys = kojihelpers.connection.get_buildsys()

        try:
            # Self-identify
            username = bsys.getLoggedInUser()["name"]
        except koji.GenericError:
            logger.exception(
                "Could not self-identify with Koji. Will retry in a few minutes."
            )
            return

        # TODO: Show any currently-running tasks
        # NOTE: This might be better to do live, rather than periodic.

        try:
            # Look up packages tagged into the stable tag
            tagged_pkgs = await call_koji(
                bsys.listTagged, config.main["koji"]["stable_tag"], latest=True
            )
        except koji.GenericError:
            logger.exception(
                "Could not communicate with Koji. Will retry in a few minutes."
            )
            return

        tagged_builds = {build["name"]: build for build in tagged_pkgs}

        # Start preparing the raw data
        _status_data = defaultdict(lambda: None)
        _status_data["__updated"] = datetime.now(timezone.utc)

        koji_url = kojihelpers.connection.get_koji_url()

        # Get the list of packages that DBS has built.
        built_packages = await call_koji(
            bsys.listBuilds, userID=username, queryOpts={"order": "start_ts"}
        )
        for build in built_packages:
            pname = build["name"]
            if pname in desired_pkgs:
                # The sort order goes from oldest to newest, so if we see the same
                # package, just overwrite the build data.
                if pname in _status_data:
                    _status_data[pname].update(build)
                else:
                    _status_data[pname] = build

                _status_data[pname]["view"] = (
                    config.comps["downstream_components"][pname]["view"]
                    if "view" in config.comps["downstream_components"][pname]
                    else "UNKNOWN"
                )

                _status_data[pname]["status_detail"] = ""
                _status_data[pname]["build_url"] = os.path.join(
                    koji_url,
                    "taskinfo?taskID={}".format(build["task_id"]),
                )

                if _status_data[pname]["state"] == koji.BUILD_STATES["BUILDING"]:
                    _status_data[pname]["status"] = BuildStatus.BUILDING
                    continue
                else:
                    # Unknown for now until we get down further
                    _status_data[pname]["status"] = BuildStatus.UNKNOWN

                if "tagged" not in _status_data[pname]:
                    # Set a default of "Unknown"
                    _status_data[pname]["tagged"] = "UNKNOWN"

                if pname in tagged_builds and "nvr" in tagged_builds[pname]:
                    _status_data[pname]["tagged"] = tagged_builds[pname]["nvr"]

                if build["nvr"] == _status_data[pname]["tagged"]:
                    _status_data[pname]["status"] = BuildStatus.SUCCEEDED
                elif (
                    pname in tagged_builds
                    and _status_data[pname]["status"] == BuildStatus.UNKNOWN
                ):
                    # Check whether the latest tagged package is ELN or Fedora
                    if re.search(r"\.fc\d\d$", _status_data[pname]["tagged"]):
                        _status_data[pname]["status"] = BuildStatus.FAILED
                        _status_data[pname]["status_detail"] = "Fedora build in tag"

                    elif dest_is_newer(build, tagged_builds[pname]):
                        _status_data[pname]["status"] = BuildStatus.SUCCEEDED
                        _status_data[pname]["status_detail"] = "Built by another user"
                    else:
                        _status_data[pname]["status"] = BuildStatus.FAILED
                        _status_data[pname]["status_detail"] = "Build failed"
                else:
                    _status_data[pname]["status"] = BuildStatus.FAILED
                    _status_data[pname]["status_detail"] = "Build is not tagged"

        # Now double-check that we didn't miss any expected packages
        # This will use the defaultdict to set the value to None for
        # any packages not in the list
        [_status_data[pkg] for pkg in desired_pkgs]

        # Build JSON-serializable copy with string status for the frontend
        serializable_data = _build_serializable_status(_status_data)
        encoded_json_data = json.dumps(serializable_data, default=str).encode("UTF-8")

    except Exception:  # noqa: S110
        # Normally it's bad to catch all exceptions, but in this case the
        # status page is purely cosmetic and will retry in a few minutes.
        logger.exception("Unexpected error while refreshing status page.")

    logger.info("Status page update completed.")


def evr(build):
    # if build['epoch']:
    #     epoch = str(build['epoch'])
    # else:
    #     epoch = "0"
    # #  epoch's are important, but we just want to
    # #  know if we need to rebuild the package
    # #  so for this, they are not important.
    epoch = "0"
    version = build["version"]
    p = re.compile(".(fc|eln)[0-9]*")
    # release = re.sub(p, "", build["release"])
    release = build["release"]
    return epoch, version, release


def is_higher(pkg1, pkg2):
    # Returns True if they are the same or evr1 is higher than evr2
    # Returns False if evr1 is lower
    return rpm.labelCompare(evr(pkg1), evr(pkg2)) >= 0


def dest_is_newer(latest_src, latest_dest):
    # If there is no latest build in the destination tag, treat it as older.
    if not latest_dest:
        return False

    # Otherwise, return whether latest_dest is newer than latest_src
    return is_higher(latest_dest, latest_src)


def _status_display_string(build_status):
    """Map BuildStatus enum to string for JSON/frontend. Use UNKNOWN for else case."""
    if build_status == BuildStatus.SUCCEEDED:
        return "SUCCESS"
    if build_status == BuildStatus.BUILDING:
        return "Building"
    if build_status == BuildStatus.FAILED:
        return "FAILED"
    return "UNKNOWN"


def _build_serializable_status(_status_data):
    """Build a dict suitable for JSON: same structure as _status_data but status is a string."""
    result = {}
    for key, value in _status_data.items():
        if key.startswith("__"):
            result[key] = value
            continue
        if value is None:
            result[key] = None
            continue
        entry = dict(value)
        if "status" in entry and isinstance(entry["status"], BuildStatus):
            entry["status"] = _status_display_string(entry["status"])
        result[key] = entry
    return result
