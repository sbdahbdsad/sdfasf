"""Regression tests for system inbound identity during startup migration."""
import unittest

import main


WEB_PROXY = {
    "name": "Web Proxy",
    "protocol": "telegram-webproxy",
    "inbound_type": "transport",
    "_managed_kind": "webproxy",
    "system": True,
}


class NodeSelectorMigrationTests(unittest.IsolatedAsyncioTestCase):
    def test_fresh_install_creates_node_without_overwriting_web_proxy(self):
        web_proxy = dict(WEB_PROXY)
        inbounds = {"webproxy": web_proxy, "default-reality": {"name": "Reality", "protocol": "reality"}}
        changed, created = main._ensure_node_selector_inbound(inbounds)
        self.assertTrue(changed)
        self.assertTrue(created)
        self.assertIs(inbounds["webproxy"], web_proxy)
        self.assertEqual(inbounds["webproxy"]["protocol"], "telegram-webproxy")
        self.assertEqual(inbounds["Node"]["protocol"], "node")
        self.assertEqual(inbounds["Node"]["inbound_type"], "node")

    def test_canonical_node_wins_even_when_web_proxy_is_first(self):
        web_proxy = dict(WEB_PROXY)
        node = {"name": "Node", "protocol": "node", "inbound_type": "node", "system": True}
        inbounds = {"webproxy": web_proxy, "Node": node}
        selected = main._find_node_selector_inbound(inbounds)
        self.assertIsNotNone(selected)
        self.assertEqual(selected[0], "Node")
        changed, created = main._ensure_node_selector_inbound(inbounds)
        self.assertTrue(changed)  # initializes the node-id lists on older records
        self.assertFalse(created)
        self.assertIs(inbounds["webproxy"], web_proxy)
        self.assertEqual(inbounds["Node"]["protocol"], "node")
        changed_again, created_again = main._ensure_node_selector_inbound(inbounds)
        self.assertFalse(changed_again)
        self.assertFalse(created_again)

    def test_typed_legacy_node_id_is_migrated_to_canonical_id(self):
        web_proxy = dict(WEB_PROXY)
        node = {"name": "Multi Node", "protocol": "node", "enabled_node_ids": ["n1", "n1", " n2 "]}
        inbounds = {"webproxy": web_proxy, "legacy-node": node}
        changed, created = main._ensure_node_selector_inbound(inbounds)
        self.assertTrue(changed)
        self.assertFalse(created)
        self.assertNotIn("legacy-node", inbounds)
        self.assertEqual(inbounds["Node"]["enabled_node_ids"], ["n1", "n2"])
        self.assertEqual(inbounds["Node"]["node_ids"], ["n1", "n2"])
        self.assertIs(inbounds["webproxy"], web_proxy)

    def test_legacy_system_node_name_is_supported(self):
        node = {"name": "Node", "system": True, "node_ids": ["n1"]}
        self.assertEqual(main._find_node_selector_inbound({"legacy-id": node}), ("legacy-id", node))

    def test_generic_system_inbound_is_not_a_node_candidate(self):
        inbounds = {"system-item": {"name": "Internal Service", "protocol": "vless", "system": True}}
        self.assertIsNone(main._find_node_selector_inbound(inbounds))
        changed, created = main._ensure_node_selector_inbound(inbounds)
        self.assertTrue(changed)
        self.assertTrue(created)
        self.assertIn("system-item", inbounds)
        self.assertIn("Node", inbounds)

    async def test_inbounds_api_exposes_web_proxy_and_node_as_distinct_rows(self):
        import os
        from unittest.mock import patch

        original = {
            "INBOUNDS": main.INBOUNDS,
            "USERS": main.USERS,
            "WEB_PROXY_SECRETS": main.WEB_PROXY_SECRETS,
            "web_proxy_status": main.web_proxy_status,
        }
        try:
            web_proxy = dict(WEB_PROXY)
            web_proxy.update({"enabled": True, "network": "ws", "security": "tls"})
            node = {"name": "Node", "protocol": "node", "inbound_type": "node", "system": True,
                    "enabled_node_ids": [], "node_ids": []}
            main.INBOUNDS = {"webproxy": web_proxy, "Node": node}
            main.USERS = {}
            main.WEB_PROXY_SECRETS = {}
            main.web_proxy_status = lambda: ("enabled", "")
            with patch.dict(os.environ, {"SPIDER_TWP_SIGNING_KEY": "test-only-placeholder"}):
                result = await main.list_inbounds({"kind": "session"})
            rows = {item["inbound_id"]: item for item in result["inbounds"]}
            self.assertIn("webproxy", rows)
            self.assertIn("Node", rows)
            self.assertEqual(rows["webproxy"]["protocol"], "telegram-webproxy")
            self.assertEqual(rows["Node"]["protocol"], "node")
        finally:
            main.INBOUNDS = original["INBOUNDS"]
            main.USERS = original["USERS"]
            main.WEB_PROXY_SECRETS = original["WEB_PROXY_SECRETS"]
            main.web_proxy_status = original["web_proxy_status"]

    async def test_web_proxy_adoption_is_persisted_when_node_is_already_canonical(self):
        from unittest.mock import patch

        original = {
            "INBOUNDS": main.INBOUNDS,
            "USERS": main.USERS,
            "LINKS": main.LINKS,
            "WEB_PROXY_SECRETS": main.WEB_PROXY_SECRETS,
            "save_state": main.save_state,
        }
        saves = []
        async def capture_save_state():
            saves.append(True)
        try:
            main.INBOUNDS = {
                "legacy-webproxy": dict(WEB_PROXY),
                "Node": {"name": "Node", "protocol": "node", "inbound_type": "node",
                         "system": True, "node_ids": [], "enabled_node_ids": []},
            }
            main.USERS = {}
            main.LINKS = {}
            main.WEB_PROXY_SECRETS = {}
            main.save_state = capture_save_state

            with patch.object(main, "web_proxy_hostname", return_value="panel.example.com"):
                self.assertEqual(await main.ensure_web_proxy_inbound(), "webproxy")
                self.assertEqual(saves, [True], "adopting a legacy ID must be persisted")
                self.assertEqual(await main.ensure_web_proxy_inbound(), "webproxy")
            self.assertEqual(len(saves), 1, "a stable second startup must not save again")
            self.assertIn("Node", main.INBOUNDS)
            self.assertIn("webproxy", main.INBOUNDS)
        finally:
            main.INBOUNDS = original["INBOUNDS"]
            main.USERS = original["USERS"]
            main.LINKS = original["LINKS"]
            main.WEB_PROXY_SECRETS = original["WEB_PROXY_SECRETS"]
            main.save_state = original["save_state"]

    async def test_recovers_web_proxy_record_saved_under_node_id_by_old_bug(self):
        original = {
            "INBOUNDS": main.INBOUNDS,
            "USERS": main.USERS,
            "LINKS": main.LINKS,
            "WEB_PROXY_SECRETS": main.WEB_PROXY_SECRETS,
            "save_state": main.save_state,
        }
        async def no_save_state():
            return None
        try:
            # Shape produced by the old startup migration: the Web Proxy record
            # was renamed to Node but retained its managed-kind marker.
            broken_record = dict(WEB_PROXY)
            broken_record.update({"name": "Node", "protocol": "node", "inbound_type": "node", "enabled": True})
            main.INBOUNDS = {"Node": broken_record}
            main.USERS = {}
            main.LINKS = {}
            main.WEB_PROXY_SECRETS = {}
            main.save_state = no_save_state

            canonical_id = await main.ensure_web_proxy_inbound()
            self.assertEqual(canonical_id, "webproxy")
            self.assertTrue(main.is_web_proxy_inbound(main.INBOUNDS["webproxy"]))

            changed, created = main._ensure_node_selector_inbound(main.INBOUNDS)
            self.assertTrue(changed)
            self.assertTrue(created)
            self.assertIn("webproxy", main.INBOUNDS)
            self.assertIn("Node", main.INBOUNDS)
            self.assertEqual(main.INBOUNDS["webproxy"]["protocol"], "telegram-webproxy")
            self.assertEqual(main.INBOUNDS["Node"]["protocol"], "node")
        finally:
            main.INBOUNDS = original["INBOUNDS"]
            main.USERS = original["USERS"]
            main.LINKS = original["LINKS"]
            main.WEB_PROXY_SECRETS = original["WEB_PROXY_SECRETS"]
            main.save_state = original["save_state"]


if __name__ == "__main__":
    unittest.main()
