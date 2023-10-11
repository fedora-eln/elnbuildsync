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
from . import cleanup
from . import config
from . import listener
from . import status
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
@click.option("--config-url", default=None)
@click.option("--config-file", default=None)
@click.option(
    "--untagging/--no-untagging",
    default=False,
    help="Untag all but the most recent builds in the destination target",
)
def main(log_level, dry_run, config_url, config_file, untagging):
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=log_level,
    )
    for handler in logging.root.handlers:
        handler.addFilter(logging.Filter("elnbuildsync"))
    logger.debug("Debug logging enabled")

    config.dry_run = dry_run
    config.do_untagging = untagging

    task.deferLater(reactor, 0, initialize_services, config_url, config_file)

    logger.debug("Starting Twisted mainloop")
    reactor.run()


@inlineCallbacks
def initialize_services(config_url=None, config_file=None):
    # Read in the config file
    try:
        yield config.load_config(config_git_url=config_url, config_file=config_file)
    except Exception as e:
        logger.exception(e)
        logger.critical("Could not load configuration.")
        sys.exit(128)

    # Schedule configuration updates
    updater = task.LoopingCall(config.update_config)
    updater.start(config.config_timer, now=False)

    # Schedule batch checking
    batching.message_batch_processor = task.LoopingCall(batching.process_message_batch)
    batching.message_batch_processor.start(batching.message_batch_timer, now=False)

    # Schedule periodic status page and run it once at startup
    config.status_processor = task.LoopingCall(status.create_status_page)
    config.status_processor.start(config.status_timer, now=True)

    # Schedule periodic cleanup
    config.cleanup_processor = task.LoopingCall(cleanup.periodic_cleanup)
    config.cleanup_processor.start(config.cleanup_timer, now=False)

    # Add a five-minute timer to check for task completion, because Koji
    # does not always send out an AMQP message as expected
    listener.task_check_processor = task.LoopingCall(listener.check_tasks)
    listener.task_check_processor.start(config.task_check_timer, now=False)

    # Start listening for Fedora Messages
    fedora_messaging.api.twisted_consume(listener.message_handler)

    logger.debug("Starting HTTP server")
    reactor.listenTCP(8080, web.setup_web_resources())


if __name__ == "__main__":
    main()
