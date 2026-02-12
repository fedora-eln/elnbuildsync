#!/usr/bin/python3

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
import pathlib
import pytest
import sys

logger = logging.getLogger(__name__)

import elnbuildsync

script_dir = pathlib.Path(__file__).parent


def test_config_file(requests_mock):
    requests_mock.get(
        "https://tiny.distro.builders/view-buildroot-source-package-name-list--view-eln-extras.txt",
        text="packageA\nkernel",
    )
    requests_mock.get(
        "https://tiny.distro.builders/view-source-package-name-list--view-eln-extras.txt",
        text="packageB\ntexlive-base",
    )
    requests_mock.get(
        "https://tiny.distro.builders/view-buildroot-source-package-name-list--view-eln.txt",
        text="packageC",
    )
    requests_mock.get(
        "https://tiny.distro.builders/view-source-package-name-list--view-eln.txt",
        text="packageD",
    )

    # Read in the main config file
    try:
        elnbuildsync.config.load_config(config_file=f"{script_dir}/testconfig.yaml")
    except Exception as e:
        logger.exception(e)
        logger.critical("Could not load configuration.")
        sys.exit(128)

    assert elnbuildsync.config.main
    assert "build_system" in elnbuildsync.config.main
    assert "profile" in elnbuildsync.config.main["build_system"]
    assert elnbuildsync.config.main["build_system"]["profile"] == "koji"

    assert "trigger" in elnbuildsync.config.main
    assert "rpms" in elnbuildsync.config.main["trigger"]
    assert elnbuildsync.config.main["trigger"]["rpms"] == "f40"

    assert "build" in elnbuildsync.config.main
    assert "target" in elnbuildsync.config.main["build"]
    assert elnbuildsync.config.main["build"]["target"] == "eln"

    assert "scratch" in elnbuildsync.config.main["build"]
    assert not elnbuildsync.config.main["build"]["scratch"]

    assert elnbuildsync.config.comps
    assert "rpms" in elnbuildsync.config.comps
    assert "packageA" in elnbuildsync.config.comps["rpms"]
    assert "packageB" in elnbuildsync.config.comps["rpms"]
    assert "packageC" in elnbuildsync.config.comps["rpms"]
    assert "packageD" in elnbuildsync.config.comps["rpms"]

    # TODO: Trim out excluded packages
    # assert "kernel" not in elnbuildsync.config.comps["rpms"]
    # assert "texlive-base" not in elnbuildsync.config.comps["rpms"]


def main():
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=logging.CRITICAL,
    )
    logger.setLevel(logging.DEBUG)
    logger.debug("Debug logging enabled")

    # The elnbuildsync module has its own logger
    ebs_logger = logging.getLogger("elnbuildsync")
    ebs_logger.setLevel(logging.DEBUG)
    ebs_logger.debug("Debug logging enabled")

    test_config_file()


if __name__ == "__main__":
    print("Run with pytest", file=sys.stderr)
