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

from fedora_messaging.message import Message as FedoraMessage
from twisted.internet.defer import inlineCallbacks, TimeoutError

from .rebuildattempt import RebuildAttempt
from .tagmessage import TagMessage

from . import config
from . import kojihelpers


logger = logging.getLogger(__name__)


class RebuildBatch:
    # Temporary internal variable to store the latest batch ID
    # Remove this once we are getting this from the DB
    _latest_batch_id = 0

    def __init__(self, target: str, tag_messages: list[TagMessage], scratch=False):
        """
        Do not call RebuildBatch() alone. Instantiate via
        `yield RebuildBatch(target, msgs).async_init()` instead.
        This ensures that the database actions will settle before the object
        is used.
        """
        self.tag_messages = dict()
        self.target = target
        self.scratch = scratch
        self.side_tag = None
        self._dest_tag = None
        self._side_tag_base = None
        self._unprocessed_tag_messages = tag_messages

        # Database ID
        self._rebuild_batch_id = 0

    @inlineCallbacks
    def async_init(self):
        build_ids = list()
        for tag_message in self._unprocessed_tag_messages:
            yield self.add_tag_message(tag_message)

            if not config.skip_tag("rpms", tag_message.component):
                build_ids.append(tag_message.get_build_id())

        (
            self._side_tag_base,
            self._dest_tag,
        ) = yield kojihelpers.tags.get_tags_for_target(self.target)

        # Create the side-tag for this batch
        while True:
            try:
                self.side_tag = yield kojihelpers.tags.prepare_side_tag(
                    self._side_tag_base,
                    build_ids,
                )
            except TimeoutError as e:
                # Keep retrying to create a side-tag.
                # Any other exception will be propagated up the stack.
                logger.warning(
                    f"Timed out creating the side-tag from {self._side_tag_base}. Retrying."
                )

                # Delete the failed side-tag
                yield kojihelpers.tags.remove_side_tag(self.side_tag)
                continue

            # Side-tag is ready. Proceed.
            break

        # Create the RebuildBatch record in the database here.
        # self._rebuild_batch_id = ID from database

        # TODO: get this from the DB
        self._rebuild_batch_id = self._latest_batch_id
        self._latest_batch_id += 1

        return self

    @inlineCallbacks
    def add_tag_message(self, message: TagMessage):
        # Add the tag_message object to this batch
        yield message.assign_to_rebuildbatch(self._rebuild_batch_id)

        # Overwrite any earlier instance of this component, since we only want
        # to rebuild the most recent one. This is necessary to avoid races
        # where the older build is tagged in after the newer one.
        if message.component in self.tag_messages:
            # There's an earlier build already queued.
            drop_message = self.tag_messages[message.component]

            # Remove this entry from the database so it doesn't get
            # re-loaded in the future
            yield drop_message.drop()

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
                except NameError as e:
                    raise ValueError("Missing 'srpm' in message") from e
                break

        return srpm_field.split("/")[-1].partition(".src.rpm")[0]

    @inlineCallbacks
    def run(self):
        # Create a RebuildAttempt
        # The initial one contains the complete set of components from the tag_messages

        # Get the list of SCM URLs
        scm_urls = list()
        for tag_message in self.tag_messages.values():
            scm_urls.append(tag_message.scmurl)

        attempt = yield RebuildAttempt(scm_urls, self).async_init()
        successes, failures = yield attempt.async_await()

        # Store all successful builds for later tagging
        all_successes = successes

        for success in successes.values():
            logger.info(f"Rebuild of {success['info']['request'][0]} succeeded")

        # TODO: Config option to enable/disable retry loop

        # Arbitrarily pick ten million, since we will never have that many
        # packages, let alone failures.
        # Note: if the batch consists of a single component, it will still be
        # retried here if it fails. This is intentional and should reduce the
        # number of flaky-test failures.
        prev_failures = 10000000
        num_failures = len(failures)
        while num_failures > 0 and num_failures < prev_failures:
            prev_failures = num_failures

            logger.info(
                f"Retrying {num_failures} tasks that failed for {self.side_tag}"
            )
            retry_urls = [
                failure["info"]["request"][0] for failure in failures.values()
            ]
            for url in retry_urls:
                logger.debug(f"Retrying {url}")

            attempt = yield RebuildAttempt(retry_urls, self).async_init()
            successes, failures = yield attempt.async_await()
            all_successes.update(successes)

            num_failures = len(failures)

        if num_failures:
            logger.warning(f"{num_failures} tasks failed for {self.side_tag}")
            for task_id, err_msg in failures.items():
                logger.warning(f"FAILED: {task_id}: {err_msg['srpm']}")

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
                pass
            build_nvrs.append(nvr)

        # Only try to tag builds in if they're non-scratch builds.
        if self.scratch:
            for nvr in build_nvrs:
                logger.info(f"Not tagging scratch-build of {nvr} into {self._dest_tag}")

        else:
            for nvr in build_nvrs:
                logger.info(f"Tagging {nvr} into {self._dest_tag}")

            yield kojihelpers.tags.tag_builds(self._dest_tag, build_nvrs)

        logger.info(f"Removing side-tag {self.side_tag}")
        yield kojihelpers.tags.remove_side_tag(self.side_tag)
