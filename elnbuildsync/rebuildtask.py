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

from . import db_models
from .utils import as_deferred

logger = logging.getLogger(__name__)


class RebuildTask:
    def __init__(self, koji_task_id, rebuild_attempt):
        self.result = None
        self.koji_task_id = koji_task_id
        self.rebuild_attempt = rebuild_attempt

        # DB ID
        # This may only be accessed within a
        # async with db_models.async_session() as session:
        # context manager.
        self._db_obj = None

        logger.debug(f"Created RebuildTask for task_id {koji_task_id}")

    async def async_init(self):
        await as_deferred(self._async_db_init())

        return self

    async def _async_db_init(self):
        """
        This function returns a coroutine and must be wrapped by
        `as_deferred()` to be used in a Twisted async function.
        """
        # Create the object in the database
        async with db_models.async_session() as session:
            db_task = db_models.DBRebuildTask(
                koji_task_id=self.koji_task_id,
                attempt=self.rebuild_attempt._db_obj,
            )
            session.add(db_task)
            await session.commit()
            logger.debug(f"RebuildTask DB ID: {db_task.id}")
            self._db_obj = db_task

    async def finish(self, state):

        self.result = state
        await as_deferred(self._async_db_finish())

    async def _async_db_finish(self):
        # Save this to the database here
        async with db_models.async_session() as session:
            self._db_obj.koji_task_result = self.result
            session.add(self._db_obj)
            await session.commit()
            logger.debug(f"Final task result: {self._db_obj.koji_task_result}")
