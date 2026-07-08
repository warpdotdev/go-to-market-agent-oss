import io
import json
import os
import unittest
import urllib.error
from email.message import Message
from unittest import mock

from hubspot_agent import client


class _Response:
    def __init__(self, status, body=""):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ClientTests(unittest.TestCase):
    def setUp(self):
        client._token = None
        self._env = mock.patch.dict(
            os.environ,
            {},
            clear=True,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        client._token = None

    def test_load_token_prefers_private_app_token(self):
        os.environ["HUBSPOT_PRIVATE_APP_TOKEN"] = "private-token"
        os.environ["GENERAL_HUBSPOT_APP_TOKEN"] = "general-token"
        os.environ["HUBSPOT_ACCESS_TOKEN"] = "access-token"

        self.assertEqual(client._load_token(), "private-token")

    def test_load_token_reads_dotenv_file(self):
        mocked_open = mock.mock_open(
            read_data="GENERAL_HUBSPOT_APP_TOKEN=dotenv-general\n"
        )
        with mock.patch("hubspot_agent.client.os.path.exists", return_value=True), mock.patch(
            "builtins.open",
            mocked_open,
        ):
            self.assertEqual(client._load_token(), "dotenv-general")

    def test_load_token_raises_when_missing(self):
        with mock.patch("hubspot_agent.client.os.path.exists", return_value=False):
            with self.assertRaises(RuntimeError):
                client._load_token()

    def test_hubspot_request_returns_none_for_204(self):
        with mock.patch("hubspot_agent.client._load_token", return_value="token"), mock.patch(
            "hubspot_agent.client.urllib.request.urlopen",
            return_value=_Response(204),
        ):
            self.assertIsNone(client.hubspot_request("DELETE", "/crm/v3/lists/123"))

    def test_hubspot_request_parses_json_response(self):
        body = json.dumps({"ok": True, "id": "123"})
        with mock.patch("hubspot_agent.client._load_token", return_value="token"), mock.patch(
            "hubspot_agent.client.urllib.request.urlopen",
            return_value=_Response(200, body),
        ):
            result = client.hubspot_request("GET", "/automation/v4/flows")

        self.assertEqual(result, {"ok": True, "id": "123"})

    def test_hubspot_request_retries_on_429(self):
        headers = Message()
        headers["Retry-After"] = "0"
        rate_limited = urllib.error.HTTPError(
            url="https://api.hubapi.com/test",
            code=429,
            msg="Too Many Requests",
            hdrs=headers,
            fp=io.BytesIO(b'{"status":"error"}'),
        )

        with mock.patch("hubspot_agent.client._load_token", return_value="token"), mock.patch(
            "hubspot_agent.client.time.sleep"
        ) as sleep_mock, mock.patch(
            "hubspot_agent.client.urllib.request.urlopen",
            side_effect=[rate_limited, _Response(200, '{"results":[1]}')],
        ) as urlopen_mock:
            result = client.hubspot_request("GET", "/test")

        self.assertEqual(result, {"results": [1]})
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once_with(0)

    def test_paginated_get_yields_across_multiple_pages(self):
        with mock.patch(
            "hubspot_agent.client.hubspot_request",
            side_effect=[
                {
                    "results": [{"id": "1"}],
                    "paging": {"next": {"after": "cursor-1"}},
                },
                {
                    "results": [{"id": "2"}],
                },
            ],
        ) as request_mock:
            items = list(client.paginated_get("/automation/v4/flows", params={"limit": 1}))

        self.assertEqual(items, [{"id": "1"}, {"id": "2"}])
        self.assertEqual(
            request_mock.call_args_list,
            [
                mock.call("GET", "/automation/v4/flows?limit=1"),
                mock.call("GET", "/automation/v4/flows?limit=1&after=cursor-1"),
            ],
        )
