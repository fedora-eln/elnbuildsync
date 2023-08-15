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

import logging

from cachetools import cached, LRUCache
from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks
from twisted.internet.threads import deferToThread

from .. import kojihelpers
from .connection import get_buildsys

from .. import config


logger = logging.getLogger(__name__)


@inlineCallbacks
def prepare_side_tag(base_tag, initial_build_ids=list()):
    """
    Creates a Koji side tag based on @base_tag

    Requests the side-tag and yields until the repo has been generated.

    :params str base_tag: The build tag to inherit from (e.g. f39-build)
    :params list initial_packages: The set of build_ids that will be tagged
    into this side-tag.
    :return twisted.Deferred: A Twisted Deferred whose callback will fire once
    the repo is ready for use.
    """

    downstream_koji = get_buildsys("destination")
    # Trigger the creation of the side-tag
    logger.info(f"Creating side tag from {base_tag}")
    side_tag_info = yield deferToThread(downstream_koji.createSideTag, base_tag)
    side_tag_name = side_tag_info["name"]

    logger.debug(f"Side {side_tag_name} created.")

    if initial_build_ids:
        yield tag_builds(side_tag_name, initial_build_ids)

    # Wait for koji to generate the buildroot repo
    logger.info(f"Waiting for {side_tag_name} to generate.")
    try:
        yield wait_repo(side_tag_name)
    except TimeoutError as e:
        logger.error(f"Timed out awaiting side-tag {side_tag_name}", exc_info=True)
        try:
            yield deferToThread(downstream_koji.removeSideTag, side_tag_name)
        except Exception:
            logger.warning(f"Unable to remove {side_tag_name}")

        # Re-raise the timeout error to the caller
        raise

    return side_tag_name


@inlineCallbacks
def tag_builds(tag, builds):
    yield deferToThread(_tag_builds_thread, tag, builds)


def _tag_builds_thread(tag, build_ids):
    downstream_koji = get_buildsys("destination")

    with downstream_koji.multicall(batch=config.koji_batch) as mc:
        logger.info(f"Tagging {len(build_ids)} builds into {tag}")
        for build_id in build_ids:
            mc.tagBuild(tag, build_id)


@inlineCallbacks
def wait_repo(tag):
    """
    Wait for a repo regeneration to begin and then to complete

    Note: there is a possibility of a small race-condition where the repo
    may begin regenerating slightly before this function starts listening
    for it. In that case, it may be waiting until the next time the regen
    begins. If the initial start is not important, use wait_repo_regen()
    instead.
    """
    yield _wait_repo_init(tag)
    yield _wait_repo_regen(tag)

    return tag


@inlineCallbacks
def wait_repo_regen(tag):
    """
    Wait for a repo to regenerate without first waiting for the regen to start.

    This should be used whenever a repo is created for the first time.
    """
    yield _wait_repo_regen(tag)

    return tag


def _wait_repo_init(tag):
    deferred = Deferred()
    deferred.addTimeout(config.waitrepo_init_timeout, reactor)
    kojihelpers.awaiting_repo_init[tag].append(deferred)

    logger.info(f"Waiting for {tag} to begin regenerating")
    return deferred


def _wait_repo_regen(tag):
    deferred = Deferred()
    deferred.addTimeout(config.waitrepo_timeout, reactor)
    kojihelpers.awaited_repos[tag].append(deferred)

    logger.info(f"Waiting for {tag} to finish regenerating")
    return deferred


@inlineCallbacks
def get_tags_for_target(target):
    """
    Returns: buildroot_tag, destination_tag
    """

    buildroot_tag, destination_tag = yield deferToThread(
        _get_tags_for_target_thread, target
    )

    return buildroot_tag, destination_tag


@cached(cache=LRUCache(maxsize=4))
def _get_tags_for_target_thread(target):
    bsys = kojihelpers.connection.get_buildsys("destination")
    targetinfo = bsys.getBuildTarget(target)
    logger.debug(f"Target info: {targetinfo}")
    return targetinfo["build_tag_name"], targetinfo["dest_tag_name"]


@inlineCallbacks
def remove_side_tag(side_tag):
    yield deferToThread(_remove_side_tag_thread, side_tag)


def _remove_side_tag_thread(side_tag):
    bsys = kojihelpers.connection.get_buildsys("destination")
    bsys.removeSideTag(side_tag)
