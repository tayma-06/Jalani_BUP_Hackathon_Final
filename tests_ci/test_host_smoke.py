"""Regression checks for deployment verification, without Docker or a remote host."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import create_autospec, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import host_smoke
from app_smoke import smoke
from http_checks import TokenCache


class HostSmokeTests(unittest.TestCase):
    def test_host_retry_reuses_tokens_and_calls_current_smoke_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {"backend_image": "ghcr.io/test/backend@sha256:" + "a" * 64,
                        "frontend_image": "ghcr.io/test/frontend@sha256:" + "b" * 64,
                        "git_sha": "c" * 40, "version": "v1.0.0"}
            (root / "manifest.json").write_text(json.dumps(manifest))
            config = {"services": {"backend": {"environment": {
                "OPERATOR_USER": "operator", "OPERATOR_PASSWORD": "test-secret"}}}}
            checked = create_autospec(smoke, side_effect=[AssertionError("starting"), {"status": "passed"}])

            def retry(check, **kwargs):
                try:
                    return check()
                except AssertionError:
                    return check()

            with patch.object(host_smoke, "verify", return_value={}), \
                    patch.object(host_smoke.subprocess, "check_output", return_value=json.dumps(config)), \
                    patch.object(host_smoke, "smoke", checked), \
                    patch.object(host_smoke, "eventually", side_effect=retry):
                result = host_smoke.run(root, root, root / "evidence.json")
            self.assertEqual(result["status"], "passed")
            first, second = [call.args for call in checked.call_args_list]
            self.assertIsInstance(first[4], TokenCache)
            self.assertIs(first[4], second[4])
            self.assertNotIn("test-secret", (root / "evidence.json").read_text())

    def test_monitoring_requires_discovered_receiver(self):
        with patch.object(host_smoke, "request", side_effect=[
            (200, {}, "OK"),
            (200, {}, {"status": "success", "data": {"activeAlertmanagers": []}}),
        ]), self.assertRaisesRegex(AssertionError, "not discovered"):
            host_smoke.monitoring_smoke()

    def test_monitoring_requires_healthy_backend_scrape(self):
        for health in ("down", "up"):
            with self.subTest(health=health), patch.object(host_smoke, "request", side_effect=[
                (200, {}, "OK"),
                (200, {}, {"status": "success", "data": {"activeAlertmanagers": [
                    {"url": "http://alertmanager:9093/api/v2/alerts"}]}}),
                (200, {}, {"status": "success", "data": {"activeTargets": [
                    {"labels": {"job": "jalani-backend"}, "health": health}]}}),
            ]):
                if health == "down":
                    with self.assertRaisesRegex(AssertionError, "scrape is unhealthy"):
                        host_smoke.monitoring_smoke()
                else:
                    self.assertEqual(host_smoke.monitoring_smoke()["status"], "passed")


if __name__ == "__main__":
    unittest.main()
