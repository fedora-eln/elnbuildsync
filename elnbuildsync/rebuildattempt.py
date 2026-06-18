# This file is part of ELNBuildSync
# Copyright (C) 2023-2026 Stephen Gallagher <sgallagh@redhat.com>

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

from . import kojihelpers
from . import db_models
from .decorators import as_deferred

logger = logging.getLogger(__name__)


class RebuildAttempt:
    def __init__(self, scm_urls, slice):
        self.koji_task_ids = []
        self.scm_urls = scm_urls
        self.slice = slice

        # DB Object
        self._db_obj = None

    async def async_init(self):
        # Kick off the builds and get their task IDs
        task_index = await kojihelpers.builds.start_builds(
            self.slice.rebuild_batch.side_tag,
            self.scm_urls,
            scratch=self.slice.rebuild_batch.scratch,
            fail_fast=self.slice.rebuild_batch.fail_fast,
        )
        self.koji_task_ids = list(task_index.values())

        # Create the RebuildAttempt in the database
        await self._async_db_init()

        return self

    @as_deferred
    async def _async_db_init(self):
        # Create the object in the database
        async with db_models.async_session() as session:
            db_attempt = db_models.DBRebuildAttempt(
                slice=self.slice._db_obj, completed=False
            )
            session.add(db_attempt)
            await session.commit()
            logger.debug(f"RebuildAttempt DB ID: {db_attempt.id}")
            self._db_obj = db_attempt

    async def async_await(self):
        successes = dict()
        failures = dict()

        results = await kojihelpers.builds.wait_for_tasks(self.koji_task_ids)
        for success, value in results:
            if success:
                successes[value["id"]] = value
            else:
                try:
                    try:
                        value.raiseException()
                    except Exception as e:
                        data = e.data

                    id = data["id"]
                    failures[id] = data
                except Exception:
                    logger.exception("Unexpected error while awaiting a task")
                    raise

        return (successes, failures)

    @as_deferred
    async def _async_db_finish(self):
        # Save this to the database here
        async with db_models.async_session() as session:
            self._db_obj.completed = True
            session.add(self._db_obj)
            await session.commit()
            logger.debug(f"Rebuild Attempt {self._db_obj.id}: COMPLETE")
