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


from enum import Enum
from queue import Queue, Empty
from fedora_messaging.message import Message
from twisted.internet.defer import Deferred


message_queue = Queue()
message_batch_timer = 5
message_batch_processor = None

rebuild_batch_queue = Queue()
build_batch_timer = 5
build_batch_processor = None


class BuildTask:
    def __init__(self):
        pass


class BuildAttempt:
    def __init__(self, tasks):
        pass


def process_message_batch():
    builds = list()
    while True:
        try:
            rd = message_queue.get_nowait()
            builds.append(rd)
        except Empty as e:
            break

    if not builds:
        # Nothing to do here
        return

    # Create Batch object


def process_build_batch(builds):
    # Wait for the queue to become available
    yield wait_for_build_batch(build_batch_queue)
    pass
