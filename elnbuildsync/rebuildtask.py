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

from . import config
from . import kojihelpers

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, Deferred
from twisted.internet.threads import deferToThread


logger = logging.getLogger(__name__)


class RebuildTask:
    def __init__(self, koji_task_id, rebuild_attempt):
        self.result = None
        self.koji_task_id = koji_task_id
        self._rebuild_attempt = rebuild_attempt
        self.deferred = Deferred()

        # Set up a long timeout, which we will cancel later
        # if the build succeeds or fails normally.
        self.timeout = reactor.callLater(config.task_timeout, self._timeout())

        # DB IDs
        self._rebuild_task_id = 0

        logger.debug(f"Created RebuildTask for task_id {koji_task_id}")

    @inlineCallbacks
    def async_init(self):
        # TODO remove this; it's just to ensure we have an async generator
        # until the DB interaction is available.
        yield deferToThread(RebuildTask._simple_yield)

        # Save this to the database here
        return self

    @inlineCallbacks
    def finish(self, state):
        # TODO remove this; it's just to ensure we have an async generator
        # until the DB interaction is available.
        yield deferToThread(RebuildTask._simple_yield)

        # Cancel the timeout
        if self.timeout:
            self.timeout.cancel()
            self.timeout = None

        self.result = state
        # Save this to the database here

    def _timeout(self):
        # Construct a fake message for the error
        msg = dict()
        msg["id"] = self.koji_task_id

        # TODO: put something useful here.
        msg["srpm"] = ""

        # Call the errback with a TimeOutError
        self.deferred.errback(kojihelpers.errors.BuildTimeoutError(msg))

    @staticmethod
    def _simple_yield():
        pass
