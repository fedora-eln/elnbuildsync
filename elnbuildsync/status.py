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
import htmlmin
from twisted.web.template import (
    Element,
    flattenString,
    renderer,
    XMLFile,
)

from . import config
from . import kojihelpers
from .kojihelpers.connection import call_koji

logger = logging.getLogger(__name__)

raw_data = None
encoded_json_data = None
web_page = None


class BuildStatus(Enum):
    UNKNOWN = auto()
    ERRORED = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    BUILDING = auto()


async def create_status_page():
    global raw_data
    global encoded_json_data
    global web_page

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
        except koji.GenericError as e:
            logger.exception(
                "Could not self-identify with Koji. Will retry in a few minutes."
            )
            return

        # TODO: Show any currently-running tasks
        # NOTE: This might be better to do live, rather than periodic.

        try:
            # Look up packages tagged into the tag associated with the target
            tagged_pkgs = await call_koji(
                bsys.listTagged, config.main["koji"]["build_target"], latest=True
            )
        except koji.GenericError as e:
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

        # Save the finalized data to the public variables
        raw_data = _status_data
        encoded_json_data = json.dumps(raw_data, default=str).encode("UTF-8")

        # Pre-generate the web page
        raw_page = await flattenString(None, StatusTableElement())
        logger.debug(f"Uncompressed page: {len(raw_page)}")

        web_page = htmlmin.minify(raw_page.decode("utf-8")).encode()
        logger.debug(f"Compressed page: {len(web_page)}")

    except:  # pylint: disable=broad-except
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


class StatusTableElement(Element):
    loader = XMLFile(os.path.join(os.path.dirname(__file__), "templates", "status.xml"))

    @renderer
    def header(self, request, tag):
        global raw_data
        update_time = raw_data["__updated"].isoformat()
        yield tag.clone().fillSlots(update_time=update_time)

    @renderer
    def builds(self, request, tag):
        global raw_data
        for pkg in sorted(raw_data.keys()):
            if pkg.startswith("__"):
                continue

            build = raw_data[pkg]

            task_url = ""

            if build is None:
                yield tag.clone().fillSlots(
                    name=pkg,
                    view="UNKNOWN",
                    nvr="UNKNOWN",
                    state="UNKNOWN",
                    detail="Not known to Koji",
                    task="",
                    task_url=task_url,
                    tagged_build="UNKNOWN",
                    build_time="UNKNOWN",
                )

            else:
                if "task_id" in build:
                    task = str(build["task_id"])
                    task_url = build.get("build_url", "")

                if build["status"] == BuildStatus.SUCCEEDED:
                    state = "SUCCESS"
                elif build["status"] == BuildStatus.BUILDING:
                    state = "Building"
                elif build["status"] == BuildStatus.FAILED:
                    state = "FAILED"
                else:
                    state = "Error"

                detail = build["status_detail"] if build["status_detail"] else ""

                tagged_build = build.get("tagged", "UNKNOWN")

                build_time = "UNKNOWN"
                if "start_ts" in build and build["start_ts"]:
                    build_time = datetime.fromtimestamp(
                        build["start_ts"], tz=timezone.utc
                    ).isoformat()

                yield tag.clone().fillSlots(
                    name=pkg,
                    view=build["view"],
                    nvr=build["nvr"],
                    state=state,
                    task=task,
                    task_url=task_url,
                    detail=detail,
                    tagged_build=tagged_build,
                    build_time=build_time,
                )
