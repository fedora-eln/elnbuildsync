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
from twisted.internet.threads import deferToThread

from elnbuildsync import db_models
from elnbuildsync.utils import as_deferred

from .kojihelpers.builds import get_scmurl


logger = logging.getLogger(__name__)


class TagMessage:
    # Tag JSON samples:
    # https://apps.fedoraproject.org/datagrepper/v2/search?topic=org.fedoraproject.prod.buildsys.tag

    def __init__(self, tag_message: FedoraMessage) -> None:
        """
        Do not call TagMessage() alone. Instantiate via
        `await TagMessage(msg, batch_id).async_init()` instead. This ensures
        that the database actions will settle before the object is used.
        """
        self.component = tag_message.body["name"]
        self.scmurl = None
        self._message = tag_message

        # Database object
        self._db_obj = None

    async def async_init(self):
        try:
            logger.debug(f"Getting SCM URL for {self._message.body['build_id']}")
            self.scmurl = await get_scmurl(self._message.body["build_id"])
        except Exception as e:
            logger.exception(e)
            raise

        logger.debug(f"Got {self.scmurl}")

        # Create the TagMessage record in the database here
        await as_deferred(self._async_db_init())

        return self

    async def _async_db_init(self):
        """
        This function returns a coroutine and must be wrapped by
        `as_deferred()` to be used in a Twisted async function.
        """
        async with db_models.async_session() as session:
            db_tag_msg = db_models.DBTagMessage(
                component=self.component,
                scmurl=self.scmurl,
                raw=str(self._message),
            )
            session.add(db_tag_msg)
            await session.commit()
            logger.debug(f"TagMessage DB ID: {db_tag_msg.id}")
            self._db_obj = db_tag_msg

    async def drop(self):
        """
        This function returns a coroutine and must be wrapped by
        `as_deferred()` to be used in a Twisted async function.
        """
        async with db_models.async_session() as session:
            session.delete(self._db_obj)
            await session.commit()

    def get_build_id(self):
        return self._message.body["build_id"]
