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
from .kojihelpers.builds import get_scmurl
from .rebuild_data import RebuildData

from fedora_messaging.exceptions import Nack, Drop
from twisted.internet import reactor
from twisted.internet.defer import (
    AlreadyCalledError,
    Deferred,
    inlineCallbacks,
    TimeoutError,
)


logger = logging.getLogger(__name__)


# A dictionary to keep track of builds in-progress
active_builds = dict()

def message_handler(msg):
    try:
        logger.debug(f"Received a message with topic {msg.topic}")

        # Listen for repositories we are waiting on.
        if msg.topic.endswith("buildsys.repo.init"):
            tag = msg.body["tag"]

            if tag in kojihelpers.awaiting_repo_init:
                logger.info(f"repo {tag} has started regenerating")
                for deferred in kojihelpers.awaiting_repo_init[tag]:
                    # Enqueue the callbacks onto the reactor so we aren't
                    # blocking handling new messages
                    reactor.callLater(0, fire_callback, deferred, tag)

                # Clear the awaited list
                del kojihelpers.awaiting_repo_init[tag]

            else:
                logger.debug(
                    f"Unknown repository tag {msg.body['tag']}, ignoring."
                )
                raise Drop()

        elif msg.topic.endswith("buildsys.repo.done"):
            tag = msg.body["tag"]
            if tag in kojihelpers.awaited_repos:
                logger.info(f"Repo {tag} has regenerated")
                for deferred in kojihelpers.awaited_repos[tag]:
                    # Enqueue the callbacks onto the reactor so we aren't
                    # blocking handling new messages
                    reactor.callLater(0, fire_callback, deferred, tag)

                # Clear the awaited list
                del kojihelpers.awaited_repos[tag]

            else:
                logger.debug(
                    f"Unknown repository tag {msg.body['tag']}, ignoring."
                )
                raise Drop()
        
        elif msg.topic.endswith("buildsys.task.state.change"):
            # Are we looking for this build?
            task_id = msg.body["id"]
            if task_id in active_builds:
                if msg.body["new"] in ("FREE", "OPEN", "ASSIGNED"):
                    logger.debug(f"Build {task_id} ({msg.body['info']['request']}) is {msg.body['new']}")
                    raise Drop()

                elif msg.body["new"] == "CLOSED":
                    # Successful build
                    logger.info(f"Build {task_id} ({msg.body['info']['request']}) completed successfully")
                    reactor.callLater(0, fire_callback, active_builds[task_id], msg.body)
                
                else:
                    # It either failed or was canceled. Call the errback
                    reactor.callLater(0, fire_errback, active_builds[task_id], msg.body)

                del active_builds[task_id]
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
            logger.debug(
                f"Message tag {tag} not configured as a trigger, ignoring."
            )
            raise Drop()

        # Check whether this component is meaningful to us
        if not config.is_eligible("rpms", msg.body["name"]):
            raise Drop()

        # This is a component we care about, so process it in the main reactor thread
        # to avoid blocking new messages
        
        # TODO
        # reactor.callLater(0, rebuild_tagged_component, msg)

    except Drop as e:
        # Tell the AMQP server that we're ignoring this message
        raise

    except Exception as e:
        logger.exception(e)
        # If anything goes wrong during the message handler, Nack() the
        # message so it will get retried.
        raise Nack("Unexpected error, will retry") from e


def fire_callback(deferred, data):
    try:
        deferred.callback(data)
    except AlreadyCalledError as e:
        # Most likely due to a timeout, so ignore it
        logger.exception(e)
        pass


def fire_errback(deferred, data):
    try:
        deferred.errback(data)
    except AlreadyCalledError as e:
        # Most likely due to a timeout, so ignore it
        logger.exception(e)
        pass


@inlineCallbacks
def rebuild_tagged_component(msg):
    comp = msg.body["name"]
    version = msg.body["version"]
    release = msg.body["release"]
    target = config.main["build"]["target"]

    scmurl = yield get_scmurl(msg.body["task_id"])
    rd = RebuildData("rpms", comp, version, release, scmurl, target)

    batching.batch_processor.reset()
    batching.message_queue.put(rd)


def register_build_task_id(task_id):
    active_builds[task_id] = Deferred()
    return active_builds[task_id]