from . import config

import koji
import requests
import yaml

from cachetools import cached, TTLCache
from enum import auto, Enum
from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks

logger = config.logger


# Koji magic number
KOJI_BACKGROUND_PRIORITY = 5


class BuildSystemType(Enum):
    source = auto()
    destination = auto()


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
        return None

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

    try:
        cfg = koji.read_config(
            profile_name=config.main[bsys_type.name]["profile"]
        )
        bsys = koji.ClientSession(cfg["server"], opts=cfg)
    except Exception:
        logger.exception(
            'Failed initializing the %s koji instance with the "%s" profile, skipping.',
            bsys_type.name,
            config.main[bsys_type.name]["profile"],
        )
        return None
    logger.debug("The %s koji instance initialized.", bsys_type.name)
    if bsys_type is BuildSystemType.destination or force_login:
        logger.debug(
            "Authenticating with the %s koji instance." % bsys_type.name
        )
        try:
            # It's safe to always log out. It's a no-op if not currently logged in,
            # but we want to make sure the gssapi_login() runs.
            bsys.logout()
            bsys.gssapi_login()
        except Exception:
            logger.exception(
                "Failed authenticating against the %s koji instance, skipping."
                % bsys_type.name
            )
            return None
        logger.debug(
            "Successfully authenticated with the %s koji instance."
            % bsys_type.name
        )

    return bsys


def get_koji_config(which):
    bsys_type = BuildSystemType[which]
    return koji.read_config(
        profile_name=config.main[bsys_type.name]["profile"]
    )


def get_build_info(nvr):
    """Get SCMURL, plus extra attributes for modules, for a source build system
    build NVR.  NVRs are unique.

    :param nvr: The build NVR to look up
    :returns: A dictionary with `scmurl`, `name`, `stream`, and `modulemd` keys,
    or None on error
    """
    if not config.main:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None

    bsys = get_buildsys("source")
    if bsys is None:
        logger.error(
            "Build system unavailable, cannot retrieve the build info of %s.",
            nvr,
        )
        return None
    try:
        bsrc = bsys.getBuild(nvr)
    except Exception:
        logger.exception(
            "An error occured while retrieving the build info for %s.", nvr
        )
        return None

    bi = dict()
    if "source" in bsrc:
        bi["scmurl"] = bsrc["source"]
        logger.debug("Retrieved SCMURL for %s: %s", nvr, bi["scmurl"])
    else:
        logger.error("Cannot find any SCMURL associated with %s.", nvr)
        return None

    try:
        minfo = bsrc["extra"]["typeinfo"]["module"]
        bi["name"] = minfo["name"]
        bi["stream"] = minfo["stream"]
        bi["module_version"] = minfo["version"]
        bi["modulemd"] = minfo["modulemd_str"]
        logger.debug(
            "Actual name:stream for %s is %s:%s", nvr, bi["name"], bi["stream"]
        )
    except Exception:
        bi["name"] = None
        bi["stream"] = None
        bi["module_version"] = None
        bi["modulemd"] = None
        logger.debug("No module info for %s.", nvr)

    return bi


def get_ref_overrides(modulemd):
    """
    Get RPM components ref overrides from the modulemd file.
    """
    ref_overrides = {}
    data = yaml.safe_load(modulemd)
    for name, rpm_data in data["data"]["xmd"]["mbs"].get("rpms", {}).items():
        ref_overrides[name] = rpm_data["ref"]
    logger.info(f"RPM ref overrides {ref_overrides}")
    return ref_overrides


def get_build(comp, ns="rpms", tag=None, bsys=None):
    """Get the latest build NVR for the specified component.  Searches the
    component namespace trigger tag to locate this.  Note this is not the
    highest NVR, it's the latest tagged build.

    :param comp: The component name
    :param ns: The component namespace
    :param tag: If specified, overrides the default tag for the namespace
    :param bsys: If not specified, defaults to the "source" buildsystem
    :returns: NVR of the latest build, or None on error
    """
    if not config.main:
        logger.critical("DistroBuildSync is not configured, aborting.")
        return None

    if bsys is None:
        bsys = get_buildsys("source")
        if bsys is None:
            logger.error(
                "Build system unavailable, cannot find the latest build for %s/%s.",
                ns,
                comp,
            )
            return None

    if ns == "rpms":
        try:
            rpmtag = tag if tag else config.main["trigger"][ns]
            nvr = bsys.listTagged(rpmtag, package=comp, latest=True)
        except Exception:
            logger.exception(
                "An error occured while getting the latest build for %s/%s.",
                ns,
                comp,
            )
            return None
        if nvr:
            logger.debug(
                "Located the latest build for %s/%s: %s",
                ns,
                comp,
                nvr[0]["nvr"],
            )
            return nvr[0]
        logger.debug("Did not find any builds for %s/%s.", ns, comp)
        return None

    if ns == "modules":
        ms = config.split_module(comp)
        cname = ms["name"]
        sname = ms["stream"]
        try:
            moduletag = tag if tag else config.main["trigger"][ns]
            builds = bsys.listTagged(moduletag)
        except Exception:
            logger.exception(
                "An error occured while getting the latest builds for %s/%s.",
                ns,
                cname,
            )
            return None
        if not builds:
            logger.error("Did not find any builds for %s/%s.", ns, cname)
            return None
        logger.debug("Found %d total builds for %s/%s", len(builds), ns, cname)
        # find the latest build for name:stream
        latest = None
        latest_version = 0
        for b in builds:
            binfo = get_build_info(b["nvr"])
            if (
                binfo is None
                or binfo["name"] is None
                or binfo["stream"] is None
            ):
                logger.error(
                    "Could not get module info for %s, skipping.", b["nvr"]
                )
            elif (
                cname == binfo["name"]
                and sname == binfo["stream"]
                and int(binfo["module_version"]) >= latest_version
            ):
                latest = b
                latest_version = int(binfo["module_version"])
        if latest:
            logger.debug(
                "Located the latest build for %s/%s: %s",
                ns,
                comp,
                latest["nvr"],
            )
            return latest
        logger.error("Did not find any builds for %s/%s.", ns, comp)
        return None

    logger.error("Unrecognized namespace: %s/%s", ns, comp)
    return None


def get_target_info(target):
    """Get information about a build target

    :param target: the string name of the target
    :returns: A dictionary with the keys 'build_tag', 'build_tag_name',
    'dest_tag', 'dest_tag_name', 'id' and 'name' or None on error
    """
    bsys = get_buildsys("destination")
    if bsys is None:
        logger.error(
            "Build system unavailable, cannot retrieve the target info of %s.",
            target,
        )
        return None

    try:
        targetinfo = bsys.getBuildTarget(target)
    except Exception as e:
        logger.critical(e)
        logger.exception(
            "An error occured while retrieving the target info for %s.", target
        )
        return None

    return targetinfo


def get_scmurl(build_id):
    """Get the SCMURL that the build was created from

    :param build_id: The ID of the build (likely retrieved from a tagging message)
    :returns: A string containing the full, dereferenced SCMURL for the build
    """

    bsys = get_buildsys("source")
    if bsys is None:
        logger.error(
            f"Build system unavailable, cannot retrieve the SCMURL of {build_id}."
        )
        return None

    try:
        buildinfo = bsys.getBuild(build_id, strict=True)
    except koji.GenericError as e:
        logger.exception(
            f"Could not retrieve information for build {build_id}"
        )
        return None

    return buildinfo["source"]


def wait_repo(tag):
    deferred = Deferred()
    deferred.addTimeout(config.waitrepo_timeout, reactor)
    config.awaited_repos[tag].append(deferred)

    logger.info(f"Waiting for {tag} to regenerate")
    return deferred


def call_distrogitsync(ns, comp, ref_overrides=None):
    compset = [(ns, comp)]
    ref_overrides = ref_overrides or {}
    for c in ref_overrides.keys():
        compset.append(("rpms", c))
    for namespace, c in compset:
        if config.distrogitsync:
            logger.info("Calling distrogitsync for %s/%s" % (namespace, c))
            try:
                r = requests.post(
                    "%s/%s/%s" % (config.distrogitsync, namespace, c)
                )
                r.raise_for_status()
            except requests.exceptions.RequestException:
                logger.exception("Failed to contact distrogitsync")
                continue


def create_side_tag(downstream_target, upstream_sidetag):
    """
    Creates new downstream sidetag based inheriting the build tag of
    `downstream_target` and adds `downstream_sidetag` "extra" record
    to `upstream_sidetag` in upstream Koji so it is possible to map
    upstream sidetag to downstream sidetag.

    If the `downstream_sidetag` already exists, it returns it.

    :params str downstream_target: Downstream target name.
    :params str upstream_sidetag: Upstream sidetag name.
    :return str: Name of the downstream sidetag.
    """

    # Create downstream sidetag only if it does not exist.
    upstream_koji = get_buildsys("source", force_login=True)
    upstream_tag = upstream_koji.getTag(upstream_sidetag)
    if "downstream_sidetag" in upstream_tag["extra"]:
        downstream_sidetag = upstream_tag["extra"]["downstream_sidetag"]
        logger.info(
            "Downstream sidetag for %s already exists: %s."
            % (upstream_sidetag, downstream_sidetag)
        )
        return downstream_sidetag

    logger.info("Creating downstream sidetag for %s." % upstream_sidetag)
    # Get downstream build tag.
    downstream_koji = get_buildsys("destination")
    downstream_target = downstream_koji.getBuildTarget(downstream_target)
    downstream_tag = downstream_target["build_tag_name"]

    # Create downstream sidetag
    if not config.dry_run:
        downstream_sidetag = downstream_koji.createSideTag(
            downstream_tag, suffix="stack-gate"
        )["name"]
    else:
        logger.info(
            "Running in dry_run mode, not creating downstream_sidetag for %s."
            % downstream_tag
        )
        downstream_sidetag = "%s-dry-run-mode-stack-gate" % downstream_tag

    # Set the mapping between upstream sidetag and downstream sidetag.
    if not config.dry_run:
        upstream_koji.editTag2(
            upstream_sidetag, extra={"downstream_sidetag": downstream_sidetag}
        )
        logger.info(
            "Downstream sidetag for %s created: %s."
            % (upstream_sidetag, downstream_sidetag)
        )
    else:
        logger.info(
            "Running in dry_run mode, not editing upstream_sidetag %s ."
            % upstream_sidetag
        )
    return downstream_sidetag
