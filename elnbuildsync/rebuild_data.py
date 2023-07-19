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
    logger.debug("Processing {}/{}.".format(namespace, component))

    if component in config.main["control"]["exclude"][namespace]:
        raise ValueError(
            "The %s/%s component is excluded from sync, skipping.",
            namespace,
            component,
        )

    if config.main["control"]["strict"] and component not in config.comps[namespace]:
        raise ValueError(
            "The {}/{} component not configured while the strict mode is enabled, ignoring.".format(
                namespace, component
            )
        )

    nvr = kojihelpers.get_build(component, namespace)
    if not nvr:
        raise ValueError(
            f"The {namespace}/{component} component's build not tagged in the source Koji tag."
        )

    bi = kojihelpers.get_build_info(nvr["nvr"])
    scmurl = bi["scmurl"]
    ref = config.split_scmurl(scmurl)["ref"]
    if ref:
        if namespace == "modules":
            ref_overrides = kojihelpers.get_ref_overrides(bi["modulemd"])
        else:
            ref_overrides = None

    target = config.comps[namespace].get(component, {}).get("target")
    if not target:
        target = config.main["build"]["target"]

    return RebuildData(
        namespace,
        component,
        nvr["version"],
        nvr["release"],
        scmurl,
        target,
        ref_overrides,
    )


def build_components(target, builds):
    bsys = kojihelpers.get_buildsys("destination")
    prefix = config.main["build"]["prefix"]
    if not target:
        target = config.main["build"]["target"]

    with bsys.multicall(batch=config.koji_batch) as mc:
        for rd in builds:
            if config.is_eligible(rd.ns, rd.comp):
                namespace = rd.ns
                scmurl = config.split_scmurl(rd.scmurl)
                gitcomponent = scmurl["comp"]
                ref = scmurl["ref"]
                downstream_scmurl = f"{prefix}/{namespace}/{gitcomponent}#{ref}"

                dry = "DRY-RUN: " if config.dry_run else ""
                scratch = "Scratch-b" if config.main["build"]["scratch"] else "B"
                logger.info(f"{dry}{scratch}uilding {downstream_scmurl} for {target}")

                if not config.dry_run:
                    kojihelpers.call_distrogitsync(
                        namespace, gitcomponent, rd.ref_overrides
                    )
                    mc.build(
                        downstream_scmurl,
                        target,
                        {"scratch": config.main["build"]["scratch"]},
                        priority=kojihelpers.KOJI_BACKGROUND_PRIORITY,
                    )
