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

from fedora_messaging.message import Message
from twisted.internet.threads import deferToThread

from .kojihelpers.builds import get_scmurl


logger = logging.getLogger(__name__)


class TagMessage:
    # Tag JSON samples:
    # https://apps.fedoraproject.org/datagrepper/v2/search?topic=org.fedoraproject.prod.buildsys.tag

    def __init__(self, tag_message: Message) -> None:
        """
        Do not call TagMessage() alone. Instantiate via
        `await TagMessage(msg, batch_id).async_init()` instead. This ensures
        that the database actions will settle before the object is used.
        """
        self.component = tag_message.body["name"]
        self.scmurl = None
        self._message = tag_message

        # Database IDs
        self._tag_message_id = 0
        self._rebuild_batch_id = 0

    async def async_init(self):
        try:
            logger.debug(f"Getting SCM URL for {self._message.body['build_id']}")
            self.scmurl = await get_scmurl(self._message.body["build_id"])
        except Exception as e:
            logger.exception(e)
            raise

        logger.debug(f"Got {self.scmurl}")

        # TODO: Create the TagMessage record in the database here
        return self

    async def assign_to_rebuildbatch(self, rebuild_batch_id: int) -> None:
        self._rebuild_batch_id = rebuild_batch_id

        # TODO: Update DB entry with batch ID
        # This is a placeholder to ensure we return a Deferred
        await deferToThread(TagMessage._simple_await)

    async def drop(self):
        # Remove this entry from the database. It will not be processed.

        # TODO: actually remove it from the DB
        # This is a placeholder to ensure we return a Deferred
        await deferToThread(TagMessage._simple_await)

    @staticmethod
    def _simple_await():
        pass

    def get_build_id(self):
        return self._message.body["build_id"]
