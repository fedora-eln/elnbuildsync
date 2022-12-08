import logging
import re
import rpm
import os
import pprint
from datetime import datetime, timezone

from collections import defaultdict
from enum import Enum, auto
from koji import BUILD_STATES
from twisted.internet import reactor, task
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread

from . import config
from . import kojihelpers
from . import listener
from . import rebuild_data

logger = config.logger
status_data = None


class BuildStatus(Enum):
    UNKNOWN = auto()
    ERRORED = auto()
    SUCCEEDED = auto()
    FAILED = auto()
    BUILDING = auto()


@inlineCallbacks
def periodic_cleanup():
    bsys = kojihelpers.get_buildsys(kojihelpers.BuildSystemType.destination)

    # We have the set of desired packages from Content Resolver
    desired_pkg_names = set(config.comps["rpms"].keys())

    # Get the list of packages currently tagged into the destination tag
    latest_tagged_dest_pkgs = yield deferToThread(
        bsys.listTagged, config.main["build"]["target"], latest=True
    )

    # Get the list of up-to-date packages in the destination tag
    # Exclude those not in the desired list, so they will be cleaned up below
    latest_tagged_dest_nvrs = set(
        [
            pkg["nvr"]
            for pkg in latest_tagged_dest_pkgs
            if pkg["name"] in desired_pkg_names
        ]
    )

    # Get the complete list of builds tagged into the destination tag
    all_tagged_dest_pkgs = yield deferToThread(
        bsys.listTagged, config.main["build"]["target"], latest=False
    )
    all_tagged_dest_nvrs = set([pkg["nvr"] for pkg in all_tagged_dest_pkgs])

    # Queue up the set of old builds to untag
    nvrs_to_untag = all_tagged_dest_nvrs - latest_tagged_dest_nvrs
    if len(nvrs_to_untag) > 0:
        logger.debug("Preparing to untag {} builds".format(len(nvrs_to_untag)))
        for nvr in nvrs_to_untag:
            logger.debug("Will untag {}".format(nvr))
    else:
        logger.debug("No builds to untag")

    if config.do_untagging and len(nvrs_to_untag) > 0:
        task.deferLater(
            reactor,
            0,
            untag_packages,
            config.main["build"]["target"],
            nvrs_to_untag,
        )

    # Packages in the desired list but not in the tag should be built
    latest_tagged_dest_pkg_names = {
        pkg["name"] for pkg in latest_tagged_dest_pkgs
    }
    pkgs_to_build = desired_pkg_names - latest_tagged_dest_pkg_names

    # Queue up the set of new builds to attempt
    rd_list = []
    for component in pkgs_to_build:
        if config.is_eligible("rpms", component):
            try:
                rd = rebuild_data.rebuild_data_from_component(
                    "rpms", component
                )
            except ValueError as e:
                logger.warn(
                    "{0} has never been built in {1}".format(
                        component, config.main["trigger"]["rpms"]
                    )
                )
                continue

            rd_list.append(rd)
    rebuild_data.build_components(config.main["build"]["target"], rd_list)

    # The remaining packages belong in the tag


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


def is_higher(evr1, evr2):
    # Returns True if they are the same or evr1 is higher than evr2
    # Returns False if evr1 is lower
    res = rpm.labelCompare(evr1, evr2) >= 0
    logger.debug(f"Comparing {evr1} to {evr2}: {res}")

    return res


def dest_is_newer(latest_src, latest_dest):
    # If there is no latest build in the destination tag, treat it as older.
    if not latest_dest:
        return False

    # Otherwise, return whether latest_dest is newer than latest_src
    return is_higher(evr(latest_dest), evr(latest_src))


def untag_packages(target, nvrs):
    if not config.dry_run:
        bsys = kojihelpers.get_buildsys(
            kojihelpers.BuildSystemType.destination
        )
        with bsys.multicall(batch=config.koji_batch) as mc:
            for nvr in nvrs:
                logger.info(f"Untagging {nvr} from {target}")
                mc.untagBuild(target, nvr)


@inlineCallbacks
def create_status_page():
    global status_data

    logger.info("Refreshing status page")

    # Get the list of desired package names
    desired_pkgs = [
        component for component in sorted(config.comps["rpms"], key=str.lower)
    ]

    bsys = kojihelpers.get_buildsys(kojihelpers.BuildSystemType.destination)

    tagged_pkgs = yield deferToThread(
        bsys.listTagged, config.main["build"]["target"], latest=True
    )
    tagged_builds = {build["name"]: build for build in tagged_pkgs}

    # Self-identify
    username = bsys.getLoggedInUser()["name"]

    _status_data = defaultdict(lambda: None)
    _status_data["__updated"] = datetime.now(timezone.utc)

    dest_url_base = kojihelpers.get_koji_config("destination")["weburl"]

    # Get the list of packages that DBS has built.
    for build in bsys.listBuilds(
        userID=username, queryOpts={"order": "start_ts"}
    ):
        pname = build["name"]
        if pname in desired_pkgs:
            # The sort order goes from oldest to newest, so if we see the same
            # package, just overwrite the build data.
            _status_data[pname] = build
            _status_data[pname]["view"] = (
                config.comps["rpms"][pname]["view"]
                if "view" in config.comps["rpms"][pname]
                else "UNKNOWN"
            )
            _status_data[pname]["status_detail"] = ""
            _status_data[pname]["build_url"] = os.path.join(
                dest_url_base, "taskinfo?taskID={}".format(build["task_id"])
            )

            if _status_data[pname]["state"] == BUILD_STATES["BUILDING"]:
                _status_data[pname]["status"] = BuildStatus.BUILDING
                continue

            elif (
                _status_data[pname]["state"] == BUILD_STATES["COMPLETE"]
                or _status_data[pname]["state"] == BUILD_STATES["FAILED"]
            ):
                # Unknown for now until we get down further
                _status_data[pname]["status"] = BuildStatus.UNKNOWN

            else:
                # Any value other than "Building", "Complete" or FAILED
                _status_data[pname]["status"] = BuildStatus.ERRORED
                _status_data[pname]["status_detail"] = "Canceled or Deleted"

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
                if re.search("\.fc\d\d$", build["tagged"]):
                    _status_data[pname]["status"] = BuildStatus.FAILED
                    _status_data[pname][
                        "status_detail"
                    ] = "Fedora build in tag"

                elif dest_is_newer(build, tagged_builds[pname]):
                    _status_data[pname]["status"] = BuildStatus.SUCCEEDED
                    _status_data[pname][
                        "status_detail"
                    ] = "Built by another user"
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

    if logger.isEnabledFor(logging.DEBUG):
        for pkg in sorted(_status_data.keys()):
            # Ignore reserved entries
            if pkg.startswith("__"):
                continue
            logger.debug(
                "{}: {}".format(
                    pkg,
                    _status_data[pkg]["start_time"]
                    if _status_data[pkg]
                    else "UNKNOWN",
                )
            )

    status_data = _status_data
    logger.debug("Status page updated")
