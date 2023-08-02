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


from fedora_messaging.message import Message as FedoraMessage
from twisted.internet.defer import inlineCallbacks

from .rebuildattempt import RebuildAttempt
from .tagmessage import TagMessage


class RebuildBatch:
    tag_messages = list()
    side_tag = None
    dest_tag = None
    finished = False
    attempts = list()

    # Database ID
    _rebuild_batch_id = 0

    def __init__(
        self, dest_tag: str, side_tag: str, fedora_tag_messages: list[FedoraMessage]
    ):
        """
        Do not call RebuildBatch() alone. Instantiate via
        `yield RebuildBatch(dest_tag, side_tag, msgs).async_init()` instead.
        This ensures that the database actions will settle before the object
        is used.
        """
        self.dest_tag = dest_tag
        self.side_tag = side_tag

        self._fedora_tag_messages = fedora_tag_messages

    @inlineCallbacks
    def async_init(self):
        for fedora_tag_message in fedora_tag_messages:
            yield self.add_tag_message(fedora_tag_message)

        # Create the RebuildBatch record in the database here.
        # self._rebuild_batch_id = ID from database
        pass

    @inlineCallbacks
    def add_tag_message(self, fedora_tag_message: FedoraMessage):
        # Create the new TagMessage (which also creates the DB object)
        message = yield TagMessage(
            fedora_tag_message, self._rebuild_batch_id
        ).async_init()

        # Add the tag_message object to this batch
        self.tag_messages.append(message)
