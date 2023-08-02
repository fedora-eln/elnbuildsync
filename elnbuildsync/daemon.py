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

from twisted.internet import reactor, task
from twisted.internet.defer import Deferred, inlineCallbacks

from . import batching
from . import config
from . import kojihelpers
from . import listener
from . import web

from .kojihelpers.builds import perform_builds


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
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=log_level,
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

    # Schedule batch checking
    batching.message_batch_processor = task.LoopingCall(batching.process_message_batch)
    batching.message_batch_processor.start(batching.message_batch_timer, now=False)

    # Start listening for Fedora Messages
    fedora_messaging.api.twisted_consume(listener.message_handler)

    logger.debug("Starting HTTP server")
    reactor.listenTCP(8080, web.setup_web_resources())

    task.deferLater(
        reactor,
        1,
        perform_builds,
        "eln",
        [
            "git+https://src.fedoraproject.org/rpms/fedora-release.git#b8076dc0584f61b29bc851de67e8397184701dab",
        ],
        scratch=True,
    )

    logger.debug("Starting Twisted mainloop")
    reactor.run()
    pass


@inlineCallbacks
def test_side_tag():
    side_tag = yield kojihelpers.tags.prepare_side_tag("eln-build")
    logger.info(f"Side tag {side_tag} is ready for builds")


@inlineCallbacks
def test_wait_repo():
    side_tag = yield kojihelpers.tags.wait_repo("eln-build")
    logger.info(f"Side tag {side_tag} is ready for builds")


@inlineCallbacks
def test_get_buildinfo():
    import json

    buildinfo = yield kojihelpers.builds.get_buildinfo("source", 2234734, strict=True)
    logger.info(f"TEST: {json.dumps(buildinfo, indent=2)}")


@inlineCallbacks
def test_awaiting_queue():
    logger.info("Awaiting data")
    yield wait_for_queue()
    logger.info("Successfully awaited")


testing_deferred = None


def wait_for_queue():
    global testing_deferred
    testing_deferred = Deferred()
    return testing_deferred


def sim_put():
    global testing_deferred
    logger.info("Putting")
    testing_deferred.callback(None)
    logger.info("Putting complete")


if __name__ == "__main__":
    main()
