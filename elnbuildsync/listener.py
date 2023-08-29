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
import txredisapi as redis

from . import batching
from . import config

from . import kojihelpers
from .rebuild_data import RebuildData
from .state import ELNBuildSyncState as state

from fedora_messaging.exceptions import Nack, Drop
from twisted.internet import reactor
from twisted.internet.defer import (
    AlreadyCalledError,
    Deferred,
    inlineCallbacks,
    TimeoutError,
)


logger = logging.getLogger(__name__)

task_check_processor = None


def message_handler(msg):
    try:
        # Listen for repositories we are waiting on.
        if msg.topic.endswith("buildsys.repo.init"):
            tag = msg.body["tag"]

            if tag in kojihelpers.awaiting_repo_init:
                logger.info(f"repo {tag} has started regenerating")
                for deferred in kojihelpers.awaiting_repo_init[tag]:
                    # Enqueue the callbacks onto the reactor so we aren't
                    # blocking handling new messages
                    reactor.callLater(0, fire_callback, deferred, tag)

                # Remove it from the awaited list
                del kojihelpers.awaiting_repo_init[tag]
                return

            else:
                logger.debug(f"Unknown repository tag {msg.body['tag']}, ignoring.")
                raise Drop()

        elif msg.topic.endswith("buildsys.repo.done"):
            tag = msg.body["tag"]
            if tag in kojihelpers.awaited_repos:
                logger.info(f"Repo {tag} has regenerated")
                for deferred in kojihelpers.awaited_repos[tag]:
                    # Enqueue the callbacks onto the reactor so we aren't
                    # blocking handling new messages
                    reactor.callLater(0, fire_callback, deferred, tag)

                # Remove it from the awaited list
                del kojihelpers.awaited_repos[tag]
                return

            else:
                logger.debug(f"Unknown repository tag {msg.body['tag']}, ignoring.")
                raise Drop()

        elif msg.topic.endswith("buildsys.task.state.change"):
            # Are we looking for this build?
            task_id = msg.body["id"]
            if task_id in state.active_builds:
                if msg.body["new"] in ("FREE", "OPEN", "ASSIGNED"):
                    logger.debug(
                        f"Build {task_id} ({msg.body['info']['request']}) is {msg.body['new']}"
                    )
                    raise Drop()

                elif msg.body["new"] == "CLOSED":
                    # Successful build
                    logger.info(
                        f"Build {task_id} ({msg.body['info']['request']}) completed successfully"
                    )
                    reactor.callLater(
                        0, fire_callback, state.active_builds[task_id], msg.body
                    )

                else:
                    # It either failed or was canceled. Call the errback
                    logger.info(f"Build {task_id} failed.")
                    reactor.callLater(
                        0, fire_errback, state.active_builds[task_id], msg.body
                    )

                del state.active_builds[task_id]
                return

            else:
                # Ignore messages from unrelated builds
                logger.debug(f"Unknown task_id {task_id}. Ignoring.")
                raise Drop()

        if not msg.topic.endswith("buildsys.tag"):
            # Ignore any non-tagging messages
            logger.debug(f"Unable to handle {msg.topic} topics, ignoring.")
            raise Drop()

        tag = msg.body["tag"]
        if tag != config.main["trigger"]["rpms"]:
            logger.debug(f"Message tag {tag} not configured as a trigger, ignoring.")
            raise Drop()

        # Check whether this component is meaningful to us
        if not config.is_eligible("rpms", msg.body["name"]):
            raise Drop()

        # If we are currently processing a batch, Nack() the message so it
        # will stay in the queue and not get lost if we crash/restart.
        if batching.running:
            raise Nack()

        logger.info(f"Triggering rebuild on tag {tag}")

        # This is a component we care about, so add it to the queue
        batching.message_batch_processor.reset()

        # TODO: We also need to save the list of pending messages to the DB
        # so they aren't lost if we restart. It's okay to block this thread
        # for this purpose.
        logger.debug(f"Adding {msg.body['name']} to the next batch.")
        batching.message_queue.put(msg)

    except Drop as e:
        # Tell the AMQP server that we're ignoring this message
        raise

    except Nack as e:
        # We're explicitly informing the AMQP server that we can't handle
        # this request currently and it should be re-queued.
        raise

    except Exception as e:
        logger.exception(e)
        # If anything goes wrong during the message handler, Nack() the
        # message so it will get retried.
        raise Nack("Unexpected error, will retry") from e


@inlineCallbacks
def check_tasks():
    remove_tasks = list()
    for task in state.active_builds.keys():
        try:
            taskinfo = yield kojihelpers.builds.get_taskinfo(
                "destination", task, request=True
            )

            if taskinfo["state"] == koji.TASK_STATES["CLOSED"]:
                # Task is finished.
                logger.info(
                    f"Build {task} ({taskinfo['request'][0]}) completed successfully"
                )
                reactor.callLater(0, fire_callback, state.active_builds[task], taskinfo)
                # Stop watching this task ID
                remove_tasks.append(task)

            elif taskinfo["state"] in (
                koji.TASK_STATES["FREE"],
                koji.TASK_STATES["OPEN"],
                koji.TASK_STATES["ASSIGNED"],
            ):
                # Still processing; ignore it
                continue

            else:
                # It either failed or was canceled. Call the errback
                logger.info(f"Build task {task} failed.")
                reactor.callLater(0, fire_errback, state.active_builds[task], taskinfo)
                # Stop watching this task ID
                remove_tasks.append(task)
        except Exception as e:
            # Log any failures so we don't block future checks.
            logger.exception(e)

            # Delete this task so we don't continue failing on it
            remove_tasks.append(task)

    for task in remove_tasks:
        del state.active_builds[task]


def fire_callback(deferred, *data):
    try:
        deferred.callback(*data)
    except AlreadyCalledError as e:
        # Most likely due to a timeout, so ignore it
        logger.exception(e)
        pass


def fire_errback(deferred, *data):
    try:
        deferred.errback(kojihelpers.errors.BuildFailedError(*data))
    except AlreadyCalledError as e:
        # Most likely due to a timeout, so ignore it
        logger.exception(e)
        pass


def register_build_task_id(task_id):
    logger.debug(f"Registering task {task_id}")
    if task_id in state.active_builds:
        raise ValueError("Cannot register the same task ID twice")

    state.active_builds[task_id] = Deferred()
    return state.active_builds[task_id]
