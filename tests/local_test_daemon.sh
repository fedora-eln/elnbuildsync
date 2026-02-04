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

# SPDX-License-Identifier: 	GPL-3.0-or-later

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJ_DIR=$SCRIPT_DIR/..

if [ -r /run/.containerenv ]; then
  CONTAINER_ENGINE=/usr/bin/podman-remote
else
  CONTAINER_ENGINE=/usr/bin/podman
fi

cd $PROJ_DIR

sudo dnf -y install postgresql
# htmlmin requires the cgi module which was removed from stdlib, but hasn't been updated to declare it
pip install legacy-cgi
pip install --no-build-isolation htmlmin
pip install -r requirements.txt
pip install --editable $PROJ_DIR

function check_db_avail() {
    PGPASSWORD=$(cat tests/ebs_db_pw) \
    pg_isready --quiet \
               --dbname elnbuildsync \
               --username elnbuildsync \
               --host localhost \
               --port 5432
    db_ready=$?
}

function closedb() {
    echo "Terminating non-persistent database"
    ${CONTAINER_ENGINE} stop temp_postgres
}

# Check if there's already a database running
check_db_avail > /dev/null 2>&1

if [ $db_ready -ne 0 ]; then
    # Start a non-persistent database for testing
    echo "Starting up non-persistent database container"
    ${CONTAINER_ENGINE} pull docker.io/postgres:18
    echo ${CONTAINER_ENGINE} run --publish 5432:5432 --rm --detach \
        --volume ${SCRIPT_DIR}/ebs_db_pw:/run/secrets/ebs_db_pw:Z \
        --name temp_postgres \
        --env POSTGRES_PASSWORD_FILE=/run/secrets/ebs_db_pw \
        --env POSTGRES_USER=elnbuildsync \
        docker.io/postgres:18
    ${CONTAINER_ENGINE} run --publish 5432:5432 --rm --detach \
        --volume ${SCRIPT_DIR}/ebs_db_pw:/run/secrets/ebs_db_pw:Z \
        --name temp_postgres \
        --env POSTGRES_PASSWORD_FILE=/run/secrets/ebs_db_pw \
        --env POSTGRES_USER=elnbuildsync \
        docker.io/postgres:18
    trap closedb EXIT

    echo Started database container, waiting for it to complete initialization

    # Wait for the DB to start up
    db_ready=-1
    while [ $db_ready -ne 0 ]; do
        sleep 0.1
        check_db_avail
    done
fi

FEDORA_MESSAGING_CONF="$SCRIPT_DIR/fedora-messaging/fedora.toml" \
~/.local/bin/elnbuildsync \
    --log-level info \
    --lull-time 5 \
    --config-file $SCRIPT_DIR/testconfig.yaml \
    --db-pw-file $SCRIPT_DIR/ebs_db_pw \
    $@ \
    2>&1 | tee /tmp/elnbuildsync.log
