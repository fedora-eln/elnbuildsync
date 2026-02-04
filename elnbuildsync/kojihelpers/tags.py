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

from cachetools import cached, LRUCache
from twisted.internet import reactor
from twisted.internet.defer import DeferredList
from twisted.internet.defer import TimeoutError as DeferredTimeoutError
from .connection import call_koji

from .. import kojihelpers
from .. import listener
from .connection import get_buildsys

from .. import config


logger = logging.getLogger(__name__)


async def prepare_side_tag(base_tag, initial_build_ids=list()):
    """
    Creates a Koji side tag based on @base_tag

    Requests the side-tag and awaits until the repo has been generated.

    :params str base_tag: The build tag to inherit from (e.g. f39-build)
    :params list initial_packages: The set of build_ids that will be tagged
    into this side-tag.
    :return twisted.Deferred: A Twisted Deferred whose callback will fire once
    the repo is ready for use.
    """

    downstream_koji = get_buildsys("destination")
    # Trigger the creation of the side-tag
    logger.info(f"Creating side tag from {base_tag}")
    side_tag_info = await call_koji(downstream_koji.createSideTag, base_tag)
    side_tag_name = side_tag_info["name"]

    logger.debug(f"Side {side_tag_name} created.")

    if initial_build_ids:
        # Convert builds to nvrs to make logging easier
        buildinfos = await kojihelpers.builds.get_multi_buildinfo(
            "destination", initial_build_ids
        )
        nvrs = [buildinfo["nvr"] for buildinfo in buildinfos.values()]

        # Tag the builds
        # We'll ignore the task index here, since we're actually going to
        # monitor the tag, rather than the tasks.
        _ = await tag_builds(side_tag_name, nvrs)

        # Wait for the builds to appear in the tag
        results = await wait_for_nvrs_in_tag(side_tag_name, nvrs)
        for success, value in results:
            if success:
                logger.info(f"Build {value} tagged into {side_tag_name}")
            else:
                # The most likely scenario here is that the tagging timed out,
                # so we'll just proceed. Failures here are not really
                # recoverable. Log and continue.
                logger.error(
                    f"Build failed to tag into {side_tag_name}", exc_info=value
                )

    return side_tag_name


async def tag_builds(tag, build_ids):
    """
    Tag a list of builds into a tag.

    :params str tag: The tag name to tag into
    :params list build_ids: The list of nvrs or build IDs to tag
    :return dict: A dictionary of task_id -> Koji vcall
    """
    task_index = await call_koji(_tag_builds_thread, tag, build_ids)
    logger.debug(f"Tagged {len(build_ids)} builds into {tag}")

    return task_index


def _tag_builds_thread(tag, build_ids):
    """
    Tag a list of nvrs into a tag.

    :params str tag: The tag name to tag into
    :params list build_ids: The list of nvrs or build IDs to tag
    :return dict: A dictionary of task_id -> Koji vcall
    """
    downstream_koji = get_buildsys("destination")
    build_vcalls = dict()

    try:
        with downstream_koji.multicall(batch=config.koji_batch) as mc:
            logger.info(f"Tagging {len(build_ids)} builds into {tag}")
            for build_id in build_ids:
                build_vcalls[build_id] = mc.tagBuild(tag, build_id)
    except Exception as e:
        logger.exception(e)
        raise

    task_index = dict()
    for build_id, vcall in build_vcalls.items():
        task_id = vcall.result
        task_index[build_id] = task_id
        logger.info(f"Tagging build {build_id} into {tag} via task {task_id}")

    return task_index


async def untag_builds(tag, builds):
    await call_koji(_untag_builds_thread, tag, builds)
    logger.debug(f"Untagged {len(builds)} builds from {tag}")
    return


def _untag_builds_thread(tag, build_ids):
    downstream_koji = get_buildsys("destination")

    with downstream_koji.multicall(batch=config.koji_batch) as mc:
        logger.info(f"Untagging {len(build_ids)} builds from {tag}")
        for build_id in build_ids:
            mc.untagBuild(tag, build_id, strict=False)


async def get_tags_for_target(target):
    """
    Returns: buildroot_tag, destination_tag
    """

    buildroot_tag, destination_tag = await call_koji(
        _get_tags_for_target_thread, target
    )

    return buildroot_tag, destination_tag


@cached(cache=LRUCache(maxsize=4))
def _get_tags_for_target_thread(target):
    bsys = kojihelpers.connection.get_buildsys("destination")
    targetinfo = bsys.getBuildTarget(target)
    logger.debug(f"Target info: {targetinfo}")
    return targetinfo["build_tag_name"], targetinfo["dest_tag_name"]


async def remove_side_tag(side_tag):
    await call_koji(_remove_side_tag_thread, side_tag)


def _remove_side_tag_thread(side_tag):
    bsys = kojihelpers.connection.get_buildsys("destination")
    bsys.removeSideTag(side_tag)


async def wait_for_nvrs_in_tag(tag, nvrs):
    """
    Wait for a list of nvrs to appear in a tag.

    :params str tag: The tag name to wait for
    :params list nvrs: The list of nvrs to wait for
    :return list: A list of results
    """
    logger.info(f"Waiting for {len(nvrs)} nvrs to appear in tag {tag}")

    deferreds = list()
    for nvr in nvrs:
        deferred = listener.register_nvr_tag(tag, nvr, timeout=config.tag_timeout)
        deferreds.append(deferred)

    result = await DeferredList(deferreds, consumeErrors=True)
    return result


async def get_nvrs_from_tag(tag):
    """
    Get the list of builds tagged into a tag.

    :params str tag: The tag name to get builds from
    :return dict: A dictionary of nvr -> buildinfo
    """
    bsys = kojihelpers.connection.get_buildsys("destination")
    builds = await call_koji(bsys.listTagged, tag, latest=False, inherit=True)
    return {build["nvr"]: build for build in builds}
