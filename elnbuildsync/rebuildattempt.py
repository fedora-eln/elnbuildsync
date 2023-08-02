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


from twisted.internet.defer import inlineCallbacks

from .rebuildtask import RebuildTask


# Temporary internal variable to store the latest attempt ID
# Remove this once we are getting this from the DB
_latest_attempt_id = 0


class RebuildAttempt:
    tasks = list()

    _unregistered_tasks = list()

    # DB IDs
    _rebuild_attempt_id = 0
    _rebuild_batch_id = 0

    def __init__(self, tasks, rebuild_batch_id):
        self.tasks = tasks
        self._rebuild_batch_id = rebuild_batch_id
        self._unregistered_tasks = tasks

    @inlineCallbacks
    def async_init(self):
        global latest_attempt_id

        for task in self._unregistered_tasks:
            yield self.add_task(task)

        # TODO: Create the RebuildAttempt in the database here

        # TODO: get this from the DB
        self._rebuild_attempt_id = _latest_attempt_id
        _latest_attempt_id += 1

        return self

    @inlineCallbacks
    def add_task(self, task):
        rtask = yield RebuildTask(task, self._rebuild_attempt_id).async_init()
        self.tasks.append(rtask)
