# This file is part of ELNBuildSync
# Copyright (C) 2024-2026 Stephen Gallagher <sgallagh@redhat.com>

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

from .rebuildattempt import RebuildAttempt

logger = logging.getLogger(__name__)


class RebuildBatchSlice:
    """
    A subset of the packages to be built as part of a RebuildBatch, based on
    an ordering value. Triggers one or more RebuildAttempts.
    """

    def __init__(self, ordering, build_triggers, rebuild_batch):
        self.ordering = ordering
        self.build_triggers = build_triggers
        self.rebuild_batch = rebuild_batch

    async def run(self):
        logger.debug(f"Processing components at ordering {self.ordering}.")

        # Set up the RebuildAttempt
        all_successes = {}
        scm_urls = []
        for msg in self.build_triggers:
            try:
                scm_urls.append(await msg.get_scmurl())
            except Exception:
                logger.exception(
                    "Could not retrieve SCM URL for %s (build_id=%s); skipping",
                    msg.component,
                    msg.build_id,
                )
        attempt = await RebuildAttempt(scm_urls=scm_urls, slice=self).async_init()

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

            retry_urls = []
            for failure in failures.values():
                if failure["info"]["request"][0] is not None:
                    retry_urls.append(failure["info"]["request"][0])
                else:
                    # If the task failed due to a timeout, we don't want to
                    # retry it. Reduce the number of previous failures by one
                    # so we don't retry all the other tasks an extra time.
                    prev_failures -= 1

            if prev_failures <= 0:
                # If the only failure(s) were due to timeouts, we're done.
                break

            logger.info(
                f"Retrying {prev_failures} tasks that failed for {self.rebuild_batch.side_tag}"
            )

            for url in retry_urls:
                logger.debug(f"Retrying {url}")

            attempt = await RebuildAttempt(retry_urls, self).async_init()
            successes, failures = await attempt.async_await()
            all_successes.update(successes)

            num_failures = len(failures)

        # When we get here, either they have all succeeded or the same set
        # have failed twice in a row.
        failure_requests = []
        if num_failures:
            logger.warning(
                f"{num_failures} tasks failed for {self.rebuild_batch.side_tag}"
            )
            for task_id, err_msg in failures.items():
                try:
                    try:
                        request = err_msg["info"]["request"][0]
                    except ValueError:
                        request = err_msg["request"][0]
                    logger.warning(f"FAILED: {task_id}: {request}")
                    failure_requests.append(request)
                except Exception:
                    # If something goes wrong here, just log that the task failed.
                    logger.warning(f"FAILED: {task_id}")
                    failure_requests.append(f"Task: {task_id}")

        return all_successes, failure_requests
