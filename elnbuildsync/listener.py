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
import txredisapi as redis

from . import config

from . import kojihelpers
from .kojihelpers import tags

logger = logging.getLogger(__name__)

from fedora_messaging.exceptions import Nack, Drop
from twisted.internet.defer import (
    AlreadyCalledError,
    inlineCallbacks,
    TimeoutError,
)


def message_handler(msg):
    try:
        logger.debug(f"Received a message with topic {msg.topic}")

        # Listen for repositories we are waiting on.
        if msg.topic.endswith("buildsys.repo.init"):
            tag = msg.body["tag"]

            if tag in kojihelpers.awaiting_repo_init:
                logger.info(f"repo {tag} has started regenerating")
                for deferred in kojihelpers.awaiting_repo_init[tag]:
                    tags._wait_repo_done(tag, deferred)

                # Clear the awaited list
                del kojihelpers.awaiting_repo_init[tag]

            else:
                logger.debug(f"Unknown repository tag {msg.body['tag']}, ignoring.")
                raise Drop()

        elif msg.topic.endswith("buildsys.repo.done"):
            tag = msg.body["tag"]
            if tag in kojihelpers.awaited_repos:
                logger.info(f"Repo {tag} has regenerated")
                for deferred in kojihelpers.awaited_repos[tag]:
                    try:
                        deferred.callback(None)
                    except AlreadyCalledError:
                        # Most likely due to a timeout, so ignore it
                        pass
                # Clear the awaited list
                del kojihelpers.awaited_repos[tag]
            else:
                logger.debug(f"Unknown repository tag {msg.body['tag']}, ignoring.")
                raise Drop()

        if not msg.topic.endswith("buildsys.tag"):
            # Ignore any non-tagging messages
            logger.debug(f"Unable to handle {msg.topic} topics, ignoring.")
            raise Drop()

    except Drop as e:
        # Tell the AMQP server that we're ignoring this message
        raise

    except Exception as e:
        logger.exception(e)
        # If anything goes wrong during the message handler, Nack the message
        # so it will get retried.
        raise Nack('Unexpected error, will retry') from e
