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


from enum import Enum
from queue import Queue, Empty
from fedora_messaging.message import Message
from twisted.internet.defer import inlineCallbacks

from . import config
from .rebuildbatch import RebuildBatch

message_queue = Queue()
message_batch_timer = 5
message_batch_processor = None


@inlineCallbacks
def process_message_batch():
    fedora_tag_messages = list()
    while True:
        try:
            tag_message = message_queue.get_nowait()
            fedora_tag_messages.append(tag_message)
        except Empty as e:
            break

    if not fedora_tag_messages:
        # Nothing to do here
        return

    # Create Batch object
    batch = yield RebuildBatch(target=config.main["build"]["target"], fedora_tag_messages=fedora_tag_messages, scratch=config.main["build"]["scratch"]).async_init()

    # Run the batch.
    # IMPORTANT: this must complete before other batches are started. This
    # may take a long time and will introduce lag in how quickly packages
    # are processed through.
    yield batch.run()
