"""The social scenarios against the CLOUDFLARE WORKER running locally under workerd (``wrangler dev``): the same
protocol, the same rules, the real Durable Object with WebSocket hibernation, real sockets and real time.

Opt-in because it starts a local Worker (about a minute): set EQU3_TEST_WORKER=1. It needs
``cloudflare/social/node_modules`` (``npm install`` there). This tests the Worker's code on this computer; it says
nothing about Cloudflare's network (see tests/test_v3_social_live.py for the deployed instance)."""

import os
import unittest

from tests.social_harness import WORKER_DIR, WorkerBackend
from tests.social_scenarios import AccountScenarios, FriendScenarios, MetricsScenarios, PresenceScenarios

HAVE = os.environ.get("EQU3_TEST_WORKER") == "1" and os.path.isdir(os.path.join(WORKER_DIR, "node_modules"))
WHY = "set EQU3_TEST_WORKER=1 and run `npm install` in cloudflare/social to test the Worker locally"
_backend = None


def setUpModule():
    global _backend
    if HAVE:
        _backend = WorkerBackend()


def tearDownModule():
    if _backend:
        _backend.stop()


class WorkerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE:
            raise unittest.SkipTest(WHY)
        cls.backend = _backend


class TestWorkerAccounts(AccountScenarios, WorkerCase):
    pass


class TestWorkerFriends(FriendScenarios, WorkerCase):
    pass


class TestWorkerPresence(PresenceScenarios, WorkerCase):
    pass


class TestWorkerMetrics(MetricsScenarios, WorkerCase):
    pass


if __name__ == "__main__":
    unittest.main()
