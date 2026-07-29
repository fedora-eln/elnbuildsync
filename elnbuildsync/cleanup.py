# This file is part of ELNBuildSync
# Copyright (C) 2023-2026 Stephen Gallagher <sgallagh@redhat.com>

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


import logging

from . import batching, config, kojihelpers
from .kojihelpers.connection import call_koji

logger = logging.getLogger(__name__)


async def periodic_cleanup():
    # Do nothing if we're paused
    if config.is_paused():
        logger.debug("Skipping periodic cleanup during pause.")
        return

    logger.debug("Starting periodic cleanup.")

    # We have the set of desired packages from Content Resolver
    desired_pkg_names = set(config.comps["downstream_components"].keys())

    # Get the list of packages currently tagged into the stable tag
    latest_tagged_dest_pkgs = await call_koji(
        "listTagged", config.main["koji"]["stable_tag"], latest=True
    )

    # Get the list of up-to-date packages in the destination tag
    # Exclude those not in the desired list, so they will be cleaned up below
    latest_tagged_dest_nvrs = {
        pkg["nvr"]
        for pkg in latest_tagged_dest_pkgs
        if pkg["name"] in desired_pkg_names
    }

    # Get the complete list of builds tagged into the stable tag
    all_tagged_dest_pkgs = await call_koji(
        "listTagged", config.main["koji"]["stable_tag"], latest=False
    )
    all_tagged_dest_nvrs = {pkg["nvr"] for pkg in all_tagged_dest_pkgs}

    # Queue up the set of old builds to untag
    nvrs_to_untag = all_tagged_dest_nvrs - latest_tagged_dest_nvrs

    if len(nvrs_to_untag) > 0:
        logger.info(f"{len(nvrs_to_untag)} builds to untag:")
        for nvr in sorted(nvrs_to_untag):
            logger.info(f"\t{nvr}")

        if config.do_untagging:
            await kojihelpers.tags.untag_builds(
                config.main["koji"]["stable_tag"], nvrs_to_untag
            )
        else:
            logger.info("Untagging is disabled, skipping untagging.")

    # Packages in the desired list but not in the tag should be built
    latest_tagged_dest_pkg_names = {pkg["name"] for pkg in latest_tagged_dest_pkgs}
    pkgs_to_build = desired_pkg_names - latest_tagged_dest_pkg_names
    await batching.rebuild_from_components(pkgs_to_build)

    logger.debug("Periodic cleanup finished.")
