import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "gitea_aca.py"
SPEC = importlib.util.spec_from_file_location("gitea_aca", SCRIPT_PATH)
assert SPEC and SPEC.loader
gitea_aca = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gitea_aca
SPEC.loader.exec_module(gitea_aca)


class ParserTests(unittest.TestCase):
    def test_deploy_requires_subscription_and_admin_email(self):
        parser = gitea_aca.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["deploy", "--subscription", "example"])

    def test_destroy_requires_resource_group(self):
        parser = gitea_aca.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["destroy", "--subscription", "example"])


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.config = gitea_aca.DeployConfig(
            subscription="subscription",
            location="westus2",
            resource_group="gitea-rg",
            environment="gitea-env",
            app_name="gitea-app",
            image=gitea_aca.DEFAULT_IMAGE,
            storage_account=None,
            admin_username="admin",
            admin_email="admin@example.com",
            min_replicas=0,
            yes=True,
        )

    def test_bootstrap_spec_creates_admin_and_mounts_storage(self):
        spec = gitea_aca.build_app_spec(
            self.config,
            "/subscriptions/example/environments/gitea-env",
            "https://gitea.example.test/",
            "secret-key",
            admin_password="temporary-password",
            bootstrap_admin=True,
        )
        properties = spec["properties"]
        container = properties["template"]["containers"][0]
        secret_names = {
            secret["name"] for secret in properties["configuration"]["secrets"]
        }
        env_names = {entry["name"] for entry in container["env"]}
        env_values = {entry["name"]: entry.get("value") for entry in container["env"]}

        self.assertEqual(container["resources"], {"cpu": 0.25, "memory": "0.5Gi"})
        self.assertEqual(properties["template"]["scale"]["maxReplicas"], 1)
        self.assertIn("gitea-admin-password", secret_names)
        self.assertIn("GITEA_ADMIN_PASSWORD", env_names)
        self.assertEqual(env_values["GITEA__queue__TYPE"], "channel")
        self.assertEqual(properties["configuration"]["activeRevisionsMode"], "Multiple")
        self.assertEqual(container["volumeMounts"][0]["mountPath"], "/data")
        self.assertIn("gitea admin user create", container["args"][2])
        self.assertIn("gitea web", container["args"][2])
        self.assertNotIn("s6-svscan", container["args"][2])

    def test_final_spec_removes_admin_bootstrap(self):
        spec = gitea_aca.build_app_spec(
            self.config,
            "/subscriptions/example/environments/gitea-env",
            "https://gitea.example.test/",
            "secret-key",
            admin_password="temporary-password",
            bootstrap_admin=False,
        )
        properties = spec["properties"]
        container = properties["template"]["containers"][0]
        secret_names = {
            secret["name"] for secret in properties["configuration"]["secrets"]
        }
        env_names = {entry["name"] for entry in container["env"]}

        self.assertEqual(
            secret_names, {"gitea-secret-key", "gitea-admin-password"}
        )
        self.assertNotIn("GITEA_ADMIN_PASSWORD", env_names)
        self.assertEqual(container["command"], ["/usr/bin/entrypoint"])
        self.assertEqual(container["args"], ["/bin/bash", "/etc/s6/gitea/run"])

    def test_storage_name_is_valid_and_random(self):
        first = gitea_aca.generate_storage_name()
        second = gitea_aca.generate_storage_name()
        self.assertRegex(first, gitea_aca.STORAGE_NAME_RE)
        self.assertNotEqual(first, second)

    def test_file_service_configuration_uses_account_name_option(self):
        class RecordingCLI:
            def __init__(self):
                self.arguments = None

            def run(self, arguments):
                self.arguments = arguments

        cli = RecordingCLI()
        gitea_aca.enable_file_share_soft_delete(cli, "gitea-rg", "gitea123")

        self.assertIn("--account-name", cli.arguments)
        self.assertNotIn("--name", cli.arguments)
        account_name_index = cli.arguments.index("--account-name") + 1
        self.assertEqual(cli.arguments[account_name_index], "gitea123")

    def test_revision_wait_requires_latest_revision_to_be_ready(self):
        class RevisionCLI:
            def json(self, arguments):
                return {
                    "properties": {
                        "latestRevisionName": "gitea-app--new",
                        "latestReadyRevisionName": "gitea-app--new",
                    }
                }

        gitea_aca.wait_for_latest_revision(RevisionCLI(), self.config)


if __name__ == "__main__":
    unittest.main()
