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


import ast
import koji
import logging

from twisted.internet.defer import inlineCallbacks
from twisted.internet.threads import deferToThread

from . import kojihelpers
from .rebuildtask import RebuildTask


logger = logging.getLogger(__name__)


class RebuildAttempt:
    # Temporary internal class variable to store the latest attempt ID
    # Remove this once we are getting this from the DB
    _latest_attempt_id = 0

    def __init__(self, scm_urls, rebuild_batch):
        self.tasks = dict()
        self.scm_urls = scm_urls
        self._rebuild_batch = rebuild_batch

        # DB ID
        self._rebuild_attempt_id = 0

    @inlineCallbacks
    def async_init(self):
        # Kick off the builds and get their task IDs
        task_index = yield kojihelpers.builds.start_builds(
            self._rebuild_batch.side_tag,
            self.scm_urls,
            scratch=self._rebuild_batch.scratch,
        )
        tasks = task_index.values()

        # TODO: Create the RebuildAttempt in the database here

        # Create the associated RebuildTask objects
        for task in tasks:
            yield self.add_task(task)

        # TODO: get this from the DB
        self._rebuild_attempt_id = self._latest_attempt_id
        self._latest_attempt_id += 1

        return self

    @inlineCallbacks
    def add_task(self, task):
        if task in self.tasks:
            raise ValueError("You may only register the same task_id once")

        try:
            rtask = yield RebuildTask(task, self).async_init()
        except Exception as e:
            logger.critical(f"Failed to create RebuildTask", exc_info=True)
            raise

        self.tasks[task] = rtask

    @inlineCallbacks
    def async_await(self):
        successes = dict()
        failures = dict()

        task_ids = [task.koji_task_id for task in self.tasks.values()]
        results = yield kojihelpers.builds.wait_for_builds(task_ids)
        for success, value in results:
            if success:
                successes[value["id"]] = value

                # TODO: Get the build_id here by parsing the result
                # section of a child task of type 'builSRPMfromSCM'
                # It will have the form:
                # "result": {
                #   "srpm": "tasks/7581/104277581/fedora-release-39-0.22.eln128.src.rpm",
                #  ...
                # },

                # Store the results in the DB
                yield self.tasks[value["id"]].finish(koji.TASK_STATES["CLOSED"])

            else:
                try:
                    err_msg = value.getErrorMessage()
                    logger.debug(f"Rebuild failure: {err_msg}")

                    err_obj = ast.literal_eval(err_msg)
                    failures[err_obj["id"]] = err_obj

                    # Store the results in the DB
                    yield self.tasks[err_obj["id"]].finish(koji.TASK_STATES["FAILED"])
                except Exception as e:
                    logger.exception(e)
                    raise

        return (successes, failures)
