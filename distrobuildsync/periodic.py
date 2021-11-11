import re
import rpm

from collections import defaultdict
from twisted.internet import reactor, task
from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread

from . import config
from . import kojihelpers
from . import listener
from . import rebuild_data

logger = config.logger


@inlineCallbacks
def periodic_cleanup():
    components = {}
    to_untag = set()
    to_build = set()

    # Get the list of desired package names
    desired_pkgs = [
        component for component in sorted(config.comps["rpms"], key=str.lower)
    ]

    # Get the list of the latest packages currently tagged into the
    # destination tag.
    logger.info("Looking up builds. This may take a long time.")
    bsys = kojihelpers.get_buildsys(kojihelpers.BuildSystemType.destination)
    tagged_src_pkgs = yield deferToThread(bsys.listTagged, config.main["trigger"]["rpms"], latest=True)
    tagged_dest_pkgs = yield deferToThread(bsys.listTagged, config.main["build"]["target"], latest=True)
    all_tagged_dest_pkgs = yield deferToThread(bsys.listTagged, config.main["build"]["target"], latest=False)
    tagged_dest_pkg_names = sorted([pkg["name"] for pkg in tagged_dest_pkgs])

    # Create a lookup table to make untagging easier later
    all_dest_nvrs = defaultdict(list)
    for pkg in all_tagged_dest_pkgs:
        all_dest_nvrs[pkg["name"]].append(pkg["nvr"])

    rd_list = list()

    src_builds = {build["name"]: build for build in tagged_src_pkgs}
    dest_builds = {build["name"]: build for build in tagged_dest_pkgs}

    # Make sure we have all the desired packages built
    for pkgname in desired_pkgs:
        if pkgname not in tagged_dest_pkg_names:
            # No build for this package exists.
            # Check whether we're explicitly ignoring this package (because it
            # requires special handling).·
            if not config.is_eligible("rpms", pkgname):
                logger.warning("Skipping {} because it is excluded".format(pkgname))
                continue

            # Schedule it for building
            try:
                rd_list.append(rebuild_data.rebuild_data_from_component("rpms", pkgname))
            except ValueError as e:
                logger.warning(e)
            continue

        # Get the latest build from the source and dest tags for comparison
        latest_src = src_builds[pkgname] if pkgname in src_builds else None
        if not latest_src:
            logger.critical(
                "Requested package {} does not exist in the {} tag. Ignoring.".format(
                    pkgname, config.main["trigger"]["rpms"]
                )
            )
            continue
        latest_dest = dest_builds[pkgname] if pkgname in dest_builds else None
        if dest_is_older(latest_src, latest_dest):
            if config.is_eligible("rpms", pkgname):
                try:
                    rd_list.append(rebuild_data.rebuild_data_from_component("rpms", pkgname))
                except ValueError as e:
                    logger.critical(e)

                logger.warning(
                    "Package {} will be rebuilt for {}".format(
                        pkgname, config.main["build"]["target"]
                    )
                )
            continue

    # Check whether any of the packages we currently have in the tag
    # are no longer needed.
    for pkgname in tagged_dest_pkg_names:
        # Check whether it's no longer needed
        if pkgname not in desired_pkgs:
            try:
                for nvr in all_dest_nvrs[pkgname]:
                    logger.warning("Adding {} to the untag list.".format(nvr))
                    to_untag.add(nvr)
            except ValueError as e:
                logger.critical(e)
            continue

    # Untag the packages we no longer have in ELN
    # This doesn't need to be blocking, so we defer it to run whenever
    # the mainloop has time.
    if len(to_untag) > 0:
        task.deferLater(
            reactor, 0, untag_packages, config.main["build"]["target"], to_untag
        )

    # Fire off the builds
    if len(rd_list) > 0:
        yield listener.rebuild_batch(config.main["build"]["target"], rd_list)


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
    release = re.sub(p, "", build["release"])
    return epoch, version, release


def is_higher(evr1, evr2):
    return rpm.labelCompare(evr1, evr2) > 0


def dest_is_older(latest_src, latest_dest):
    logger.debug("Comparing {} and {}".format(latest_src, latest_dest))

    # If there is no latest build in the destination tag, treat it as older.
    if not latest_dest:
        return True

    # Otherwise, return whether latest_src is newr than latest_dest
    return is_higher(evr(latest_src), evr(latest_dest))


def untag_packages(target, nvrs):
    if not config.dry_run:
        bsys = kojihelpers.get_buildsys(kojihelpers.BuildSystemType.destination)
        with bsys.multicall(batch=config.koji_batch) as mc:
            for nvr in nvrs:
                logger.info(f"Untagging {nvr} from {target}")
                mc.untagBuild(target, nvr)
