import time

from koji import BUILD_STATES

from twisted.python.filepath import FilePath
from twisted.web.resource import Resource
from twisted.web.template import Element, renderElement, renderer, TagLoader, XMLFile

status_data = {
        "MUMPS": {
            "state": BUILD_STATES["COMPLETE"],
            "nvr": "MUMPS-5.5.1-1.eln121",
        },
        "sscg":  {
            "state": BUILD_STATES["COMPLETE"],
            "nvr": "sscg-3.0.2-9.eln120",
        },
    }


class StatusTableElement(Element):
    loader = XMLFile(FilePath("status.xml"))

    @renderer
    def builds(self, request, tag):
        for pkg in sorted(status_data.keys()):
            build = status_data[pkg]

            if build["state"] == BUILD_STATES["COMPLETE"]:
                color = "#00FF00"
                state = "COMPLETE"

            yield tag.clone().fillSlots(
                name=pkg,
                nvr=build["nvr"],
                bgcolor=color,
                state=state,
            )


class StatusResource(Resource):
    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        return renderElement(request, StatusTableElement())


resource = StatusResource()
