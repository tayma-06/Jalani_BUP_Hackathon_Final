"""Tests for the automation code. These are not official-simulator integration evidence."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from app_smoke import smoke
from http_checks import code
from manifest import env_text
from simulator_contract import candidate
from verify_images import check_revision


class ManifestTests(unittest.TestCase):
    def good(self):
        return {"backend_image": "ghcr.io/team/backend@sha256:" + "a" * 64,
                "frontend_image": "ghcr.io/team/frontend@sha256:" + "b" * 64,
                "git_sha": "c" * 40, "version": "v1.2.3"}

    def test_pair_and_sha_are_preserved(self):
        text = env_text(self.good())
        self.assertIn("BACKEND_IMAGE=ghcr.io/team/backend@sha256:", text)
        self.assertIn("FRONTEND_IMAGE=ghcr.io/team/frontend@sha256:", text)
        self.assertIn("GIT_SHA=" + "c" * 40, text)

    def test_mutable_tag_and_env_injection_rejected(self):
        for bad in ("ghcr.io/team/backend:latest", "ghcr.io/team/backend@sha256:" + "a" * 64 + "\nX=1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                env_text(dict(self.good(), backend_image=bad))

    def test_bad_sha_and_version_rejected(self):
        for field, value in (("git_sha", "main"), ("version", "$(touch bad)")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                env_text(dict(self.good(), **{field: value}))

    def test_matching_baked_revision_passes(self):
        check_revision({"Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}}, "a" * 40)

    def test_wrong_baked_revision_fails(self):
        with self.assertRaises(ValueError):
            check_revision({"Config": {"Labels": {"org.opencontainers.image.revision": "b" * 40}}}, "a" * 40)


class CandidateTests(unittest.TestCase):
    def world(self):
        depot = {"id": "d", "status": "OPEN", "inventory": {"DIESEL": 80}, "dispatch_capacity_per_tick": 40}
        station = {"id": "s", "status": "OPEN", "inventory": {"DIESEL": 90}, "capacity": {"DIESEL": 100}}
        route = {"id": "r", "source_depot_id": "d", "destination_station_id": "s",
                 "status": "AVAILABLE", "max_shipment": 30}
        return depot, station, route

    def test_test_shipment_respects_smallest_headroom(self):
        d, s, r = self.world()
        body = candidate([d], [s], [r])
        self.assertEqual(body["quantity"], 5)
        self.assertEqual(body["route_id"], "r")

    def test_disrupted_route_never_chosen(self):
        d, s, r = self.world()
        r["status"] = "DISRUPTED"
        with self.assertRaises(AssertionError):
            candidate([d], [s], [r])


class SmokeTests(unittest.TestCase):
    def response(self, base, path, *args, **kwargs):
        payload = {"/": "<html>App</html>", "/api/health/live": {}, "/api/health/ready": {},
                   "/api/health": {"git_sha": "a" * 40, "mode": "NORMAL"},
                   "/api/network/state": {"stale": False, "mode": "NORMAL", "data_age_s": 1,
                                           "stations": [{"id": "s"}], "tick": 10},
                   "/api/recommendations": []}[path]
        return 200, {}, payload

    @patch("app_smoke.login", return_value="test-token")
    def test_no_recommendations_can_be_a_healthy_world(self, _):
        with patch("app_smoke.request", side_effect=self.response):
            self.assertEqual(smoke("http://test", "a" * 40, "u", "p")["status"], "passed")

    @patch("app_smoke.login", return_value="test-token")
    def test_wrong_release_fails_even_when_http_is_200(self, _):
        with patch("app_smoke.request", side_effect=self.response), self.assertRaises(AssertionError):
            smoke("http://test", "b" * 40, "u", "p")

    @patch("app_smoke.login", return_value="test-token")
    def test_stale_200_response_fails_deploy(self, _):
        def stale(*args, **kwargs):
            status, headers, data = self.response(*args, **kwargs)
            if args[1] == "/api/network/state":
                data["stale"] = True
            return status, headers, data
        with patch("app_smoke.request", side_effect=stale), self.assertRaises(AssertionError):
            smoke("http://test", "a" * 40, "u", "p")

    def test_both_error_envelopes(self):
        self.assertEqual(code({"detail": {"code": "ROUTE_MISMATCH"}}), "ROUTE_MISMATCH")
        self.assertEqual(code({"error": {"code": "FAULT_INJECTED"}}), "FAULT_INJECTED")
        self.assertIsNone(code({"detail": [{"msg": "validation"}]}))


if __name__ == "__main__":
    unittest.main()
