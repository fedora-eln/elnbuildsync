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
import os
import pathlib
import sys

from twisted.internet import reactor, task
from twisted.internet.defer import inlineCallbacks

import elnbuildsync

logger = logging.getLogger(__name__)


@inlineCallbacks
def test_create_side_tag():
    try:
        side_tag = yield elnbuildsync.kojihelpers.tags.prepare_side_tag("eln-build")
    except Exception as e:
        logger.exception(e)
        raise

    logger.info(f"Side tag {side_tag} is ready.")
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
        level=logging.CRITICAL,
    )
    logger.setLevel(log_level)
    logger.debug("Debug logging enabled")

    # The elnbuildsync module has its own logger
    ebs_logger = logging.getLogger("elnbuildsync")
    ebs_logger.setLevel(log_level)
    ebs_logger.debug("Debug logging enabled")

    # Read in the main config file
    try:
        # TODO: replace with test config file
        elnbuildsync.config.load_config(
            "https://gitlab.com/redhat/centos-stream/ci-cd/distrosync/distrobuildsync-config"
        )
    except Exception as e:
        logger.exception(e)
        logger.critical("Could not load configuration.")
        sys.exit(128)

    # Set the current working directory to the root of the tests
    # This is needed so that relative paths to certificates in the
    # fedora_messaging config will work
    script_dir = pathlib.Path(__file__).parent
    os.chdir(script_dir)

    # Read in the Fedora Messages config file
    fedora_messaging.config.conf.load_config(f"{script_dir}/fedtest.toml")

    # Start listening for Fedora Messages
    logger.debug("Start listening for Fedora messages")
    fedora_messaging.api.twisted_consume(elnbuildsync.listener.message_handler)

    logger.debug("Queueing up the test build")
    task.deferLater(reactor, 1, test_create_side_tag)

    logger.debug("Starting Twisted mainloop")
    reactor.run()


if __name__ == "__main__":
    main()
