"""Authorization and viewer lifecycle checks without external media services."""

import asyncio
import hashlib
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from cloud import server
from app.cloud_edge import public_code


class CloudPreviewTest(unittest.TestCase):
    def setUp(self):
        server.active.clear()
        server.leases.clear()
        server.connections.clear()

    def tearDown(self):
        server.active.clear()
        server.leases.clear()
        server.connections.clear()

    def test_media_tokens_are_bound_to_action_and_path(self):
        allowed = {("hash", "camera-1/front", "read")}

        def fake_query(_sql, args=(), **_kwargs):
            return (1,) if args in allowed else None

        with patch.object(server, "query", fake_query), patch.object(server, "digest", return_value="hash"):
            self.assertEqual(server.media_auth({"action": "read", "path": "media/camera-1/front",
                                                 "user": "viewer", "password": "valid"}), {"ok": True})
            for payload in (
                {"action": "publish", "path": "media/camera-1/front", "user": "edge", "password": "valid"},
                {"action": "read", "path": "media/camera-1/back", "user": "viewer", "password": "valid"},
                {"action": "read", "path": "media/camera-1/front", "user": "edge", "password": "valid"},
            ):
                with self.assertRaises(HTTPException) as error:
                    server.media_auth(payload)
                self.assertEqual(error.exception.status_code, 401)

    def test_two_viewers_share_one_edge_upload(self):
        calls = []

        async def fake_start(key):
            calls.append(key)
            server.active[key] = {"last_viewer": 0, "state": "starting"}

        def fake_query(sql, args=(), **_kwargs):
            if sql.startswith("SELECT 1 FROM sources"):
                return (1,)
            return None

        async def scenario():
            first = await server.view("camera-1", "front", authorization="Bearer valid")
            second = await server.view("camera-1", "front", authorization="Bearer valid")
            self.assertNotEqual(first["lease_id"], second["lease_id"])
            self.assertNotEqual(first["read_token"], second["read_token"])
            self.assertEqual(calls, ["camera-1/front"])
            server.stop_view(first["lease_id"], authorization="Bearer valid")
            self.assertIn(second["lease_id"], server.leases)

        with patch.object(server, "authorize"), patch.object(server, "query", fake_query), \
             patch.object(server, "start_stream", fake_start):
            server.connections["camera-1"] = object()
            asyncio.run(scenario())

    def test_non_url_safe_local_source_has_stable_public_code(self):
        code = public_code("现场/入口摄像机")
        self.assertEqual(code, "source_" + hashlib.sha256("现场/入口摄像机".encode()).hexdigest()[:32])
        self.assertEqual(public_code("front_1"), "front_1")


if __name__ == "__main__":
    unittest.main()
