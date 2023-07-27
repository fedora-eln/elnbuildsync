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


from fedora_messaging.message import Message
from twisted.internet.defer import Deferred, inlineCallbacks

from .kojihelpers.builds import get_scmurl


class TagMessage:
    # Tag JSON samples:
    # https://apps.fedoraproject.org/datagrepper/v2/search?topic=org.fedoraproject.prod.buildsys.tag

    component = None
    scmurl = None

    # Database IDs
    _tag_message_id = 0
    _rebuild_batch_id = 0

    def __init__(self, tag_message: Message, rebuild_batch_id: int) -> None:
        """
        Do not call TagMessage() alone. Instantiate via
        `yield TagMessage(msg, batch_id).async_init()` instead. This ensures
        that the database actions will settle before the object is used.
        """
        self.component = tag_message.body["name"]
        self._rebuild_batch_id = rebuild_batch_id
        self._json_message = tag_message


    @inlineCallbacks
    def async_init(self):
        self.scmurl = yield get_scmurl(self._json_message.body["build_id"])

        # Create the TagMessage record in the database here
        pass