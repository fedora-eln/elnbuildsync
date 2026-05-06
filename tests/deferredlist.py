from twisted.internet import defer, reactor, task

d1 = defer.Deferred()
d2 = defer.Deferred()


@defer.inlineCallbacks
def test_deferredlist():
    results = yield defer.DeferredList([d1, d2], consumeErrors=True)
    print(results)
    reactor.stop()


def finish():
    d1.callback("Whee!")
    d2.errback(Exception("FAIL!"))


def main():
    task.deferLater(reactor, 1, test_deferredlist)
    task.deferLater(reactor, 5, finish)
    reactor.run()


if __name__ == "__main__":
    main()
