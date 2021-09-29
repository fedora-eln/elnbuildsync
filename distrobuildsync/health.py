#!/usr/bin/env python3

from twisted.internet import reactor
from twisted.web.resource import Resource
from twisted.web.server import Site


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


def setup_health_checks():
    global started
    root = RootResource()
    root.putChild(b"startup", StartupResource())
    root.putChild(b"alive", LivenessResource())

    return Site(root)


if __name__ == "__main__":
    # For debugging
    reactor.listenTCP(8080, setup_health_checks())
    reactor.run()
