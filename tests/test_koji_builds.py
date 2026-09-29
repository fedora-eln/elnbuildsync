# This file is part of ELNBuildSync
# Copyright (C) 2026 Stephen Gallagher <sgallagh@redhat.com>

# SPDX-License-Identifier: GPL-3.0-or-later

from unittest.mock import AsyncMock, patch

import koji
import pytest

from elnbuildsync import daemon
from elnbuildsync.kojihelpers import builds


@pytest.mark.asyncio
class TestCancelStaleTasks:
    async def test_cancels_active_tasks_with_recurse_false(self):
        user_info = {"id": 42, "name": "ebs-user"}
        active_tasks = [{"id": 1001}, {"id": 1002}, {"id": 1003}]

        with patch.object(builds, "call_koji", new_callable=AsyncMock) as mock_call:
            # side_effect as a list makes each successive call_koji() call
            # return the next value in turn: 1st call returns user_info
            # (getLoggedInUser), 2nd returns active_tasks (listTasks), 3rd
            # returns {} (the multicall cancel result).
            mock_call.side_effect = [user_info, active_tasks, {}]
            await builds.cancel_stale_tasks()

        assert mock_call.call_count == 3
        mock_call.assert_any_call("getLoggedInUser")
        mock_call.assert_any_call(
            "listTasks",
            opts={
                "owner": 42,
                "state": [
                    koji.TASK_STATES["FREE"],
                    koji.TASK_STATES["OPEN"],
                    koji.TASK_STATES["ASSIGNED"],
                ],
            },
        )
        # The cancel call uses recurse=False because listTasks already
        # returns both parent and child tasks.
        cancel_call = mock_call.call_args_list[2]
        assert cancel_call.args[0] is builds._cancel_multiple_tasks_thread
        assert cancel_call.args[1] == [1001, 1002, 1003]
        assert cancel_call.kwargs == {"recurse": False}

    async def test_no_stale_tasks(self):
        user_info = {"id": 42, "name": "ebs-user"}

        with patch.object(builds, "call_koji", new_callable=AsyncMock) as mock_call:
            # 1st call → user_info (getLoggedInUser), 2nd → [] (listTasks).
            # Empty list causes cancel_stale_tasks() to return early; no
            # third call is made.
            mock_call.side_effect = [user_info, []]
            await builds.cancel_stale_tasks()

        assert mock_call.call_count == 2

    async def test_get_user_failure_skips_cleanup(self):
        with patch.object(
            builds,
            "call_koji",
            new_callable=AsyncMock,
            side_effect=koji.GenericError("auth error"),
        ) as mock_call:
            await builds.cancel_stale_tasks()

        assert mock_call.call_count == 1

    async def test_list_tasks_failure_skips_cleanup(self):
        user_info = {"id": 42, "name": "ebs-user"}

        with patch.object(builds, "call_koji", new_callable=AsyncMock) as mock_call:
            # 1st call → user_info (getLoggedInUser), 2nd → raises (listTasks).
            mock_call.side_effect = [user_info, koji.GenericError("list error")]
            await builds.cancel_stale_tasks()

        assert mock_call.call_count == 2

    async def test_cancel_failure_is_logged_not_raised(self):
        user_info = {"id": 42, "name": "ebs-user"}
        active_tasks = [{"id": 1001}]

        with patch.object(builds, "call_koji", new_callable=AsyncMock) as mock_call:
            # 1st → user_info, 2nd → active_tasks, 3rd → raises on cancel.
            mock_call.side_effect = [
                user_info,
                active_tasks,
                koji.GenericError("cancel failed"),
            ]
            # Should not raise
            await builds.cancel_stale_tasks()

        assert mock_call.call_count == 3

    async def test_dry_run_skips_cancellation(self, monkeypatch):
        monkeypatch.setattr(builds.config, "dry_run", True)

        with patch.object(builds, "call_koji", new_callable=AsyncMock) as mock_call:
            await builds.cancel_stale_tasks()

        mock_call.assert_not_called()

    async def test_enable_stale_task_cancel_false_skips_cancellation(self, monkeypatch):
        monkeypatch.setattr(builds.config, "enable_stale_task_cancel", False)

        with patch.object(builds, "call_koji", new_callable=AsyncMock) as mock_call:
            await builds.cancel_stale_tasks()

        mock_call.assert_not_called()


def test_cli_has_enable_stale_task_cancel_flag():
    option_names = [
        opt
        for param in daemon.main.params
        for opt in (*param.opts, *param.secondary_opts)
    ]
    assert "--enable-stale-task-cancel" in option_names
    assert "--no-enable-stale-task-cancel" in option_names
