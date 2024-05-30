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


import json
import logging

from collections import defaultdict

from twisted.internet.defer import TimeoutError as DeferredTimeoutError

from .rebuildattempt import RebuildAttempt
from .rebuildbatchslice import RebuildBatchSlice
from .tagmessage import TagMessage

from . import config
from . import kojihelpers
from . import db_models
from .utils import as_deferred


logger = logging.getLogger(__name__)


class RebuildBatch:
    # Temporary internal variable to store the latest batch ID
    # Remove this once we are getting this from the DB
    _latest_batch_id = 0

    def __init__(
        self,
        target: str,
        tag_messages: list[TagMessage],
        scratch=False,
        fail_fast=False,
    ):
        """
        Do not call RebuildBatch() alone. Instantiate via
        `await RebuildBatch(target, msgs).async_init()` instead.
        This ensures that the database actions will settle before the object
        is used.
        """
        self.tag_messages = dict()
        self.target = target
        self.scratch = scratch
        self.fail_fast = fail_fast
        self.side_tag = None
        self.slices = list()
        self._dest_tag = None
        self._side_tag_base = None
        self._unprocessed_tag_messages = tag_messages

        # Database object
        self._db_obj = None

        logger.debug(
            f"Creating batch from {len(self._unprocessed_tag_messages)} messages"
        )

    async def async_init(self):
        build_ids = list()
        for tag_message in self._unprocessed_tag_messages:
            await self.add_tag_message(tag_message)

            if not config.skip_tag("rpms", tag_message.component):
                build_ids.append(tag_message.get_build_id())

        (
            self._side_tag_base,
            self._dest_tag,
        ) = await kojihelpers.tags.get_tags_for_target(self.target)

        # Create the side-tag for this batch
        while True:
            try:
                self.side_tag = await kojihelpers.tags.prepare_side_tag(
                    self._side_tag_base,
                    build_ids,
                )
            except DeferredTimeoutError as e:
                # Keep retrying to create a side-tag.
                # Any other exception will be propagated up the stack.
                logger.warning(
                    f"Timed out creating the side-tag from {self._side_tag_base}. Retrying."
                )
                continue

            # Side-tag is ready. Proceed.
            break

        # Create the RebuildBatch record in the database here.
        await as_deferred(self._async_db_init())

        return self

    async def _async_db_init(self):
        """
        This function returns a coroutine and must be wrapped by
        `as_deferred()` to be used in a Twisted async function.
        """
        async with db_models.async_session() as session:
            tag_msg_objs = [msg._db_obj for msg in self.tag_messages.values()]
            koji_opts = {
                "scratch": self.scratch,
                "fail_fast": self.fail_fast,
            }
            db_batch = db_models.DBRebuildBatch(
                side_tag=self.side_tag,
                dest_tag=self._dest_tag,
                tag_messages=tag_msg_objs,
                options=json.dumps(koji_opts),
                completed=False,
            )
            session.add(db_batch)
            session.commit()

        self._db_obj = db_batch

    async def add_tag_message(self, message: TagMessage):
        # Overwrite any earlier instance of this component, since we only want
        # to rebuild the most recent one. This is necessary to avoid races
        # where the older build is tagged in after the newer one.
        if message.component in self.tag_messages:
            # There's an earlier build already queued.
            drop_message = self.tag_messages[message.component]

            # Remove this entry from the database so it doesn't get
            # re-loaded in the future
            await as_deferred(drop_message.drop())

        self.tag_messages[message.component] = message

    @staticmethod
    def _get_srpm_nvr_from_task_msg(msg_body) -> str:
        try:
            children = msg_body["info"]["children"]
        except NameError as e:
            raise ValueError("Missing children in message") from e

        for child in children:
            if child["method"] == "buildSRPMFromSCM":
                try:
                    srpm_field = child["result"]["srpm"]
                except KeyError as e:
                    raise ValueError("Missing 'srpm' in message") from e
                break

        return srpm_field.split("/")[-1].partition(".src.rpm")[0]

    async def run(self):
        # Get the SCM URLs and order them
        all_tag_messages = defaultdict(list)
        for tag_message in self.tag_messages.values():
            order = config.get_order("rpms", tag_message.component)
            all_tag_messages[order].append(tag_message)

        all_successes = dict()

        # Create RebuildBatchSlices for each ordering value
        for order, tag_messages in sorted(all_tag_messages.items()):
            slice = await RebuildBatchSlice(order, tag_messages, self).async_init()
            self.slices.append(slice)

        # Process each of the slices
        first = True
        for slice in self.slices:
            successes = await slice.run(skip_waitrepo=first)
            first = False
            all_successes.update(successes)

        # Get the list of NVRs that we will need to tag.
        build_nvrs = list()
        for task_id, msg_body in all_successes.items():
            try:
                nvr = RebuildBatch._get_srpm_nvr_from_task_msg(msg_body)
            except ValueError as e:
                # This message was missing some key information
                logger.critical(f"Couldn't get the NVR from {task_id}")
                logger.critical(msg_body)
                # Nothing we can do about this, so just give up.
                continue
            build_nvrs.append(nvr)

        # Only try to tag builds in if they're non-scratch builds.
        if self.scratch:
            for nvr in build_nvrs:
                logger.info(f"Not tagging scratch-build of {nvr} into {self._dest_tag}")

        else:
            for nvr in build_nvrs:
                logger.info(f"Tagging {nvr} into {self._dest_tag}")

            await kojihelpers.tags.tag_builds(self._dest_tag, build_nvrs)

        logger.info(f"Removing side-tag {self.side_tag}")
        await kojihelpers.tags.remove_side_tag(self.side_tag)

        await as_deferred(self._finalize())

    async def _finalize(self):
        """
        This function returns a coroutine and must be wrapped by
        `as_deferred()` to be used in a Twisted async function.
        """
        async with db_models.async_session() as session:
            self._db_obj.completed = True
            session.add(self._db_obj)
            await session.commit()
