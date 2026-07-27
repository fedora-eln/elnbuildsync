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


class KojiHelperBaseError(Exception):
    pass


class KerberosAuthError(KojiHelperBaseError):
    """Kerberos TGT acquire/renew failed."""


class KojiLoginError(KojiHelperBaseError):
    """Koji GSSAPI login failed."""


class BuildSysUnavailable(KojiHelperBaseError):
    """Koji ClientSession could not be created or is unavailable."""


class InfoUnavailableError(KojiHelperBaseError):
    pass


class IneligibleBuildError(KojiHelperBaseError):
    pass


class TaskFailedError(KojiHelperBaseError):
    pass


class TaskTimeoutError(TaskFailedError):
    pass
