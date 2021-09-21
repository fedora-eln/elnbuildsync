import logging

from lib import distrobuildsync

try:
    import unittest2 as unittest
except ImportError:
    import unittest


class TestMiscSettings(unittest.TestCase):
    def test_loglevel(self):
        self.assertIsNotNone(distrobuildsync.loglevel())
        self.assertEqual(distrobuildsync.loglevel(logging.INFO), logging.INFO)
        self.assertEqual(distrobuildsync.loglevel(), logging.INFO)
        self.assertEqual(distrobuildsync.loglevel(logging.DEBUG), logging.DEBUG)
        self.assertEqual(distrobuildsync.loglevel(), logging.DEBUG)

    def test_retries(self):
        self.assertIsNotNone(distrobuildsync.retries())
        self.assertEqual(distrobuildsync.retries(2), 2)
        self.assertEqual(distrobuildsync.retries(), 2)
        self.assertEqual(distrobuildsync.retries(3), 3)
        self.assertEqual(distrobuildsync.retries(), 3)

    def test_pretend(self):
        self.assertIsNotNone(distrobuildsync.pretend())
        self.assertEqual(distrobuildsync.pretend(True), True)
        self.assertEqual(distrobuildsync.pretend(), True)
        self.assertEqual(distrobuildsync.pretend(False), False)
        self.assertEqual(distrobuildsync.pretend(), False)


class TestMiscParsing(unittest.TestCase):
    def test_split_scmurl(self):
        self.assertDictEqual(
            distrobuildsync.split_scmurl(""),
            {"link": "", "ref": None, "ns": None, "comp": ""},
        )
        self.assertDictEqual(
            distrobuildsync.split_scmurl(
                "https://example.com/distrobuildsync.git#prod"
            ),
            {
                "link": "https://example.com/distrobuildsync.git",
                "ref": "prod",
                "ns": "example.com",
                "comp": "distrobuildsync.git",
            },
        )
        self.assertDictEqual(
            distrobuildsync.split_scmurl("conf"),
            {"link": "conf", "ref": None, "ns": None, "comp": "conf"},
        )
        self.assertDictEqual(
            distrobuildsync.split_scmurl("/tmp/conf#testbranch"),
            {
                "link": "/tmp/conf",
                "ref": "testbranch",
                "ns": "tmp",
                "comp": "conf",
            },
        )
        self.assertDictEqual(
            distrobuildsync.split_scmurl(
                "https://src.fedoraproject.org/rpms/gzip.git#rawhide"
            ),
            {
                "link": "https://src.fedoraproject.org/rpms/gzip.git",
                "ref": "rawhide",
                "ns": "rpms",
                "comp": "gzip.git",
            },
        )

    def test_split_module(self):
        self.assertDictEqual(
            distrobuildsync.split_module(""), {"name": "", "stream": "master"}
        )
        self.assertDictEqual(
            distrobuildsync.split_module(":"), {"name": "", "stream": "master"}
        )
        self.assertDictEqual(
            distrobuildsync.split_module("name"),
            {"name": "name", "stream": "master"},
        )
        self.assertDictEqual(
            distrobuildsync.split_module("name:stream"),
            {"name": "name", "stream": "stream"},
        )
        self.assertDictEqual(
            distrobuildsync.split_module(":stream"),
            {"name": "", "stream": "stream"},
        )
        self.assertDictEqual(
            distrobuildsync.split_module("name:stream:version:context"),
            {"name": "name", "stream": "stream"},
        )
