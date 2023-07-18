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
import json
import logging

from twisted.internet import reactor

from . import listener
from . import logger
from . import web


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
    logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s")
    logger.setLevel(log_level)
    logger.debug("Debug logging enabled")

    # Start listening for Fedora Messages
    fedora_messaging.api.twisted_consume(listener.message_handler)

    # Fedora Messaging Config
    logger.debug(json.dumps(fedora_messaging.config.conf, indent=2))

    logger.debug("Starting HTTP server")
    reactor.listenTCP(8080, web.setup_web_resources())

    logger.debug("Starting Twisted mainloop")
    reactor.run()
    pass


if __name__ == "__main__":
    main()
