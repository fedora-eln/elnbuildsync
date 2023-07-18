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
import fedora_messaging.config
import logging
import sys

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks

from . import config
from .kojihelpers import tags
from . import listener
from . import web


logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
    default="INFO",
    show_default=True,
)
@click.option("--dry-run", is_flag=True, help="Simulate actions only")
@click.argument("config_url")
def main(log_level, dry_run, config_url):
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s", level=log_level
    )
    for handler in logging.root.handlers:
        handler.addFilter(logging.Filter("elnbuildsync"))
    logger.debug("Debug logging enabled")

    config.dry_run = dry_run

    # Read in the config file
    try:
        config.load_config(config_url)
    except Exception as e:
        logger.exception(e)
        logger.critical("Could not load configuration.")
        sys.exit(128)

    # Start listening for Fedora Messages
    fedora_messaging.api.twisted_consume(listener.message_handler)

    logger.debug("Starting HTTP server")
    reactor.listenTCP(8080, web.setup_web_resources())

    reactor.callLater(5, test_side_tag)

    logger.debug("Starting Twisted mainloop")
    reactor.run()
    pass


@inlineCallbacks
def test_side_tag():
    side_tag = yield tags.prepare_side_tag("eln-build")
    logger.info(f"Side tag {side_tag} all ready for builds")


if __name__ == "__main__":
    main()
