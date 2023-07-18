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

from twisted.internet import reactor
from twisted.internet.defer import Deferred, inlineCallbacks
from twisted.internet.threads import deferToThread

from . import awaited_repos, awaiting_repo_init
from .connection import get_buildsys

from .. import config


logger = logging.getLogger(__name__)


@inlineCallbacks
def prepare_side_tag(base_tag):
    """
    Creates a Koji side tag based on @base_tag

    Requests the side-tag and yields until the repo has been generated.

    :params str base_tag: The build tag to inherit from (e.g. f39-build)
    :return twisted.Deferred: A Twisted Deferred whose callback will fire once
    the repo is ready for use.
    """

    downstream_koji = get_buildsys("destination")

    # Trigger the creation of the side-tag
    side_tag_info = yield deferToThread(downstream_koji.createSideTag, base_tag)
    side_tag_name = side_tag_info["name"]

    # Wait for koji to generate the buildroot repo
    # We don't need to wait for the initialization, since this is a fresh
    # tag and therefore no race exists.
    # yield _wait_repo_done(side_tag_name)
    yield _wait_repo_done(side_tag_name)

    return side_tag_name


def wait_repo(tag):
    deferred = Deferred()
    deferred.addTimeout(config.waitrepo_timeout, reactor)
    awaiting_repo_init[tag].append(deferred)

    logger.info(f"Waiting for {tag} to begin regenerating")
    return deferred


def _wait_repo_done(tag, deferred=Deferred()):
    deferred.addTimeout(config.waitrepo_timeout, reactor)
    awaited_repos[tag].append(deferred)

    logger.info(f"Waiting for {tag} to finish regenerating")
    return deferred
