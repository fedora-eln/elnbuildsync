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

from enum import IntEnum
from twisted.internet.defer import TimeoutError as DeferredTimeoutError

from . import config
from . import kojihelpers
from .rebuildattempt import RebuildAttempt

logger = logging.getLogger(__name__)


class RebuildBatchSliceStatus(IntEnum):
    # __init__() has been called on this slice, but it's not in the DB yet
    INITIALIZING = 0

    # The slice has been fully initialized and recorded in the DB, but has not
    # yet started running.
    QUEUED = 1

    # The slice has begun running
    # Note: this state may be inaccurate; it is possible that the slice has
    # completed or otherwise terminated and we haven't yet detected that.
    # This is particularly likely when loading from the database on a process
    # restart.
    RUNNING = 2

    # The slice has terminated and the results are available.
    FINISHED = 3


class RebuildBatchSlice:
    """
    A subset of the packages to be built as part of a RebuildBatch, based on
    an ordering value. Triggers one or more RebuildAttempts.
    """

    def __init__(self, ordering, scm_urls, rebuild_batch):
        """
        Never call this function on its own. Invoke via
        ```
        await RebuildBatchSlice(ordering, scm_urls, rebuild_batch).async_init()
        ```
        """

        self.ordering = ordering
        self.scm_urls = scm_urls
        self.rebuild_batch = rebuild_batch
        self.status = RebuildBatchSliceStatus.INITIALIZING

        # Database object
        self._db_obj = None

    async def async_init(self):
        # TODO: Create database entry and mark it as "queued"
        # self._rebuild_batch_slice_id = ID from database

        self.status = RebuildBatchSliceStatus.QUEUED

        return self

    async def run(self, skip_waitrepo=False):
        logger.debug(f"Processing components at ordering {self.ordering}.")

        # TODO: Update database state to be "running"
        self.status = RebuildBatchSliceStatus.RUNNING

        if skip_waitrepo is False:
            try:
                await kojihelpers.tags.wait_repo(self.rebuild_batch.side_tag)
            except DeferredTimeoutError as e:
                logger.warning(
                    f"Timed out awaiting side-tag {self.rebuild_batch.side_tag}, proceeding anyway."
                )

        # Set up the RebuildAttempt
        all_successes = dict()
        attempt = attempt = await RebuildAttempt(
            self.scm_urls, self.rebuild_batch
        ).async_init()

        successes, failures = await attempt.async_await()

        # Store all successful builds for later tagging
        all_successes.update(successes)

        for success in successes.values():
            logger.info(f"Rebuild of {success['info']['request'][0]} succeeded")

        # == Retry Loop == #

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
                f"Retrying {num_failures} tasks that failed for {self.rebuild_batch.side_tag}"
            )

            retry_urls = [
                failure["info"]["request"][0] for failure in failures.values()
            ]
            for url in retry_urls:
                logger.debug(f"Retrying {url}")

            attempt = await RebuildAttempt(retry_urls, self).async_init()
            successes, failures = await attempt.async_await()
            all_successes.update(successes)

            num_failures = len(failures)

        # When we get here, either they have all succeeded or the same set
        # have failed twice in a row.
        if num_failures:
            logger.warning(
                f"{num_failures} tasks failed for {self.rebuild_batch.side_tag}"
            )
            for task_id, err_msg in failures.items():
                try:
                    try:
                        request = err_msg["info"]["request"][0]
                    except ValueError as e:
                        request = err_msg["request"][0]
                    logger.warning(f"FAILED: {task_id}: {request}")
                except Exception as e:
                    # If something goes wrong here, just log that the task failed.
                    logger.warning(f"FAILED: {task_id}")

        # TODO: Update database state to "finished"
        self.status = RebuildBatchSliceStatus.FINISHED

        return all_successes
