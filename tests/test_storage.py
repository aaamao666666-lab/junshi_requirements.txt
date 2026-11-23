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
        self.store.add_admin(self.tenant, 1, "owner", role="owner")

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

    def test_admin_price_is_configurable_and_has_no_default(self):
        self.assertIsNone(self.store.get_tenant_price(self.tenant)["agreed_price"])
        self.store.set_tenant_price(self.tenant, 123.5, "USDT")
        price = self.store.get_tenant_price(self.tenant)
        self.assertEqual(price["agreed_price"], 123.5)
        self.assertEqual(price["currency"], "USDT")

    def test_verified_registration_grants_unlimited_access(self):
        self.assertEqual(self.store.register_player(self.tenant, 88, "@player"), "pending")
        self.assertFalse(self.store.player_has_access(self.tenant, 88))
        self.store.verify_player(self.tenant, 88, admin_user_id=1, approved=True)
        self.assertTrue(self.store.player_has_access(self.tenant, 88))
        # Access remains granted without a subscription expiry or payment check.
        self.assertTrue(self.store.player_has_access(self.tenant, 88))

    def test_rejected_registration_does_not_grant_access(self):
        self.store.register_player(self.tenant, 89, "player2")
        self.store.verify_player(self.tenant, 89, admin_user_id=1, approved=False)
        self.assertFalse(self.store.player_has_access(self.tenant, 89))


    def test_renewal_offer_requires_admin_and_snapshots_terms(self):
        with self.assertRaises(PermissionError):
            self.store.configure_renewal_terms(self.tenant, 999, "12.5", 2592000,
                                                "TReceiver", "Agreement", "Notice")
        version = self.store.configure_renewal_terms(
            self.tenant, 1, "12.50", 2592000, "TReceiver", "Agreement v1", "Renew notice"
        )
        self.assertEqual(version, 1)
        offer = self.store.create_renewal_offer(self.tenant, 1, 88)
        self.store.configure_renewal_terms(
            self.tenant, 1, "20", 2592000, "TReceiver2", "Agreement v2", "Changed"
        )
        self.store.accept_renewal_offer(offer, 88)
        with self.assertRaises(ValueError):
            self.store.accept_renewal_offer(offer, 88)
        self.store.activate_renewal(offer, 88, "TWallet", "contract-subscription-1",
                                    "2026-12-24T00:00:00Z")
        attempt = self.store.create_renewal_attempt(
            offer, "agreement-1-period-1", "2026-12-24T00:00:00Z"
        )
        self.assertGreater(attempt, 0)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_renewal_attempt(
                offer, "agreement-1-period-1", "2026-12-24T00:00:00Z"
            )
        self.store.update_renewal_attempt(attempt, "submitted", txid="tx-001")
        self.store.update_renewal_attempt(attempt, "confirmed", txid="tx-001")
        with self.assertRaises(ValueError):
            self.store.update_renewal_attempt(attempt, "failed", error_detail="duplicate")

    def test_renewal_requires_wallet_authorization_before_active(self):
        self.store.configure_renewal_terms(self.tenant, 1, "5", 86400, "TReceiver")
        offer = self.store.create_renewal_offer(self.tenant, 1, 88)
        self.store.accept_renewal_offer(offer, 88)
        with self.assertRaises(ValueError):
            self.store.create_renewal_attempt(offer, "key", "2026-12-01T00:00:00Z")
        with self.assertRaises(ValueError):
            self.store.activate_renewal(offer, 88, "", "", "")
        self.store.cancel_renewal(offer, 88)

    def test_bet_is_persisted_and_settled_once(self):
        group = self.store.add_group(self.tenant, -3001, "Bet group")
        bet_id = self.store.create_bet(self.tenant, 91, group, "canada28", "20261124001", "大", 100)
        self.store.settle_bet(bet_id, True, 100)
        with self.assertRaisesRegex(ValueError, "bet_not_pending"):
            self.store.settle_bet(bet_id, False, 100)


if __name__ == "__main__":
    unittest.main()
