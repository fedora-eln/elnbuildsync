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

from collections.abc import Generator
from typing import ClassVar

from twisted.internet.defer import Deferred


class PendingNVRTags:
    """
    A data structure to track NVRs waiting to appear in specific tags.

    Maps tag names to NVRs, where each tag+NVR combination is associated
    with a Deferred object that will be called when the NVR appears in the tag.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Deferred]] = {}

    def __contains__(self, tag: object) -> bool:
        return tag in self._data

    def keys(self) -> list[str]:
        """
        Return a list of all tag names.
        """
        return list(self._data.keys())

    def push(self, tag: str, nvr: str, deferred: Deferred) -> None:
        """
        Store a Deferred for the given tag and NVR combination.

        Args:
            tag: The tag name to watch
            nvr: The NVR to wait for
            deferred: The Deferred to associate with this tag+NVR
        """
        if tag not in self._data:
            self._data[tag] = {}
        self._data[tag][nvr] = deferred

    def pop(self, tag: str, nvr: str) -> Deferred:
        """
        Remove and return the Deferred for the given tag and NVR combination.

        Args:
            tag: The tag name
            nvr: The NVR

        Returns:
            The Deferred associated with this tag+NVR

        Raises:
            KeyError: If the tag or NVR is not found
        """
        deferred = self._data[tag].pop(nvr)
        # Clean up empty tag entries
        if not self._data[tag]:
            del self._data[tag]
        return deferred

    def get_nvrs_from_tag(
        self, tag: str
    ) -> Generator[tuple[str, Deferred], None, None]:
        """
        Yield all NVR and Deferred pairs for the given tag.

        Args:
            tag: The tag name to get NVRs for

        Yields:
            Tuples of (nvr, deferred) for each NVR registered under the tag
        """
        if tag in self._data:
            yield from self._data[tag].items()


class ELNBuildSyncState:
    """
    This class contains all live state information about the running process

    TODO: Make data persistent on disk
    """

    # A dictionary to keep track of tasks in-progress
    active_tasks: ClassVar[dict] = {}

    # A data structure to keep track of NVRs we're waiting to
    # appear in a tag.
    pending_nvr_tags: ClassVar[PendingNVRTags] = PendingNVRTags()
