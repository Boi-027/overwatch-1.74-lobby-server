import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ow174.bnet import protocol as P
from ow174.bnet.rpc_server import BNetRpcServer

SECRET = "US-90e33fa5deadbeefdeadbeefdeadbeef-000000001"


class FakeSession:
    def __init__(self):
        self.logs, self.notifications = [], []

    def log(self, text):
        self.logs.append(text)

    async def send_response(self, header, body):
        pass

    async def send_notification(self, service_hash, method_id, body):
        self.notifications.append((service_hash, method_id))


class AuthTests(unittest.TestCase):
    def run_verify(self, ticket):
        request = P.VerifyWebCredentialsRequest()
        request.web_credentials = ticket.encode()
        header = P.Header()
        header.method_id = P.VERIFY_WEB_CREDENTIALS
        session = FakeSession()
        asyncio.run(BNetRpcServer()._auth(session, header, request.SerializeToString()))
        return session

    def test_cached_ticket_logs_in_and_is_never_written_to_the_log(self):
        session = self.run_verify(SECRET)
        self.assertIn((P.AUTH_CLIENT_HASH, P.ON_LOGON_COMPLETE), session.notifications)
        text = "\n".join(session.logs)
        self.assertNotIn("US-", text)
        self.assertNotIn("90e33fa5", text)
        self.assertIn(str(len(SECRET)), text)


if __name__ == "__main__":
    unittest.main()
