"""Host-side regression tests; no device or external network is required."""

import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

with patch.dict(sys.modules, {"ujson": json, "urequests": types.ModuleType("urequests")}):
    from uploader import sensor_community as sc
from umqtt.simple import MQTTClient


class SensorCommunityTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.requests = Mock()
        self.requests.post.return_value.status_code = 201
        for name, value in (("requests", self.requests), ("pdiag", Mock()),
                            ("_tcp_probe", Mock(return_value=True)),
                            ("socket", types.SimpleNamespace())):
            patcher = patch.object(sc, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def uploader(self, timeout):
        return sc.SensorCommunityUploader({"external": {"sensor_community": {
            "enabled": True, "sc_socket_timeout_s": timeout,
        }}}, {})

    def test_http_timeout_applies_without_global_socket_default(self):
        self.assertTrue(self.uploader(9)._post_json({}, "1"))
        self.assertEqual(self.requests.post.call_args.kwargs["timeout"], 9)

    def test_invalid_timeouts_keep_requests_bounded(self):
        for value in (None, 0, -1, "invalid"):
            with self.subTest(value=value):
                self.assertTrue(self.uploader(value)._post_json({}, "1"))
                self.assertEqual(self.requests.post.call_args.kwargs["timeout"], 5)

    def test_http_timeout_returns_failure_and_records_errno(self):
        self.requests.post.side_effect = OSError(110)
        self.assertFalse(self.uploader(5)._post_json({}, "1"))
        sc.pdiag.set_net_fail.assert_called_once_with("sc_post:1", 110)


class FakeSocket:
    def __init__(self, reads):
        self.reads = iter(reads)
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def setblocking(self, value):
        self.timeout = None if value else 0

    def connect(self, address):
        pass

    def write(self, data, size=None):
        return len(data) if size is None else size

    def read(self, size):
        value = next(self.reads)
        if isinstance(value, Exception):
            if self.timeout is None:
                raise AssertionError("Read would block without a timeout")
            raise value
        return value


class MqttTimeoutTests(unittest.TestCase):
    def client(self, reads):
        client = MQTTClient(b"test", "broker.example")
        sock = FakeSocket([b"\x20\x02\x00\x00"] + reads)
        with patch("umqtt.simple.socket.socket", return_value=sock), patch(
            "umqtt.simple.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("127.0.0.1", 1883))]
        ):
            client.connect(timeout=5)
        return client, sock

    def test_empty_poll_restores_connection_timeout(self):
        client, sock = self.client([None])
        self.assertIsNone(client.check_msg())
        self.assertEqual(sock.timeout, 5)

    def test_partial_packet_times_out_for_both_read_paths(self):
        for method in ("check_msg", "wait_msg"):
            with self.subTest(method=method):
                client, sock = self.client([b"\x30", OSError(110)])
                with self.assertRaises(OSError):
                    getattr(client, method)()
                self.assertEqual(sock.timeout, 5)

    def test_first_read_failure_restores_connection_timeout(self):
        client, sock = self.client([OSError(110)])
        with self.assertRaises(OSError):
            client.check_msg()
        self.assertEqual(sock.timeout, 5)

    def test_complete_packet_still_reaches_callback(self):
        client, sock = self.client([b"\x30", b"\x08", b"\x00\x01", b"t", b"hello"])
        callback = Mock()
        client.set_callback(callback)
        self.assertEqual(client.check_msg(), 0x30)
        callback.assert_called_once_with(b"t", b"hello")
        self.assertEqual(sock.timeout, 5)


if __name__ == "__main__":
    unittest.main()
