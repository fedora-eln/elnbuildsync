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

from enum import Enum
from queue import Queue, Empty

from fedora_messaging.message import Message as FedoraMessage

from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread

from . import config
from . import kojihelpers
from .tagmessage import TagMessage
from .rebuildbatch import RebuildBatch


message_queue = Queue()
message_batch_timer = 5
message_batch_processor = None

running = False

logger = logging.getLogger(__name__)


@inlineCallbacks
def process_message_batch():
    global running
    tag_messages = list()
    while True:
        try:
            fedora_tag_message = message_queue.get_nowait()

            tag_message = yield TagMessage(fedora_tag_message).async_init()

            tag_messages.append(tag_message)
        except Empty as e:
            break

    if not tag_messages:
        # Nothing to do here
        return

    # Create Batch object
    batch = yield RebuildBatch(
        target=config.main["build"]["target"],
        tag_messages=tag_messages,
        scratch=config.main["build"]["scratch"],
    ).async_init()

    # Run the batch.
    # IMPORTANT: this must complete before other batches are started. A large
    # number of packages may queue up in this time, but they will be processed
    # as a single batch.
    try:
        running = True
        yield batch.run()
    except Exception as e:
        # If something goes unrecoverably wrong here, always log it and skip
        # to the next batch.
        logger.exception(e)
    finally:
        running = False


@inlineCallbacks
def rebuild_from_components(components):
    global message_batch_processor
    global message_queue

    # Fake up a TagMessage for each of these to enqueue into the next batch
    bsys = kojihelpers.connection.get_buildsys(
        kojihelpers.connection.BuildSystemType.source
    )

    src_tag = config.main["trigger"]["rpms"]
    latest_tagged_src_pkgs = yield deferToThread(
        bsys.listTagged, src_tag, latest=True, inherit=True
    )
    latest_tagged_src_table = {pkg["name"]: pkg for pkg in latest_tagged_src_pkgs}

    for component in components:
        if config.is_eligible("rpms", component):
            if component in latest_tagged_src_table:
                # Fake up a FedoraMessage for the batching system
                msg = FedoraMessage(
                    topic="org.fedoraproject.prod.buildsys.tag",
                    body={
                        "name": component,
                        "version": latest_tagged_src_table[component]["version"],
                        "release": latest_tagged_src_table[component]["release"],
                        "nvr": latest_tagged_src_table[component]["nvr"],
                        "build_id": latest_tagged_src_table[component]["build_id"],
                        "tag": src_tag,
                        "ELNBuildSync_notes": "Fake message for building missing packages",
                    },
                )
                logger.info(
                    f"Rebuilding {latest_tagged_src_table[component]} for ELN."
                )

                message_batch_processor.reset()
                message_queue.put(msg)
            else:
                logger.critical(
                    f"Package {component} has never been built for {src_tag}"
                )
