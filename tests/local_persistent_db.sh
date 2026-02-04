#!/usr/bin/env bash

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

# SPDX-License-Identifier: GPL-3.0-or-later


SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJ_DIR=$SCRIPT_DIR/..

pg_data_dir=$SCRIPT_DIR/persistent_db

mkdir -p $pg_data_dir
echo "Starting up persistent database container"
podman pull postgres:15
podman run --publish 5432:5432 --rm --detach \
    --volume ${SCRIPT_DIR}/ebs_db_pw:/run/secrets/ebs_db_pw:Z \
    --volume ${pg_data_dir}:/var/lib/postgresql/data \
    --name persistent_postgres \
    --env POSTGRES_PASSWORD_FILE=/run/secrets/ebs_db_pw \
    --env POSTGRES_USER=elnbuildsync \
    postgres:15
