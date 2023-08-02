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

from fedora_messaging.message import Message as FedoraMessage
from twisted.internet.defer import inlineCallbacks, TimeoutError

from .rebuildattempt import RebuildAttempt
from .tagmessage import TagMessage

from . import kojihelpers


logger = logging.getLogger(__name__)


# Temporary internal variable to store the latest batch ID
# Remove this once we are getting this from the DB
_latest_batch_id = 0


class RebuildBatch:
    tag_messages = list()
    side_tag = None
    dest_tag = None
    finished = False

    _active_attempt = None

    # Database ID
    _rebuild_batch_id = 0

    def __init__(self, dest_tag: str, fedora_tag_messages: list[FedoraMessage]):
        """
        Do not call RebuildBatch() alone. Instantiate via
        `yield RebuildBatch(dest_tag, msgs).async_init()` instead.
        This ensures that the database actions will settle before the object
        is used.
        """
        self.dest_tag = dest_tag
        self._base_tag = f"{dest_tag}-build"
        self._fedora_tag_messages = fedora_tag_messages

    @inlineCallbacks
    def async_init(self):
        global latest_batch_id

        for fedora_tag_message in self._fedora_tag_messages:
            yield self.add_tag_message(fedora_tag_message)

        # Create the side-tag for this batch
        while True:
            try:
                self.side_tag = yield kojihelpers.tags.prepare_side_tag(self._base_tag)
            except TimeoutError as e:
                # Keep retrying to create a side-tag.
                # Any other exception will be propagated up the stack.
                continue

            # Side-tag is ready. Proceed.
            break

        # Create the RebuildBatch record in the database here.
        # self._rebuild_batch_id = ID from database

        # TODO: get this from the DB
        self._rebuild_batch_id = _latest_batch_id
        _latest_batch_id += 1

        return self

    @inlineCallbacks
    def add_tag_message(self, fedora_tag_message: FedoraMessage):
        # Create the new TagMessage (which also creates the DB object)
        message = yield TagMessage(
            fedora_tag_message, self._rebuild_batch_id
        ).async_init()

        # Add the tag_message object to this batch
        self.tag_messages.append(message)

    @inlineCallbacks
    def run(self):
        # Create a RebuildAttempt
        # The initial one contains the complete set of components from the tag_messages

        # Get the list of SCM URLs
        scm_urls = list()
        for tag_message in self.tag_messages:
            scm_urls.append(tag_message.scmurl)

        # Kick off the builds and get their task IDs
        # TODO: get scratch value from config
        tasks = kojihelpers.builds.start_builds(
            self.side_tag, scm_urls, scratch=True
        ).values()

        self._active_attempt = yield RebuildAttempt(
            tasks, self._rebuild_batch_id
        ).async_init()
