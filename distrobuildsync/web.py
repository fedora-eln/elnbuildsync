#!/usr/bin/env python3

from twisted.internet import reactor
from twisted.web.resource import Resource
from twisted.web.server import Site

from datetime import datetime
from koji import BUILD_STATES

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
        page = """

<html>
<head><title>ELN Rebuild Status</title></head>
<body>
<h1>ELN Rebuild Status</h1>
</p>
<table border=1 style=\"width:100%\">
<tr><th>Name</th><th>STATUS</th><th>NVR</th><th>Build Time</th></tr>
"""
        for pkg in sorted(periodic.status_data.keys()):
            build = periodic.status_data[pkg]

            page += f'<tr><td>{pkg}</td>'
            if build is not None:
                if build["state"] == BUILD_STATES["COMPLETE"]:
                    page += "<td bgcolor=\"#00FF00\">COMPLETE</td>"
                elif build["state"] == BUILD_STATES["BUILDING"]:
                    page += "<td bgcolor=\"#00FFFF\">BUILDING</td>"
                elif build["state"] == BUILD_STATES["FAILED"]:
                    page += "<td bgcolor=\"#FF0000\">FAILED</td>"
                else:
                    page += "<td bgcolor=\"#FF00FF\">UNKNOWN</td>"

                page += f'<td>{build["nvr"]}</td>'
                page += f'<td>{datetime.utcfromtimestamp(build["start_ts"]) if build["start_ts"] else "UNKNOWN"}</td></tr>'
            else:
                page += "<td>UNKNOWN</td><td>UNKNOWN</td><td>UNKNOWN</td>"

        page += """
</table>
</body>
</html>
"""

        return page.encode("UTF-8")

def setup_web_resources():
    global started
    root = RootResource()
    root.putChild(b"startup", StartupResource())
    root.putChild(b"alive", LivenessResource())
    root.putChild(b"status", StatusPageResource())

    return Site(root)


if __name__ == "__main__":
    # For debugging
    reactor.listenTCP(8080, setup_web_resources())
    reactor.run()
