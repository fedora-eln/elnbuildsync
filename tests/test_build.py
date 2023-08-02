#!/usr/bin/python3

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


import click
import fedora_messaging.api
import logging
import sys

from twisted.internet import reactor, task
from twisted.internet.defer import inlineCallbacks

import elnbuildsync

logger = logging.getLogger(__name__)


@inlineCallbacks
def test_build():
    try:
        yield elnbuildsync.kojihelpers.builds.perform_builds(
            "eln",
            [
                "git+https://src.fedoraproject.org/rpms/fedora-release.git#b8076dc0584f61b29bc851de67e8397184701dab",
            ],
            scratch=True,
        )
    except Exception as e:
        logger.exception(e)
        raise

    reactor.stop()


@click.command()
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
)
def main(log_level):
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=log_level,
    )
    for handler in logging.root.handlers:
        handler.addFilter(logging.Filter("elnbuildsync"))
    logger.debug("Debug logging enabled")

    # Read in the config file
    try:
        # TODO: replace with test config file
        elnbuildsync.config.load_config("https://gitlab.com/redhat/centos-stream/ci-cd/distrosync/distrobuildsync-config")
    except Exception as e:
        logger.exception(e)
        logger.critical("Could not load configuration.")
        sys.exit(128)

    # Start listening for Fedora Messages
    fedora_messaging.api.twisted_consume(elnbuildsync.listener.message_handler)

    task.deferLater(reactor, 1, test_build)

    logger.debug("Starting Twisted mainloop")
    reactor.run()


if __name__ == "__main__":
    main()
