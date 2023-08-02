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


from twisted.internet.defer import Deferred, inlineCallbacks

from .kojihelpers import builds


class RebuildTask:
    result = None
    koji_task_id = 0

    # DB IDs
    _rebuild_attempt_id = 0

    def __init__(self, koji_task_id, rebuild_attempt):
        self.koji_task_id = koji_task_id
        self._rebuild_attempt_id = rebuild_attempt

    @inlineCallbacks
    def async_init(self):
        # Save this to the database here
        return self

    @inlineCallbacks
    def async_await(self):
        yield builds.wait_for_build(self.koji_task_id)
