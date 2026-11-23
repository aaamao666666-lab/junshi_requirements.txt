import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage import SQLiteStore, token_fingerprint


class SQLiteStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "test.sqlite3")
        self.tenant = self.store.create_tenant("123456:abcdefghijklmnopqrstuvwxyz", "demo_bot", "@owner")

    def tearDown(self):
        self.tmp.cleanup()

    def test_token_is_hashed_not_stored_as_plaintext(self):
        self.assertNotEqual(token_fingerprint("123456:abcdefghijklmnopqrstuvwxyz"),
                            "123456:abcdefghijklmnopqrstuvwxyz")
        raw = Path(self.store.path).read_bytes()
        self.assertNotIn(b"123456:abcdefghijklmnopqrstuvwxyz", raw)

    def test_player_can_bind_at_most_two_groups(self):
        g1 = self.store.add_group(self.tenant, -1001, "Group 1")
        g2 = self.store.add_group(self.tenant, -1002, "Group 2")
        g3 = self.store.add_group(self.tenant, -1003, "Group 3")
        self.store.bind_player_group(self.tenant, 77, g1)
        self.store.bind_player_group(self.tenant, 77, g2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.bind_player_group(self.tenant, 77, g3)
        self.assertEqual(self.store.list_player_groups(self.tenant, 77), [g1, g2])

    def test_tenant_cannot_bind_another_tenants_group(self):
        other = self.store.create_tenant("987654:zyxwvutsrqponmlkjihgfedcba", "other_bot", "other")
        foreign_group = self.store.add_group(other, -2001, "Foreign")
        with self.assertRaisesRegex(ValueError, "group_not_available_for_tenant"):
            self.store.bind_player_group(self.tenant, 88, foreign_group)

    def test_payment_review_is_one_time(self):
        request_id = self.store.create_payment_request(self.tenant, 88, 68, "tx-abc")
        self.store.review_payment(request_id, 1, True)
        with self.assertRaisesRegex(ValueError, "payment_request_not_pending"):
            self.store.review_payment(request_id, 1, False)


if __name__ == "__main__":
    unittest.main()
