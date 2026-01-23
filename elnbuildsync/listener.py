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
from . import batching
from . import config

from . import kojihelpers
from .state import ELNBuildSyncState as state

from fedora_messaging.exceptions import Nack, Drop
from twisted.internet import reactor
from twisted.internet.defer import AlreadyCalledError, Deferred
from twisted.internet.defer import TimeoutError as DeferredTimeoutError


logger = logging.getLogger(__name__)

task_check_processor = None


def _handle_repo_init(msg):
    """Handle buildsys.repo.init messages for repositories we are waiting on."""
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


def _handle_repo_done(msg):
    """Handle buildsys.repo.done messages for repositories we are waiting on."""
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


def _handle_task_state_change(msg):
    """Handle buildsys.task.state.change messages for tasks we are tracking."""
    task_id = msg.body["id"]

    if task_id in state.active_tasks:
        if msg.body["new"] in ("FREE", "OPEN", "ASSIGNED"):
            logger.debug(
                f"Task {task_id} ({msg.body['info']['request']}) is {msg.body['new']}"
            )
            raise Drop()

        elif msg.body["new"] == "CLOSED":
            # Successful build
            logger.info(
                f"Task {task_id} ({msg.body['info']['request']}) completed successfully"
            )
            reactor.callLater(0, fire_callback, state.active_tasks[task_id], msg.body)

        else:
            # It either failed or was canceled. Call the errback
            logger.info(f"Task {task_id} failed.")
            reactor.callLater(0, fire_errback, state.active_tasks[task_id], msg.body)

        del state.active_tasks[task_id]
        return

    else:
        # Ignore messages from unrelated builds
        logger.debug(f"Unknown task_id {task_id}. Ignoring.")
        raise Drop()


def _handle_tag(msg):
    """Handle buildsys.tag messages to trigger rebuilds."""
    tag = msg.body["tag"]

    if tag == config.main["trigger"]["rpms"]:
        return _handle_trigger_tag(msg)

    elif tag in state.pending_nvr_tags.keys():
        return _handle_awaited_tag(msg)

    logger.debug(f"Message tag {tag} not configured as a trigger, ignoring.")
    raise Drop()


def _handle_trigger_tag(msg):
    # Check whether this component is meaningful to us
    if not config.is_eligible("rpms", msg.body["name"]):
        raise Drop()

    # If we are currently processing a batch or are in a "paused" state,
    # Nack() the message so it will stay in the queue and not get lost if
    # we crash/restart.
    if batching.running or config.is_paused():
        raise Nack()

    logger.info(f"Triggering rebuild on trigger tag {config.main['trigger']['rpms']}")

    # This is a component we care about, so add it to the queue
    batching.message_batch_processor.reset()

    # TODO: We also need to save the list of pending messages to the DB
    # so they aren't lost if we restart. It's okay to block this thread
    # for this purpose.
    logger.debug(f"Adding {msg.body['name']} to the next batch.")
    batching.message_queue.put(msg)


def _handle_awaited_tag(msg):
    """Handle buildsys.tag messages to trigger rebuilds."""
    tag = msg.body["tag"]

    nvr = f"{msg.body['name']}-{msg.body['version']}-{msg.body['release']}"

    try:
        deferred = state.pending_nvr_tags.pop(tag, nvr)
        reactor.callLater(0, fire_callback, deferred, nvr)
    except KeyError as e:
        logger.debug(f"NVR {nvr} not found in tag {tag}, ignoring.")
        raise Drop()


def message_handler(msg):
    logger.debug(f"Received {msg.topic}: UUID {msg.id}")
    try:
        if msg.topic.endswith("buildsys.repo.init"):
            _handle_repo_init(msg)

        elif msg.topic.endswith("buildsys.repo.done"):
            _handle_repo_done(msg)

        elif msg.topic.endswith("buildsys.task.state.change"):
            _handle_task_state_change(msg)

        elif msg.topic.endswith("buildsys.tag"):
            _handle_tag(msg)

        else:
            # Ignore any unhandled message topics
            logger.debug(f"Unable to handle {msg.topic} topics, ignoring.")
            raise Drop()

    except Drop as e:
        # Tell the AMQP server that we're ignoring this message
        logger.debug(f"Dropped message {msg.id}")
        raise

    except Nack as e:
        # We're explicitly informing the AMQP server that we can't handle
        # this request currently and it should be re-queued.
        logger.debug(f"Re-queued message {msg.id}")
        raise

    except Exception as e:
        logger.exception(e)
        # If anything goes wrong during the message handler, Nack() the
        # message so it will get retried.
        raise Nack(f"Unexpected error on message {msg.id}, will retry") from e


async def check_tasks():
    remove_tasks = list()
    for task in state.active_tasks.keys():
        try:
            taskinfo = await kojihelpers.builds.get_taskinfo(
                "destination", task, request=True
            )

            if taskinfo["state"] == koji.TASK_STATES["CLOSED"]:
                # Task is finished.
                logger.info(
                    f"Task {task} ({taskinfo['request'][0]}) completed successfully"
                )
                reactor.callLater(0, fire_callback, state.active_tasks[task], taskinfo)
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
                logger.info(f"Task {task} failed.")
                reactor.callLater(0, fire_errback, state.active_tasks[task], taskinfo)
                # Stop watching this task ID
                remove_tasks.append(task)
        except Exception as e:
            # Log any failures so we don't block future checks.
            logger.critical(f"Unexpected failure in task {task}")
            logger.exception(e)

            # Call cancel() on the Deferred before we remove it
            reactor.callLater(0, state.active_tasks[task].cancel)

            # Stop tracking this task so we don't continue failing on it
            remove_tasks.append(task)

    for task in remove_tasks:
        del state.active_tasks[task]


def fire_callback(deferred, *data):
    try:
        deferred.callback(*data)
    except AlreadyCalledError as e:
        # Most likely due to a timeout, so ignore it
        logger.exception(e)
        pass


def fire_errback(deferred, *data):
    try:
        deferred.errback(kojihelpers.errors.TaskFailedError(*data))
    except AlreadyCalledError as e:
        # Most likely due to a timeout, so ignore it
        logger.exception(e)
        pass


def register_task_id(task_id, timeout=config.task_timeout):
    logger.debug(f"Registering task {task_id}")
    if task_id in state.active_tasks:
        raise ValueError("Cannot register the same task ID twice")

    state.active_tasks[task_id] = Deferred()
    state.active_tasks[task_id].addTimeout(timeout, reactor)
    state.active_tasks[task_id].addErrback(cancel_timed_out_task, task_id)

    return state.active_tasks[task_id]


def register_nvr_tag(
    tag: str, nvr: str, timeout: float = config.tag_timeout
) -> Deferred:
    """
    Register an NVR to watch for appearance in a specific tag.

    Creates a Deferred that will be called when the NVR appears in the tag.

    Args:
        tag: The tag name to watch
        nvr: The NVR to wait for
        timeout: Timeout in seconds (defaults to config.task_timeout)

    Returns:
        A Deferred that will be called when the NVR appears in the tag
    """
    logger.debug(f"Registering NVR {nvr} for tag {tag}")

    deferred = Deferred()
    deferred.addTimeout(timeout, reactor)

    state.pending_nvr_tags.push(tag, nvr, deferred)

    return deferred


def cancel_timed_out_task(failure, task_id):
    # Reraise the original exception, catching TimeoutError if it happened
    try:
        failure.raiseException()
    except DeferredTimeoutError as e:
        pass

    # If we got a timeout, the Koji task is still running, so we will need to
    # cancel it. Do this asynchronously so we don't block on it. It's
    # technically possible that the cancelation might fail, but there's
    # nothing we can do to recover from that anyway.
    reactor.callLater(0, _do_cancelation, task_id)

    # Raise a TaskTimeoutError with the task_id
    raise kojihelpers.errors.TaskTimeoutError({"id": task_id})


def _do_cancelation(task_id):
    return Deferred.fromCoroutine(kojihelpers.builds.cancel_task(task_id))
