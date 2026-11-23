import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "junshi_master.py"


class SourceSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_source_compiles(self):
        compile(self.source, str(SOURCE), "exec")

    def test_master_token_is_environment_configured(self):
        self.assertIn('os.environ.get("MASTER_BOT_TOKEN", "").strip()', self.source)
        self.assertNotRegex(self.source, r'MASTER_BOT_TOKEN\s*=\s*["\'][^"\']+["\']')

    def test_group_posting_is_disabled_by_default(self):
        self.assertIn('ENABLE_GROUP_POSTS = os.environ.get("ENABLE_GROUP_POSTS", "false").lower() == "true"', self.source)

    def test_group_post_is_behind_explicit_gate(self):
        self.assertIn("if ENABLE_GROUP_POSTS:", self.source)
        self.assertIn('await app.bot.send_message(cfg["target_chat_id"], bet_cmd)', self.source)

    def test_no_embedded_telegram_token_pattern(self):
        self.assertNotRegex(self.source, r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b')

    def test_tenant_worker_is_scoped_by_bot_token(self):
        self.assertIn("async def sub_bot_worker(token: str, app):", self.source)
        self.assertIn('cfg = manager.data["sub_bots"][token]', self.source)


if __name__ == "__main__":
    unittest.main()
