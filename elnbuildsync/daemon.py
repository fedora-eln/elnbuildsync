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

# Install the asyncio reactor as early as possible
# The fedora_messaging imports may install the Twisted one otherwise
import asyncio
from twisted.internet import asyncioreactor

try:
    event_loop = asyncio.get_event_loop()
except RuntimeError:
    event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(event_loop)
asyncioreactor.install(event_loop)

import click
import fedora_messaging.api
import fedora_messaging.config
import logging
import sys

from twisted.internet import task
from twisted.internet.defer import Deferred

from . import batching
from . import cleanup
from . import db_models
from . import config
from . import listener
from . import status
from . import web


logger = logging.getLogger(__name__)


def log_filter(record):
    if record.name.startswith("elnbuildsync"):
        return True

    if record.name.startswith("sqlalchemy") and config.is_debug():
        return True

    return False


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
@click.option(
    "--lull-time",
    default=config.message_batch_timer,
    show_default=True,
    type=int,
    help="How long (in seconds) to wait after the last trigger before starting the batch",
)
@click.option("--config-url", default=None)
@click.option("--config-file", default=None)
@click.option("--db-pw-file", type=click.File(mode="r"), default="/etc/ebs_db_pw")
@click.option(
    "--untagging/--no-untagging",
    default=False,
    help="Untag all but the most recent builds in the destination target",
)
def main(log_level, dry_run, lull_time, config_url, config_file, db_pw_file, untagging):
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=log_level,
    )
    for handler in logging.root.handlers:
        handler.addFilter(log_filter)
    logger.debug("Debug logging enabled")

    config.dry_run = dry_run
    config.do_untagging = untagging
    config.message_batch_timer = lull_time

    logger.debug("Starting Twisted mainloop")
    return task.react(
        lambda reactor: Deferred.fromCoroutine(
            _main(reactor, db_pw_file, config_url, config_file)
        )
    )


async def _main(reactor, db_pw_file, config_url=None, config_file=None) -> None:
    config.terminator = Deferred()

    # Read in the database password
    db_pw = db_pw_file.readline().rstrip()

    # Read in the config file
    try:
        await config.load_config(
            db_pw, config_git_url=config_url, config_file=config_file
        )
    except Exception as e:
        logger.exception(e)
        logger.critical("Could not load configuration.")
        sys.exit(128)

    # Set up the Database
    logger.info("Initializing database")
    engine = await db_models.init_db(config.db_url, echo=config.is_debug())
    logger.info("Database Initialized")

    # Schedule configuration updates
    updater = task.LoopingCall(config.update_config)
    updater.start(config.config_timer, now=False)

    # Schedule batch checking
    batching.message_batch_processor = task.LoopingCall(batching.process_message_batch)
    batching.message_batch_processor.start(config.message_batch_timer, now=False)

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

    logger.info("Starting HTTP server")
    reactor.listenTCP(8080, web.setup_web_resources())
    logger.info("HTTP server ready")

    await config.terminator


if __name__ == "__main__":
    main()
