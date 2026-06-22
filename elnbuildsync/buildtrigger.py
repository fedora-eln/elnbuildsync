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
from datetime import datetime, timezone

from sqlalchemy.sql.expression import select

from . import config, db_models
from .decorators import as_deferred

logger = logging.getLogger(__name__)


class BuildTrigger:
    # Most often this will be initialized from a message received from the AMQP queue.
    # Tag JSON samples:
    # https://apps.fedoraproject.org/datagrepper/v2/search?topic=org.fedoraproject.prod.buildsys.tag

    def __init__(self, component: str, build_id: int) -> None:
        """
        Do not call BuildTrigger() alone. Instantiate via
        `await BuildTrigger(component, build_id).async_init()` instead. This
        ensures that the database entry is created before the object is used.
        :param component: The name of the component that was tagged
        :param build_id: The ID of the build that was tagged
        """
        self.component = component
        self.build_id = build_id
        self.scmurl = None

        # Database object
        self._db_obj = None

    @property
    def id(self):
        return self._db_obj.id

    @as_deferred
    async def async_init(self):
        async with db_models.async_session() as session:
            db_build_trigger = db_models.DBBuildTrigger(
                component=self.component,
                build_id=self.build_id,
            )
            session.add(db_build_trigger)
            await session.commit()
            logger.debug(f"BuildTrigger DB ID: {db_build_trigger.id}")
            self._db_obj = db_build_trigger

        return self

    @as_deferred
    async def drop(self):
        async with db_models.async_session() as session:
            session.delete(self._db_obj)
            await session.commit()

    async def get_scmurl(self):
        """Get the SCMURL that the build was created from

        :returns: A string containing the full, dereferenced SCMURL for the build
        """
        # Imported here to avoid circular import: builds → listener → buildtrigger.
        from .kojihelpers.builds import get_buildinfo

        # Store the SCM URL to avoid multiple retrievals.
        if self.scmurl is None:
            logger.debug(f"Retrieving SCM URL for {self.build_id}")
            try:
                buildinfo = await get_buildinfo(self.build_id)
            except Exception:
                logger.exception("Unexpected error retrieving SCM URL")
                raise
            self.scmurl = buildinfo["source"]

        if self.scmurl is None:
            raise ValueError(f"SCM URL for {self.build_id} is not available")

        return self.scmurl

    @staticmethod
    @as_deferred
    async def get_unprocessed_build_triggers():
        build_triggers = dict[str, BuildTrigger]()
        to_drop = list[BuildTrigger]()
        async with db_models.async_session() as session:
            result = await session.stream(
                select(db_models.DBBuildTrigger)
                .where(db_models.DBBuildTrigger.completed_at.is_(None))
                .order_by(db_models.DBBuildTrigger.created_at.asc())
                .execution_options(yield_per=config.main["db"]["page_size"])
            )
            async for db_build_trigger in result.scalars():
                # If this component already has a build trigger, drop the older one.
                # We only want to rebuild the most recent build for each component.
                # (OR do we want to build both, but in different slices?)
                if db_build_trigger.component in build_triggers:
                    # Save the build trigger to drop later, so we aren't
                    # modifying the dictionary while iterating over it.
                    to_drop.append(build_triggers[db_build_trigger.component])
                    del build_triggers[db_build_trigger.component]

                build_trigger = BuildTrigger(
                    component=db_build_trigger.component,
                    build_id=db_build_trigger.build_id,
                )
                build_trigger._db_obj = db_build_trigger
                build_triggers[db_build_trigger.component] = build_trigger

        # If we have any expired build triggers to drop, do so now.
        for build_trigger in to_drop:
            await build_trigger.drop()

        return list(build_triggers.values())

    @as_deferred
    async def mark_completed(self):
        async with db_models.async_session() as session:
            self._db_obj.completed_at = datetime.now(timezone.utc)
            session.add(self._db_obj)
            await session.commit()
