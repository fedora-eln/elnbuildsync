#!/usr/bin/python3
# -*- coding: utf-8 -*-

# Copyright (C) 2023 by Stephen Gallagher <sgallagh@redhat.com>
# SPDX-License-Identifier: 	GPL-3.0-or-later

from twisted.internet import reactor, task

from . import web


def main():
    reactor.listenTCP(8080, web.setup_web_resources())
    reactor.run()
    pass


if __name__ == '__main__':
    main()
