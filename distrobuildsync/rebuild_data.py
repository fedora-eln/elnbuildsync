from . import config
from . import kojihelpers

from collections import namedtuple


logger = config.logger


RebuildData = namedtuple(
    "RebuildData",
    [
        "ns",
        "comp",
        "version",
        "release",
        "scmurl",
        "downstream_target",
        "ref_overrides",
    ],
    defaults=[None, None],
)


def rebuild_data_from_component(namespace, component):
    logger.info("Processing {}/{}.".format(namespace, component))

    if component in config.main["control"]["exclude"][namespace]:
        raise ValueError(
            "The %s/%s component is excluded from sync, skipping.",
            namespace,
            component,
        )

    if (
        config.main["control"]["strict"]
        and component not in config.comps[namespace]
    ):
        raise ValueError(
            "The {}/{} component not configured while the strict mode is enabled, ignoring.".format(
                namespace,
                component)
        )

    nvr = kojihelpers.get_build(component, namespace)
    if not nvr:
        raise ValueError(f"The {namespace}/{component} component's build not tagged in the source Koji tag.")

    bi = kojihelpers.get_build_info(nvr["nvr"])
    scmurl = bi["scmurl"]
    ref = config.split_scmurl(scmurl)["ref"]
    if ref:
        if namespace == "modules":
            ref_overrides = kojihelpers.get_ref_overrides(bi["modulemd"])
        else:
            ref_overrides = None

    return RebuildData(namespace, component, nvr["version"], nvr["release"], scmurl, config.main["build"]["target"], ref_overrides)
