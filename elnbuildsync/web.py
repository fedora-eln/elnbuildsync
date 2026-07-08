#!/usr/bin/env python3

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

import asyncio
import json
import logging
import os
import secrets

from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.web.error import Error as WebError
from twisted.web.resource import Resource
from twisted.web.server import Site, NOT_DONE_YET
from twisted.web.util import Redirect

from . import auth
from . import batching
from . import config
from . import status

logger = logging.getLogger(__name__)

# Store OIDC state tokens temporarily (in production, consider using Redis/DB)
# Maps state -> {"redirect_uri": str, "return_to": str}
_oidc_state_store = {}


# Globals
started = False
alive = True


class RootResource(Resource):
    def getChild(self, name, request):
        if name == b"":
            return Redirect(b"/status.html")
        return Resource.getChild(self, name, request)


class StartupResource(Resource):
    """
    StartupResource

    Returns either a 200 or a 503 response code, depending on whether
    the configuration has been loaded successfully.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        request.setHeader("Cache-Control", "no-cache")
        if not started:
            request.setResponseCode(503)
        return b"started"


class LivenessResource(Resource):
    """
    LivenessResource

    Returns either a 200 or a 500 response code or will time out if the server is deadlocked.

    Certain failures can set the 'alive' variable to False to indicate an unrecoverable error.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):

        request.setHeader("Cache-Control", "no-cache")
        if not alive:
            request.setResponseCode(500)
        return b"alive"


class StatusJSONResource(Resource):
    """
    StatusJSONResource

    Returns either a 200 or 503 response code, depending on whether the first
    periodic status update has completed successfully.

    Outputs the full status data as a JSON document
    """

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def render_GET(self, request):
        request.setHeader("Content-Type", "application/json")
        request.setHeader("Cache-Control", "no-cache")
        if not status.encoded_json_data:
            request.setResponseCode(503)
            return b""

        return status.encoded_json_data


class StatusPageResource(Resource):
    """
    StatusPageResource

    Returns a static HTML page that fetches /status.json and renders the
    build status table. Publicly accessible.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    def _done(self, data):
        if not self.request.finished:
            self.request.finish()

    def _failed(self, failure):
        logger.error(f"Status page failed: {failure}")
        if not self.request.finished:
            self.request.setResponseCode(500)
            self.request.write(b"Internal server error")
            self.request.finish()

    def render_GET(self, request):
        self.request = request

        deferred = Deferred.fromFuture(asyncio.ensure_future(self._do_get()))
        deferred.addCallback(self._done)
        deferred.addErrback(self._failed)
        return NOT_DONE_YET

    async def _do_get(self):
        request = self.request

        request.setHeader("Content-Type", "text/html; charset=utf-8")
        request.setHeader("Cache-Control", "no-cache")

        template_path = os.path.join(
            os.path.dirname(__file__), "templates", "status.html"
        )
        try:
            with open(template_path, "rb") as f:
                request.write(f.read())
        except OSError as e:
            logger.exception("Failed to read status template: %s", e)
            request.setResponseCode(500)
            request.write(b"Status page template not available")


class ProtectedResource(Resource):
    """
    Base resource for admin-only endpoints protected by OpenID Connect.

    Subclasses must implement _handle_get(user) and optionally
    _handle_post(user) for GET and POST requests, respectively.
    """

    def _done(self, data):
        if not self.request.finished:
            self.request.finish()

    def _failed(self, failure):
        logger.error(failure)
        if not self.request.finished:
            self.request.setResponseCode(500)
            self.request.write(b"Internal server error")
            self.request.finish()

    def _run_async(self, coro):
        deferred = Deferred.fromFuture(asyncio.ensure_future(coro))
        deferred.addCallback(self._done)
        deferred.addErrback(self._failed)
        return NOT_DONE_YET

    def render_GET(self, request):
        self.request = request
        return self._run_async(self._do_get())

    async def _require_user(self, *, method=None):
        request = self.request
        if method is None:
            method = request.method.decode("utf-8").upper()

        user = await _check_request_auth(request)
        if user is None:
            if method == "GET":
                _redirect_to_login(request)
            else:
                request.setResponseCode(401)
                request.write(b"Authentication required\n")
            return None

        if auth.check_group_membership(user["groups"]):
            return user

        admin_groups = config.main["open_id_connect"]["admin_groups"]
        request.setResponseCode(403)
        request.setHeader("Content-Type", "text/html; charset=utf-8")
        request.write(
            (
                "Access denied. You must be a member of one of these admin groups: "
                f"{', '.join(admin_groups)}"
            ).encode()
        )
        return None

    async def _do_get(self):
        user = await self._require_user()
        if user is None:
            return
        await self._handle_get(user)

    async def _handle_get(self, user):
        raise NotImplementedError

    def render_POST(self, request):
        self.request = request
        return self._run_async(self._do_post())

    async def _do_post(self):
        request = self.request
        request.setHeader("Cache-Control", "no-cache")

        user = await self._require_user()
        if user is None:
            return

        await self._handle_post(user)

    async def _handle_post(self, user):
        raise NotImplementedError


class TriggerBuildResource(ProtectedResource):
    """
    TriggerBuildResource

    Accepts a POST request containing a JSON list of components to rebuild for
    ELN. This endpoint requires authentication if OpenID Connect is configured.
    The components are expected to be provided as their downstream names.
    """

    isLeaf = True

    def getChild(self, name, request):
        if name == "":
            return self
        return Resource.getChild(self, name, request)

    async def _handle_get(self, user):
        """Show a simple form or info page for the trigger endpoint."""
        request = self.request

        request.setHeader("Content-Type", "text/html; charset=utf-8")
        request.setHeader("Cache-Control", "no-cache")

        # Optional: show authorization token for use with curl (Bearer header)
        show_token = request.args.get(b"show_token", [b""])[0] in (
            b"1",
            b"true",
            b"yes",
        )
        token_block = ""
        if show_token:
            session_id = auth.get_bearer_token(request) or auth.get_session_cookie(
                request
            )
            if session_id:
                trigger_url = _get_base_url(request) + "/trigger"
                curl_example = (
                    f'curl -X POST -H "Content-Type: application/json" '
                    f'-H "Authorization: Bearer {session_id}" '
                    f'-d \'["bash", "glibc"]\' {trigger_url}'
                )
                token_block = f"""
<h2>Authorization token for curl</h2>
<p>Use this token in the <code>Authorization: Bearer</code> header:</p>
<pre style="background:#f5f5f5; padding: 0.5em; overflow-x: auto;">{session_id}</pre>
<h2>Example curl command</h2>
<pre style="background:#f5f5f5; padding: 0.5em; overflow-x: auto;">{curl_example}</pre>
<p><a href="/trigger">Hide token</a></p>
"""
            else:
                token_block = "<p>Could not determine session token.</p>"
        else:
            token_block = '<p><a href="/trigger?show_token=1">Display authorization token for curl</a></p>'

        html = f"""<!DOCTYPE html>
<html>
<head><title>ELN Build Trigger</title></head>
<body>
<h1>ELN Build Trigger</h1>
<p>Logged in as: <strong>{user["username"]}</strong></p>
<p>Groups: {", ".join(user["groups"])}</p>
<p>To trigger builds, POST a JSON array of downstream component names to this endpoint.</p>
{token_block}
<p><a href="/logout">Logout</a></p>
</body>
</html>"""
        request.write(html.encode())

    async def _handle_post(self, user):
        request = self.request

        logger.info(f"Build trigger request from user {user['username']}")

        if not started or config.is_paused():
            request.setResponseCode(503)
            return

        content_type = request.getHeader("Content-Type")
        if not content_type or content_type != "application/json":
            request.setResponseCode(415)
            request.write(b"Unsupported Content-Type\n")
            raise WebError("Invalid Content-Type")

        # Read in the content
        try:
            components = json.load(request.content)
        except json.decoder.JSONDecodeError as e:
            logger.exception(e)
            raise

        request.write(f"User {user['username']} requesting builds of:\n".encode())
        for comp in sorted(components):
            request.write(f"{comp}\n".encode())

        reactor.callLater(0, _build_from_components, components)


def _build_from_components(components):
    # Wrap this call into a Deferred so we can fire-and-forget it in the
    # mainloop
    Deferred.fromCoroutine(batching.rebuild_from_components(components))


class LogLevelResource(Resource):
    """
    LogLevelResource

    Sets the log level of the application or returns 400 if an invalid log
    level is specified. This endpoint requires authentication if OpenID Connect
    is configured, and admin group membership when auth is enabled.
    """

    def getChild(self, name, request):
        return LogLevelPage(name)


class LogLevelPage(ProtectedResource):
    def __init__(self, name):
        super().__init__()
        self.loglevel = name.decode("UTF-8").upper()

    async def _handle_get(self, user):
        request = self.request
        request.setHeader("Cache-Control", "no-cache")

        try:
            logging.getLogger().setLevel(self.loglevel)
        except ValueError:
            request.setResponseCode(400)
            request.write(f"Invalid log level: {self.loglevel}\n".encode())
            return

        logger.critical(
            "Log level changed to %s by user %s",
            self.loglevel,
            user["username"],
        )
        request.write(f"Log level set to {self.loglevel}\n".encode())


# =============================================================================
# OpenID Connect Authentication Resources
# =============================================================================


def _get_base_url(request) -> str:
    """Extract the base URL from a request for building redirect URIs."""
    host = request.getHeader("Host")
    if not host:
        host = request.getHost().host
        port = request.getHost().port
        if port not in (80, 443):
            host = f"{host}:{port}"

    # Check for X-Forwarded-Proto header (behind reverse proxy)
    proto = request.getHeader("X-Forwarded-Proto")
    if not proto:
        proto = "https" if request.isSecure() else "http"

    return f"{proto}://{host}"


async def _check_request_auth(request):
    """
    Check if the request is authenticated.

    Accepts either the session cookie or Authorization: Bearer <session_id>
    (the session ID can be used as a Bearer token for curl/API usage).

    Returns:
        dict with user info if authenticated, None otherwise.
        When auth is not configured, returns anonymous user dict.
    """
    if not auth.is_auth_enabled():
        return {"username": "anonymous", "groups": []}

    session_id = auth.get_bearer_token(request) or auth.get_session_cookie(request)
    if not session_id:
        return None

    return await auth.validate_session(session_id)


def _redirect_to_login(request):
    """Redirect unauthenticated user to login page."""
    return_to = request.uri.decode("utf-8")
    login_url = f"/login?return_to={return_to}"
    request.redirect(login_url.encode())
    request.finish()
    return NOT_DONE_YET


class OIDCContainerResource(Resource):
    """Container resource for /oidc/* endpoints."""

    def getChild(self, name, request):
        if name == b"callback":
            return OIDCCallbackResource()
        return Resource.getChild(self, name, request)


class LoginResource(Resource):
    """
    LoginResource

    Initiates the OpenID Connect authentication flow by redirecting
    the user to the OIDC provider's authorization endpoint.
    """

    isLeaf = True

    def render_GET(self, request):
        if not auth.is_auth_enabled():
            request.setResponseCode(404)
            return b"Authentication not configured"

        # Get the return URL (where to redirect after login)
        return_to = request.args.get(b"return_to", [b"/status.html"])[0].decode("utf-8")

        # Build the callback URL
        base_url = _get_base_url(request)
        redirect_uri = f"{base_url}/oidc/callback"

        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)
        _oidc_state_store[state] = {
            "redirect_uri": redirect_uri,
            "return_to": return_to,
        }

        # Build and redirect to authorization URL
        auth_url = auth.build_authorization_url(redirect_uri, state)
        request.redirect(auth_url.encode())
        request.finish()
        return NOT_DONE_YET


class OIDCCallbackResource(Resource):
    """
    OIDCCallbackResource

    Handles the callback from the OIDC provider after user authentication.
    Exchanges the authorization code for tokens, fetches user info,
    validates group membership, and creates a session.
    """

    isLeaf = True

    def _done(self, data):
        pass  # Request already finished in async handler

    def _failed(self, failure):
        logger.error(f"OIDC callback failed: {failure}")
        if not self.request.finished:
            self.request.setResponseCode(500)
            self.request.write(b"Authentication failed")
            self.request.finish()

    def render_GET(self, request):
        self.request = request

        deferred = Deferred.fromFuture(asyncio.ensure_future(self._handle_callback()))
        deferred.addCallback(self._done)
        deferred.addErrback(self._failed)
        return NOT_DONE_YET

    async def _handle_callback(self):
        request = self.request

        # Check for error response from OIDC provider
        error = request.args.get(b"error", [None])[0]
        if error:
            error_desc = request.args.get(b"error_description", [b"Unknown error"])[0]
            logger.error(f"OIDC error: {error.decode()} - {error_desc.decode()}")
            request.setResponseCode(401)
            request.write(f"Authentication failed: {error_desc.decode()}".encode())
            request.finish()
            return

        # Get the authorization code and state
        code = request.args.get(b"code", [None])[0]
        state = request.args.get(b"state", [None])[0]

        if not code or not state:
            request.setResponseCode(400)
            request.write(b"Missing code or state parameter")
            request.finish()
            return

        state = state.decode("utf-8")
        code = code.decode("utf-8")

        # Validate state (CSRF protection)
        state_data = _oidc_state_store.pop(state, None)
        if not state_data:
            logger.warning("Invalid or expired OIDC state")
            request.setResponseCode(400)
            request.write(b"Invalid or expired state parameter")
            request.finish()
            return

        redirect_uri = state_data["redirect_uri"]
        return_to = state_data["return_to"]

        try:
            # Exchange code for tokens
            token_response = await auth.exchange_code_for_token(code, redirect_uri)
            access_token = token_response.get("access_token")

            if not access_token:
                raise auth.OIDCError("No access token in response")

            # Fetch user info
            user_info = await auth.get_user_info(access_token)
            username = user_info.get("nickname") or user_info.get("sub")
            groups = user_info.get("groups", [])

            logger.info(f"User {username} authenticated with groups: {groups}")

            # Create session for any authenticated user (admin_groups checked per-endpoint)
            session_id = await auth.create_session(username, groups)

            # Set session cookie
            # Use secure=False for development (localhost), True for production
            is_secure = (
                request.isSecure() or request.getHeader("X-Forwarded-Proto") == "https"
            )
            auth.set_session_cookie(request, session_id, secure=is_secure)

            # Redirect to original destination
            request.redirect(return_to.encode())
            request.finish()

        except auth.OIDCError as e:
            logger.error(f"OIDC error during callback: {e}")
            request.setResponseCode(500)
            request.write(b"Authentication failed. Please try again.")
            request.finish()

        except Exception as e:
            logger.exception(f"Unexpected error during OIDC callback: {e}")
            request.setResponseCode(500)
            request.write(b"An unexpected error occurred")
            request.finish()


class LogoutResource(Resource):
    """
    LogoutResource

    Destroys the user's session and clears the session cookie.
    """

    isLeaf = True

    def _done(self, data):
        pass

    def _failed(self, failure):
        logger.error(f"Logout failed: {failure}")
        if not self.request.finished:
            self.request.setResponseCode(500)
            self.request.write(b"Logout failed")
            self.request.finish()

    def render_GET(self, request):
        self.request = request

        deferred = Deferred.fromFuture(asyncio.ensure_future(self._handle_logout()))
        deferred.addCallback(self._done)
        deferred.addErrback(self._failed)
        return NOT_DONE_YET

    async def _handle_logout(self):
        request = self.request

        session_id = auth.get_session_cookie(request)
        if session_id:
            await auth.delete_session(session_id)

        auth.clear_session_cookie(request)

        # Redirect to home or a logout confirmation page
        return_to = request.args.get(b"return_to", [b"/"])[0]
        request.redirect(return_to)
        request.finish()


def setup_web_resources():
    global started
    root = RootResource()
    root.putChild(b"startup", StartupResource())
    root.putChild(b"alive", LivenessResource())
    root.putChild(b"loglevel", LogLevelResource())
    root.putChild(b"status.json", StatusJSONResource())
    root.putChild(b"status.html", StatusPageResource())
    root.putChild(b"status", Redirect(b"status.html"))
    root.putChild(b"trigger", TriggerBuildResource())

    # OpenID Connect authentication endpoints
    root.putChild(b"login", LoginResource())
    root.putChild(b"logout", LogoutResource())
    root.putChild(b"oidc", OIDCContainerResource())

    started = True

    return Site(root)


if __name__ == "__main__":
    # For debugging
    logging.basicConfig(
        format="%(asctime)s : %(name)s : %(levelname)s : %(message)s",
        level=logging.DEBUG,
    )
    reactor.listenTCP(8080, setup_web_resources())
    reactor.run()
