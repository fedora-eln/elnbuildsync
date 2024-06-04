#!/usr/bin/env python3

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

import json
import logging

from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.web.error import Error as WebError
from twisted.web.resource import Resource
from twisted.web.server import Site, NOT_DONE_YET
from twisted.web.util import Redirect

from . import batching
from . import config
from . import status

logger = logging.getLogger(__name__)


# Globals
started = False
alive = True


class RootResource(Resource):
    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)


class StartupResource(Resource):
    """
    StartupResource

    Returns either a 200 or a 503 response code, depending on whether
    the configuration has been loaded successfully.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        global started

        request.setHeader("Cache-Control", "no-cache")
        if not started:
            request.setResponseCode(503)
        return b"started"


class LivenessResource(Resource):
    """
    LivenessResource

    Returns either a 200 or a 500 response code or will time out if the server is deadlocked.

    Certain failures can set the 'alive' variable to False to indicate an unrecoverable error.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        global alive

        request.setHeader("Cache-Control", "no-cache")
        if not alive:
            request.setResponseCode(500)
        return b"alive"


class StatusJSONResource(Resource):
    """
    StatusJSONResource

    Returns either a 200 or 503 response code, depending on whether the first
    periodic status update has completed successfully.

    Outputs the full status data as a JSON document
    """

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json")
        request.setHeader("Cache-Control", "no-cache")
        if not status.encoded_json_data:
            request.setResponseCode(503)
            return b""

        return status.encoded_json_data


class StatusPageResource(Resource):
    """
    StatusPageResource

    Returns a table of the most recent rebuild attempts for each package.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        if not status.web_page:
            request.setResponseCode(503)
            return b"Server not ready, please try again in a few minutes"

        return status.web_page


class TriggerBuildResource(Resource):
    """
    TriggerBuildResource

    Accepts a POST request containing a JSON list of components to rebuild for
    ELN.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def _done(self, data):
        self.request.finish()

    def _failed(self, data):
        logger.critical(data)
        self.request.finish()

    def render_POST(self, request):
        self.request = request

        deferred = Deferred.fromCoroutine(self._do_post())
        deferred.addCallback(self._done)
        deferred.addErrback(self._failed)
        return NOT_DONE_YET

    async def _do_post(self):
        global started

        self.request.setHeader("Cache-Control", "no-cache")
        if not started or config.is_paused():
            self.request.setResponseCode(503)
            return

        content_type = self.request.getHeader("Content-Type")
        if not content_type or content_type != "application/json":
            self.request.setResponseCode(415)
            self.request.write(b"Unsupported Content-Type\n")
            raise WebError("Invalid Content-Type")

        # Read in the content
        # TODO: This is insecure! Do something about this.
        try:
            components = json.load(self.request.content)
        except json.decoder.JSONDecodeError as e:
            logger.exception(e)
            raise

        await batching.rebuild_from_components(components)


class LogLevelResource(Resource):
    """
    LogLevelResource

    Sets the log level of the application or returns 400 if an invalid log
    level is specified
    """

    def getChild(self, name, request):
        return LogLevelPage(name)


class LogLevelPage(Resource):
    def __init__(self, name):
        super().__init__()
        self.loglevel = name.decode("UTF-8").upper()

    def render_GET(self, request):
        try:
            logging.getLogger().setLevel(self.loglevel)
        except ValueError as e:
            return f"Invalid log level: {self.loglevel}".encode("UTF-8")

        logger.warning(f"Log Level changed to {self.loglevel}")
        return f"Log level set to {self.loglevel}".encode("UTF-8")


def setup_web_resources():
    global started
    root = RootResource()
    root.putChild(b"startup", StartupResource())
    root.putChild(b"alive", LivenessResource())
    root.putChild(b"loglevel", LogLevelResource())
    root.putChild(b"status.json", StatusJSONResource())
    root.putChild(b"status.html", StatusPageResource())
    root.putChild(b"status", Redirect(b"status.html"))
    root.putChild(b"trigger", TriggerBuildResource())
    started = True

    return Site(root)


if __name__ == "__main__":
    # For debugging
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=logging.DEBUG,
    )
    reactor.listenTCP(8080, setup_web_resources())
    reactor.run()
