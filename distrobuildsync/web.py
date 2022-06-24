#!/usr/bin/env python3

import json

from twisted.internet import reactor
from twisted.web.resource import Resource
from twisted.web.server import Site

from datetime import datetime
from koji import BUILD_STATES

from . import config
from . import periodic

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

        page = f"""

<html>
<head><title>ELN Rebuild Status</title></head>
<body>
<h1>ELN Rebuild Status</h1>
<p>Updated at {periodic.status_data['__updated']}</p>
<table border=1 style=\"width:100%\">
<tr><th>Name</th><th>Status</th><th>NVR</th><th>Current in ELN</th><th>Build Time</th></tr>
"""
        for pkg in sorted(periodic.status_data.keys()):
            # Ignore reserved entries
            if pkg.startswith("__"):
                continue

            build = periodic.status_data[pkg]

            # Name
            page += f"<tr><td>{pkg}</td>"

            # Status
            if build is not None:
                if build["state"] == BUILD_STATES["COMPLETE"]:
                    page += '<td bgcolor="#00FF00">COMPLETE</td>'
                elif build["state"] == BUILD_STATES["BUILDING"]:
                    page += '<td bgcolor="#00FFFF">BUILDING</td>'
                elif build["state"] == BUILD_STATES["FAILED"]:
                    page += '<td bgcolor="#FF0000">FAILED</td>'
                else:
                    page += '<td bgcolor="#FF00FF">UNKNOWN</td>'

                # NVR
                page += f'<td>{build["nvr"]}</td>'

                # Tagged into 'eln'
                if not config.is_eligible("rpms", build["name"]):
                    page += f"<td>IGNORE</td>"
                elif build["tagged"] == True:
                    page += f'<td bgcolor="#00FF00">{build["nvr"]}</td>'
                elif build["tagged"]:
                    page += f'<td bgcolor="#FF0000">{build["tagged"]}</td>'
                else:
                    page += f'<td bgcolor="#FF00FF">UNKNOWN</td>'

                # Build Time
                page += f'<td>{datetime.utcfromtimestamp(build["start_ts"]) if build["start_ts"] else "UNKNOWN"}</td></tr>'
            else:
                if config.is_eligible("rpms", periodic.status_data[pkg]):
                    page += "<td>UNKNOWN</td><td>UNKNOWN</td><td>UNKNOWN</td>"

        page += """
</table>
</body>
</html>
"""

        return page.encode("UTF-8")


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

    return Site(root)


if __name__ == "__main__":
    # For debugging
    reactor.listenTCP(8080, setup_web_resources())
    reactor.run()
