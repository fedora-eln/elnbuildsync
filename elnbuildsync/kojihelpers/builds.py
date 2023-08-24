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

from .errors import InfoUnavailableError, IneligibleBuildError
from .connection import get_buildsys
from .. import config
from .. import listener
from twisted.internet import reactor
from twisted.internet.defer import DeferredList, inlineCallbacks
from twisted.internet.threads import deferToThread

logger = logging.getLogger(__name__)


# Koji magic number
KOJI_BACKGROUND_PRIORITY = 5


@inlineCallbacks
def get_scmurl(build_id):
    """Get the SCMURL that the build was created from

    :param build_id: The ID of the build (likely retrieved from a tagging message)
    :returns: A string containing the full, dereferenced SCMURL for the build
    """

    logger.debug(f"Retrieving SCM URL for {build_id}")
    try:
        buildinfo = yield get_buildinfo("source", build_id)
    except Exception as e:
        logger.critical("Unexpected error retrieving SCM URL", exc_info=True)
        reactor.stop()
    logger.debug(f"Buildinfo: {buildinfo}")

    logger.debug(f"Retrieved SCM URL: {buildinfo['source']}")
    return buildinfo["source"]


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
        raise InfoUnavailableError(
            f"Could not retrieve information for build {build_id}"
        ) from e

    return buildinfo


@inlineCallbacks
def get_taskinfo(which_bsys, task_id, **kwargs):
    bsys = get_buildsys(which_bsys)

    try:
        taskinfo = yield deferToThread(bsys.getTaskInfo, task_id, **kwargs)
    except koji.GenericError as e:
        logger.exception(f"Could not retrieve information for task {task_id}")
        raise InfoUnavailableError(
            f"Could not retrieve information for build {task_id}"
        ) from e

    try:
        children = yield deferToThread(
            bsys.getTaskChildren, task_id, request=True, strict=True
        )
    except koji.GenericError as e:
        logger.exception(f"Could not retrieve child information for task {task_id}")
        raise InfoUnavailableError(
            f"Could not retrieve child information for build {task_id}"
        ) from e

    # Ensure we have the result for a buildSRPMFromSCM step so we can use that
    # in RebuildBatch._get_srpm_nvr_from_task_msg() to make sure we know the
    # proper NVR to tag.
    for child in children:
        if child["method"] == "buildSRPMFromSCM":
            child["result"] = yield deferToThread(bsys.getTaskResult(child["id"]))

    # Add the ["info"] key here to simulate the layout of a
    # state-change message from Koji.
    taskinfo["info"] = dict()
    taskinfo["info"]["request"] = taskinfo["request"]

    # Add the ["info"]["children"] keys here to simulate the layout of a
    # state-change message from Koji. This is needed later by
    # RebuildBatch._get_srpm_nvr_from_task_msg() so that we don't need to
    # differentiate the two ways we can determine task completion.
    taskinfo["info"]["children"] = children

    return taskinfo


@inlineCallbacks
def perform_builds(target, scm_urls, scratch=False):
    task_index = yield start_builds(target, scm_urls, scratch)
    results = yield wait_for_builds(task_index.values())
    return results


@inlineCallbacks
def start_builds(target, scm_urls, scratch=False):
    task_index = yield deferToThread(_start_builds_thread, target, scm_urls, scratch)
    return task_index


def _start_builds_thread(target, scm_urls, scratch=False):
    bsys = get_buildsys("destination")
    build_vcalls = dict()
    try:
        with bsys.multicall(batch=config.koji_batch) as mc:
            logger.debug(f"Starting {len(scm_urls)} tasks")
            for scmurl in scm_urls:
                if not config.is_eligible:
                    raise IneligibleBuildError(
                        f"{scmurl} is ineligible to be built for {target}"
                    )

                logger.debug(f"Building {scmurl}")
                build_vcalls[scmurl] = mc.build(
                    scmurl,
                    target,
                    {"scratch": scratch},
                    priority=KOJI_BACKGROUND_PRIORITY,
                )
    except Exception as e:
        logger.exception(e)
        raise

    task_index = dict()
    for scmurl, vcall in build_vcalls.items():
        task_id = vcall.result
        task_index[scmurl] = task_id
        logger.info(f"Building task {task_id} begun for {scmurl}.")

    return task_index


@inlineCallbacks
def wait_for_build(task_id):
    logger.debug(f"Waiting for {task_id}.")

    # Wait until this task is complete
    yield listener.register_build_task_id(task_id)


@inlineCallbacks
def wait_for_builds(task_ids):
    deferreds = list()

    for task_id in task_ids:
        logger.debug(f"Waiting for {task_id} to complete.")
        deferreds.append(listener.register_build_task_id(task_id))

    result = yield DeferredList(deferreds, consumeErrors=True)
    return result
