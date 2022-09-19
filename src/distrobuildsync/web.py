#!/usr/bin/env python3

import json
import os
import re

from twisted.python.filepath import FilePath
from twisted.internet import reactor
from twisted.web.resource import Resource
from twisted.web.server import Site
from twisted.web.static import File as WebFile
from twisted.web.template import (
    Element,
    renderElement,
    renderer,
    TagLoader,
    XMLFile,
)

from datetime import datetime, timezone
from koji import BUILD_STATES

from . import config
from . import periodic

started = False
alive = True

logger = config.logger


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
        global alive

        request.setHeader("Cache-Control", "no-cache")
        if not alive:
            request.setResponseCode(500)
        return b""


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
        global started

        request.setHeader("Cache-Control", "no-cache")
        if not started:
            request.setResponseCode(503)
        return b""


class StatusTableElement(Element):

    loader = XMLFile(
        os.path.join(os.path.dirname(__file__), "templates", "status.xml")
    )

    @renderer
    def header(self, request, tag):
        update_time = periodic.status_data["__updated"].isoformat()
        yield tag.clone().fillSlots(update_time=update_time)

    @renderer
    def builds(self, request, tag):
        for pkg in sorted(periodic.status_data.keys()):
            if pkg.startswith("__"):
                continue

            build = periodic.status_data[pkg]

            # Default colors
            build_color = "#FFFFFF"
            tag_color = "#FFFFFF"
            nvr_color = "#FFFFFF"

            if build is None:
                build_color = "#99A3A4"
                tag_color = "#99A3A4"
                nvr_color = "#99A3A4"

                yield tag.clone().fillSlots(
                    name=pkg,
                    nvr="UNKNOWN",
                    nvr_bgcolor=nvr_color,
                    build_bgcolor=build_color,
                    state="UNKNOWN",
                    tag_bgcolor=tag_color,
                    tagged_build="UNKNOWN",
                    build_time="UNKNOWN",
                )

            else:
                if build["status"] == periodic.BuildStatus.MATCHED:
                    build_color = "#00FF00"
                    tag_color = "#00FF00"
                    state = "SUCCESS"
                elif build["status"] == periodic.BuildStatus.FAILED:
                    build_color = "#FF0000"
                    state = "FAILED"
                elif build["status"] == periodic.BuildStatus.BUILDING:
                    build_color = "#B4EEB4"
                    state = "Building"
                else:
                    build_color = "#00FFFF"
                    if build["status"] == periodic.BuildStatus.OLDER_THAN_TAG:
                        build_color = "#FFFF00"
                        state = "Newer build in tag"
                        if re.search("\.fc\d\d$", build["tagged"]):
                            tag_color = "#FF0000"
                        else:
                            tag_color = "#00FFFF"
                    elif (
                        build["status"] == periodic.BuildStatus.NEWER_THAN_TAG
                    ):
                        tag_color = "#FF0000"
                        state = "Succeeded but not tagged"
                    else:
                        build_color = "#FF0000"
                        tag_color = "#FF0000"
                        nvr_color = "#FF0000"
                        state = "Something went wrong"

                if "tagged" in build:
                    tagged_build = build["tagged"]
                else:
                    tagged_build = "UNKNOWN"

                build_time = "UNKNOWN"
                if "start_ts" in build and build["start_ts"]:
                    build_time = datetime.fromtimestamp(
                        build["start_ts"], tz=timezone.utc
                    ).isoformat()

                yield tag.clone().fillSlots(
                    name=pkg,
                    nvr=build["nvr"],
                    nvr_bgcolor=nvr_color,
                    build_bgcolor=build_color,
                    state=state,
                    tag_bgcolor=tag_color,
                    tagged_build=tagged_build,
                    build_time=build_time,
                )


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
        if not periodic.status_data:
            request.setResponseCode(503)
            return b"Server not ready, please try again in a few minutes"

        return renderElement(request, StatusTableElement())


class StatusJSONResource(Resource):
    """
    StatusJSONResource

    Outputs the full status data as a JSON document.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        if not periodic.status_data:
            request.setResponseCode(503)
            return b"Server not ready, please try again in a few minutes"

        return json.dumps(periodic.status_data, default=str).encode("UTF-8")


class UntaggedResource(Resource):
    """
    UntaggedResource

    Returns a list of packages whose NVRs aren't the latest in the destination tag
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        if not periodic.status_data:
            request.setResponseCode(503)
            return b"Server not ready, please try again in a few minutes"

        page = ""

        for pkg in sorted(periodic.status_data.keys()):
            # Ignore reserved entries
            if pkg.startswith("__"):
                continue

            build = periodic.status_data[pkg]

            if (
                build is None
                or (build and not build["tagged"] == True)
                and config.is_eligible("rpms", build["name"])
            ):
                page += f"{pkg}\n"

        return page.encode("UTF-8")


def setup_web_resources():
    global started
    root = RootResource()
    root.putChild(b"startup", StartupResource())
    root.putChild(b"alive", LivenessResource())
    root.putChild(b"status", StatusPageResource())
    root.putChild(b"status.json", StatusJSONResource())
    root.putChild(b"untagged", UntaggedResource())
    root.putChild(
        b"static", WebFile(os.path.join(os.path.dirname(__file__), "static"))
    )
    root.putChild(b"favicon.ico", LivenessResource())

    return Site(root)


if __name__ == "__main__":
    # For debugging
    reactor.listenTCP(8080, setup_web_resources())
    reactor.run()
