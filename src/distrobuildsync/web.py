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
from . import kojihelpers
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

            task_url = ""

            if build is None:
                yield tag.clone().fillSlots(
                    name=pkg,
                    nvr="UNKNOWN",
                    state="UNKNOWN",
                    detail="Not known to Koji",
                    task="",
                    task_url=task_url,
                    tagged_build="UNKNOWN",
                    build_time="UNKNOWN",
                )

            else:
                detail = ""

                if "task_id" in build:
                    cfg = kojihelpers.get_koji_config("destination")
                    task_url = os.path.join(
                        cfg["weburl"],
                        "taskinfo?taskID={}".format(build["task_id"]),
                    )
                    task = "{}".format(build["task_id"])

                if build["status"] == periodic.BuildStatus.MATCHED:
                    state = "SUCCESS"
                elif build["status"] == periodic.BuildStatus.BUILDING:
                    state = "Building"
                else:
                    if build["status"] == periodic.BuildStatus.OLDER_THAN_TAG:
                        if re.search("\.fc\d\d$", build["tagged"]):
                            state = "FAILED"
                            detail = "Fedora build in tag"
                        else:
                            state = "SUCCESS"
                            detail = "Newer ELN build in tag"
                    elif (
                        build["status"] == periodic.BuildStatus.NEWER_THAN_TAG
                    ):
                        state = "FAILED"
                        detail = "Succeeded but not tagged"
                    else:
                        state = "FAILED"
                        detail = "Build failed"

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
                    view=config.comps["rpms"][pkg]["view"]
                    if "view" in config.comps["rpms"][pkg]
                    else "Unknown",
                    nvr=build["nvr"],
                    state=state,
                    task=task,
                    task_url=task_url,
                    detail=detail,
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
