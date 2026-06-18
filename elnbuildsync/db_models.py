# This file is part of ELNBuildSync
# Copyright (C) 2024-2026 Stephen Gallagher <sgallagh@redhat.com>

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

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ForeignKey
from sqlalchemy import JSON
from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from typing import List
from typing import Optional


from .decorators import as_deferred

async_session: async_sessionmaker[AsyncSession]


def _utc_now():
    """Return current UTC time as timezone-aware datetime (for DB defaults)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DBUserSession(Base):
    """
    Stores authenticated user sessions for OpenID Connect authentication.

    Sessions are created after successful OIDC authentication and are used
    to validate subsequent requests to protected endpoints (e.g., /trigger).
    """

    __tablename__ = "user_sessions"

    # Secure random session ID (64 hex characters = 256 bits)
    session_id: Mapped[str] = mapped_column(primary_key=True)

    # The authenticated user's username (from OIDC 'nickname' or 'sub' claim)
    username: Mapped[str] = mapped_column(nullable=False)

    # The user's group memberships at time of authentication (JSON array)
    # Used to verify authorization without re-fetching from OIDC provider
    groups: Mapped[dict] = mapped_column(JSON, nullable=False)

    # When the session was created (timezone-aware UTC)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )

    # When the session expires (timezone-aware UTC)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DBBuildTrigger(Base):
    __tablename__ = "build_trigger"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # The name of the component that was tagged
    component: Mapped[str] = mapped_column(nullable=False, unique=False)

    # The ID of the build that was tagged
    build_id: Mapped[int] = mapped_column(nullable=False, unique=False)

    # The timestamp when the build trigger was created
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
    )

    # The timestamp when the build trigger was completed
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # The RebuildBatch this build trigger is associated with
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("rebuild_batch.id"), nullable=True
    )
    batch: Mapped[DBRebuildBatch | None] = relationship(back_populates="build_triggers")

    # The slice this build trigger is associated with
    slice_id: Mapped[int | None] = mapped_column(
        ForeignKey("rebuild_batch_slice.id"), nullable=True
    )
    slice: Mapped[DBRebuildBatchSlice | None] = relationship(
        back_populates="build_triggers"
    )


class DBRebuildBatch(Base):
    __tablename__ = "rebuild_batch"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Each batch generates a side-tag
    # If this is NULL, the side tag has not yet been requested
    side_tag: Mapped[str] = mapped_column(unique=True, nullable=True)

    # Once a batch completes, the built packages need to be tagged into a
    # destination tag.
    dest_tag: Mapped[str] = mapped_column(nullable=False)

    # Batches are triggered by one or more build triggers
    build_triggers: Mapped[List["DBBuildTrigger"]] = relationship(back_populates="batch")

    # Batches may be divided into one or more slices
    slices: Mapped[List["DBRebuildBatchSlice"]] = relationship(back_populates="batch")

    # The Koji build options for this batch
    # This is stored as a JSON blob
    # Example: `{ "scratch": true, "fail_fast": true }`
    options: Mapped[str] = mapped_column(nullable=False)

    # Whether this batch has concluded. This is mostly useful for knowing
    # whether to resume watching a batch at startup (such as after a crash or
    # service upgrade.
    completed: Mapped[bool] = mapped_column(nullable=False)


class DBRebuildBatchSlice(Base):
    __tablename__ = "rebuild_batch_slice"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # The ordering value of this slice
    ordering: Mapped[int] = mapped_column(nullable=False)

    # The set of build triggers being processed in this slice
    build_triggers: Mapped[List["DBBuildTrigger"]] = relationship()

    # The current state of the slice processing
    state: Mapped[int] = mapped_column(nullable=False)

    # Link back to the batch that started this attempt
    batch_id: Mapped[int] = mapped_column(ForeignKey("rebuild_batch.id"), nullable=True)
    batch: Mapped["DBRebuildBatch"] = relationship(back_populates="slices")

    # Slices may make one or more attempts
    attempts: Mapped[List["DBRebuildAttempt"]] = relationship(back_populates="slice")


class DBRebuildAttempt(Base):
    __tablename__ = "rebuild_attempt"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Link back to the batch that started this attempt
    slice_id: Mapped[int] = mapped_column(
        ForeignKey("rebuild_batch_slice.id"), nullable=False
    )
    slice: Mapped["DBRebuildBatchSlice"] = relationship(back_populates="attempts")

    # Whether this batch has concluded. This is mostly useful for knowing
    # whether to resume watching an attempt at startup (such as after a crash
    # or service upgrade.
    completed: Mapped[bool] = mapped_column(nullable=False)


@as_deferred
async def init_db(db_url, echo=False):
    global async_session

    engine = create_async_engine(db_url, echo=echo, poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    return engine
