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
import logging

from twisted.internet import reactor, task

from . import web


logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--log-level",
    type=click.Choice(
        ["DEBUG", "INFO" "WARNING", "ERROR", "CRITICAL"], case_sensitive=False
    ),
)
def main(log_level):
    logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s")
    logger.setLevel(log_level)
    reactor.listenTCP(8080, web.setup_web_resources())
    reactor.run()
    pass


if __name__ == "__main__":
    main()
