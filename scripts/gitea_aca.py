#!/usr/bin/env python3
"""Deploy or destroy a small persistent Gitea instance on Azure Container Apps."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


MANAGED_BY = "gitea-aca-python"
DEFAULT_IMAGE = "docker.gitea.com/gitea:1.27.3"
STORAGE_SHARE = "gitea-data"
STORAGE_MOUNT = "gitea-data"

ACA_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")
STORAGE_NAME_RE = re.compile(r"^[a-z0-9]{3,24}$")
LOCATION_RE = re.compile(r"^[a-z0-9]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class DeploymentError(RuntimeError):
    """A user-facing deployment error."""


@dataclass(frozen=True)
class DeployConfig:
    subscription: str
    location: str
    resource_group: str
    environment: str
    app_name: str
    image: str
    storage_account: str | None
    admin_username: str
    admin_email: str
    min_replicas: int
    yes: bool


class AzureCLI:
    """Small subprocess wrapper that never invokes a shell."""

    def __init__(self) -> None:
        self.environment = os.environ.copy()
        self.environment["AZURE_CORE_ONLY_SHOW_ERRORS"] = "true"

    def run(
        self,
        arguments: Sequence[str],
        *,
        capture: bool = False,
        check: bool = True,
    ) -> str:
        command = ["az", *arguments]
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE,
            env=self.environment,
        )
        if check and result.returncode != 0:
            summary = " ".join(command[:4])
            detail = result.stderr.strip()
            message = f"Azure CLI command failed ({summary})."
            if detail:
                message += f"\n{detail}"
            raise DeploymentError(message)
        return (result.stdout or "").strip()

    def json(self, arguments: Sequence[str]) -> Any:
        output = self.run([*arguments, "--output", "json"], capture=True)
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise DeploymentError("Azure CLI returned invalid JSON.") from error

    def resource_exists(self, arguments: Sequence[str]) -> bool:
        result = subprocess.run(
            ["az", *arguments, "--output", "none"],
            check=False,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.environment,
        )
        return result.returncode == 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create or destroy a low-cost Gitea deployment on Azure Container Apps. "
            "Run 'az login' first when not using Azure Cloud Shell."
        )
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    deploy = subparsers.add_parser("deploy", help="Create all Gitea resources")
    deploy.add_argument(
        "--subscription",
        required=True,
        help="Azure subscription name or ID",
    )
    deploy.add_argument(
        "--admin-email",
        required=True,
        help="Email address for the first Gitea administrator",
    )
    deploy.add_argument("--admin-username", default="admin")
    deploy.add_argument("--location", default="eastus2")
    deploy.add_argument("--resource-group", default="gitea-rg")
    deploy.add_argument("--environment", default="gitea-env")
    deploy.add_argument("--app-name", default="gitea-app")
    deploy.add_argument("--image", default=DEFAULT_IMAGE)
    deploy.add_argument(
        "--storage-account",
        help="Globally unique lowercase storage name; generated when omitted",
    )
    deploy.add_argument(
        "--min-replicas",
        type=int,
        choices=(0, 1),
        default=0,
        help="0 enables scale-to-zero; 1 keeps Gitea always running",
    )
    deploy.add_argument(
        "--yes",
        action="store_true",
        help="Skip the deployment confirmation prompt",
    )

    destroy = subparsers.add_parser(
        "destroy", help="Delete the resource group and every resource in it"
    )
    destroy.add_argument(
        "--subscription",
        required=True,
        help="Azure subscription name or ID",
    )
    destroy.add_argument(
        "--resource-group",
        required=True,
        help="Resource group created by this script",
    )
    destroy.add_argument(
        "--yes",
        action="store_true",
        help="Skip typing the resource-group name for confirmation",
    )
    destroy.add_argument(
        "--force",
        action="store_true",
        help="Allow deletion when the script's ownership tag is missing",
    )
    return parser


def validate_aca_name(value: str, option: str) -> None:
    if "--" in value or not ACA_NAME_RE.fullmatch(value):
        raise DeploymentError(
            f"{option} must be 3-32 lowercase letters, numbers, or hyphens; "
            "it must start with a letter and end with a letter or number."
        )


def validate_resource_group(value: str) -> None:
    if not value or len(value) > 90 or value.strip() != value:
        raise DeploymentError("--resource-group must contain 1-90 characters.")
    if any(character in value for character in ("/", "\\", "\0")):
        raise DeploymentError("--resource-group contains an unsafe character.")


def validate_deploy_config(config: DeployConfig) -> None:
    validate_resource_group(config.resource_group)
    validate_aca_name(config.environment, "--environment")
    validate_aca_name(config.app_name, "--app-name")
    if not LOCATION_RE.fullmatch(config.location):
        raise DeploymentError(
            "--location must be an Azure region name such as westus2."
        )
    if config.storage_account and not STORAGE_NAME_RE.fullmatch(config.storage_account):
        raise DeploymentError(
            "--storage-account must contain 3-24 lowercase letters or numbers."
        )
    if not USERNAME_RE.fullmatch(config.admin_username):
        raise DeploymentError("--admin-username contains unsupported characters.")
    if not EMAIL_RE.fullmatch(config.admin_email):
        raise DeploymentError("--admin-email must look like a valid email address.")
    if not re.fullmatch(r"docker\.gitea\.com/gitea:\d+\.\d+\.\d+", config.image):
        raise DeploymentError(
            "--image must be an exact official Gitea tag, such as "
            f"{DEFAULT_IMAGE}."
        )


def configure_account(cli: AzureCLI, subscription: str) -> dict[str, Any]:
    if shutil.which("az") is None:
        raise DeploymentError(
            "Azure CLI was not found. Install it or run this script in "
            "Azure Cloud Shell."
        )
    if not cli.resource_exists(["account", "show"]):
        raise DeploymentError(
            "Azure CLI is not signed in. Run 'az login' and try again."
        )
    cli.run(["account", "set", "--subscription", subscription])
    account = cli.json(["account", "show"])
    if not isinstance(account, dict):
        raise DeploymentError("Unable to read the active Azure subscription.")
    return account


def confirm_deploy(config: DeployConfig, subscription_name: str) -> None:
    print("\nDeployment summary")
    print(f"  Subscription:   {subscription_name}")
    print(f"  Region:         {config.location}")
    print(f"  Resource group: {config.resource_group}")
    print(f"  App:            {config.app_name}")
    print(f"  Administrator:  {config.admin_username} <{config.admin_email}>")
    print(f"  Minimum replicas: {config.min_replicas}")
    print("  Persistent data: Standard LRS Azure Files (separately billed)\n")
    if config.yes:
        return
    answer = input("Create these resources? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        raise DeploymentError("Deployment cancelled.")


def owned_resource_group(cli: AzureCLI, resource_group: str) -> bool:
    group = cli.json(["group", "show", "--name", resource_group])
    tags = group.get("tags") or {}
    return tags.get("managed-by") == MANAGED_BY


def find_managed_storage_account(
    cli: AzureCLI, resource_group: str, app_name: str
) -> str | None:
    accounts = cli.json(
        ["storage", "account", "list", "--resource-group", resource_group]
    )
    matches = [
        account["name"]
        for account in accounts
        if (account.get("tags") or {}).get("managed-by") == MANAGED_BY
        and (account.get("tags") or {}).get("gitea-app") == app_name
    ]
    if len(matches) > 1:
        raise DeploymentError(
            "More than one script-managed storage account was found. "
            "Pass the intended name with --storage-account."
        )
    return matches[0] if matches else None


def generate_storage_name() -> str:
    return f"gitea{secrets.token_hex(8)}"


def enable_file_share_soft_delete(
    cli: AzureCLI, resource_group: str, storage_account: str
) -> None:
    cli.run(
        [
            "storage",
            "account",
            "file-service-properties",
            "update",
            "--account-name",
            storage_account,
            "--resource-group",
            resource_group,
            "--enable-delete-retention",
            "true",
            "--delete-retention-days",
            "7",
            "--output",
            "none",
        ]
    )


def base_environment(root_url: str) -> list[dict[str, str]]:
    domain = root_url.removeprefix("https://").removesuffix("/")
    return [
        {"name": "SECRET_KEY", "secretRef": "gitea-secret-key"},
        {"name": "INSTALL_LOCK", "value": "true"},
        {"name": "USER_UID", "value": "1000"},
        {"name": "USER_GID", "value": "1000"},
        {"name": "DOMAIN", "value": domain},
        {"name": "ROOT_URL", "value": root_url},
        {"name": "HTTP_PORT", "value": "3000"},
        {"name": "DISABLE_SSH", "value": "true"},
        {"name": "LFS_START_SERVER", "value": "true"},
        {"name": "DISABLE_REGISTRATION", "value": "true"},
        {"name": "GITEA__server__PROTOCOL", "value": "http"},
        {"name": "GITEA__server__PUBLIC_URL_DETECTION", "value": "auto"},
        {"name": "GITEA__security__REVERSE_PROXY_LIMIT", "value": "1"},
        {
            "name": "GITEA__security__REVERSE_PROXY_TRUSTED_PROXIES",
            "value": "*",
        },
        {"name": "GITEA__database__DB_TYPE", "value": "sqlite3"},
        {"name": "GITEA__database__SQLITE_JOURNAL_MODE", "value": "DELETE"},
    ]


def bootstrap_command() -> str:
    return """set -eu
/etc/s6/gitea/setup
su-exec git gitea migrate --config /data/gitea/conf/app.ini
if [ ! -f /data/gitea/.initial-admin-created ]; then
  su-exec git gitea admin user create \\
    --config /data/gitea/conf/app.ini \\
    --admin \\
    --username "$GITEA_ADMIN_USERNAME" \\
    --email "$GITEA_ADMIN_EMAIL" \\
    --password "$GITEA_ADMIN_PASSWORD" \\
    --must-change-password=true
  touch /data/gitea/.initial-admin-created
fi
exec /usr/bin/s6-svscan /etc/s6
"""


def build_app_spec(
    config: DeployConfig,
    environment_id: str,
    root_url: str,
    gitea_secret: str,
    *,
    admin_password: str,
    bootstrap_admin: bool,
) -> dict[str, Any]:
    environment = base_environment(root_url)
    secrets_list = [
        {"name": "gitea-secret-key", "value": gitea_secret},
        {"name": "gitea-admin-password", "value": admin_password},
    ]
    container: dict[str, Any] = {
        "name": "gitea",
        "image": config.image,
        "resources": {"cpu": 0.25, "memory": "0.5Gi"},
        "env": environment,
        "volumeMounts": [{"volumeName": "gitea-data", "mountPath": "/data"}],
    }

    if bootstrap_admin:
        environment.extend(
            [
                {"name": "GITEA_ADMIN_USERNAME", "value": config.admin_username},
                {"name": "GITEA_ADMIN_EMAIL", "value": config.admin_email},
                {
                    "name": "GITEA_ADMIN_PASSWORD",
                    "secretRef": "gitea-admin-password",
                },
            ]
        )
        container["command"] = ["/usr/bin/entrypoint"]
        container["args"] = ["/bin/sh", "-c", bootstrap_command()]
    else:
        # Keep the password secret until this replacement revision is healthy:
        # the outgoing revision still references it during the transition.
        # Spell out the image defaults because empty command/args arrays have
        # inconsistent clearing semantics in Container Apps updates.
        container["command"] = ["/usr/bin/entrypoint"]
        container["args"] = ["/usr/bin/s6-svscan", "/etc/s6"]

    return {
        "location": config.location,
        "name": config.app_name,
        "tags": {"managed-by": MANAGED_BY, "application": "gitea"},
        "properties": {
            "managedEnvironmentId": environment_id,
            "configuration": {
                "activeRevisionsMode": "Single",
                "secrets": secrets_list,
                "ingress": {
                    "external": True,
                    "allowInsecure": False,
                    "targetPort": 3000,
                    "transport": "auto",
                    "traffic": [{"latestRevision": True, "weight": 100}],
                },
            },
            "template": {
                "containers": [container],
                "scale": {
                    "minReplicas": config.min_replicas,
                    "maxReplicas": 1,
                },
                "volumes": [
                    {
                        "name": "gitea-data",
                        "storageName": STORAGE_MOUNT,
                        "storageType": "AzureFile",
                        "mountOptions": (
                            "uid=1000,gid=1000,dir_mode=0770,file_mode=0660,"
                            "mfsymlinks,nobrl"
                        ),
                    }
                ],
            },
        },
    }


def apply_app_spec(
    cli: AzureCLI,
    config: DeployConfig,
    spec: dict[str, Any],
    *,
    create: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="gitea-aca-") as temp_directory:
        spec_path = Path(temp_directory) / "container-app.yaml"
        # JSON is valid YAML. Using the standard library keeps installation to
        # just Python and Azure CLI while still satisfying az --yaml.
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        spec_path.chmod(0o600)
        action = "create" if create else "update"
        cli.run(
            [
                "containerapp",
                action,
                "--name",
                config.app_name,
                "--resource-group",
                config.resource_group,
                "--yaml",
                str(spec_path),
                "--output",
                "none",
            ]
        )


def wait_for_health(root_url: str, timeout_seconds: int = 420) -> None:
    health_url = f"{root_url.rstrip('/')}/api/healthz"
    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with urllib.request.urlopen(health_url, timeout=10) as response:
                if 200 <= response.status < 400:
                    print(" healthy")
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        if attempt % 6 == 0:
            print(".", end="", flush=True)
        time.sleep(5)
    raise DeploymentError(
        f"Gitea did not become healthy within {timeout_seconds} seconds. "
        "Inspect it with 'az containerapp logs show'."
    )


def wait_for_latest_revision(cli: AzureCLI, config: DeployConfig) -> None:
    deadline = time.monotonic() + 420
    while time.monotonic() < deadline:
        app = cli.json(
            [
                "containerapp",
                "show",
                "--name",
                config.app_name,
                "--resource-group",
                config.resource_group,
            ]
        )
        properties = app.get("properties") or {}
        latest = properties.get("latestRevisionName")
        if latest and properties.get("latestReadyRevisionName") == latest:
            return
        time.sleep(5)
    raise DeploymentError(
        "The replacement Container App revision did not become ready within "
        "420 seconds. The previous revision and its bootstrap secret were retained."
    )


def deploy(cli: AzureCLI, config: DeployConfig) -> None:
    validate_deploy_config(config)
    account = configure_account(cli, config.subscription)
    subscription_name = str(
        account.get("name") or account.get("id") or config.subscription
    )
    confirm_deploy(config, subscription_name)

    print("[1/8] Installing or updating the Container Apps CLI extension...")
    cli.run(
        [
            "extension",
            "add",
            "--name",
            "containerapp",
            "--upgrade",
            "--only-show-errors",
        ]
    )
    print("[2/8] Registering Azure resource providers...")
    for namespace in ("Microsoft.App", "Microsoft.Storage"):
        cli.run(
            [
                "provider",
                "register",
                "--namespace",
                namespace,
                "--wait",
                "--output",
                "none",
            ]
        )

    group_exists = cli.run(
        ["group", "exists", "--name", config.resource_group, "--output", "tsv"],
        capture=True,
    ).lower() == "true"
    if group_exists and not owned_resource_group(cli, config.resource_group):
        raise DeploymentError(
            f"Resource group '{config.resource_group}' already exists and was not "
            "created by this script. Choose another --resource-group."
        )

    print("[3/8] Creating the resource group and persistent storage...")
    cli.run(
        [
            "group",
            "create",
            "--name",
            config.resource_group,
            "--location",
            config.location,
            "--tags",
            f"managed-by={MANAGED_BY}",
            "application=gitea",
            "--output",
            "none",
        ]
    )

    existing_storage = find_managed_storage_account(
        cli, config.resource_group, config.app_name
    )
    if existing_storage and config.storage_account not in (None, existing_storage):
        raise DeploymentError(
            "The resource group already contains managed storage "
            f"'{existing_storage}', "
            f"which differs from --storage-account '{config.storage_account}'."
        )
    storage_account = (
        existing_storage or config.storage_account or generate_storage_name()
    )
    if not STORAGE_NAME_RE.fullmatch(storage_account):
        raise DeploymentError("The generated storage-account name is invalid.")

    if not existing_storage:
        cli.run(
            [
                "storage",
                "account",
                "create",
                "--name",
                storage_account,
                "--resource-group",
                config.resource_group,
                "--location",
                config.location,
                "--kind",
                "StorageV2",
                "--sku",
                "Standard_LRS",
                "--https-only",
                "true",
                "--min-tls-version",
                "TLS1_2",
                "--allow-blob-public-access",
                "false",
                "--allow-shared-key-access",
                "true",
                "--public-network-access",
                "Enabled",
                "--tags",
                f"managed-by={MANAGED_BY}",
                f"gitea-app={config.app_name}",
                "--output",
                "none",
            ]
        )

    storage_key = cli.run(
        [
            "storage",
            "account",
            "keys",
            "list",
            "--resource-group",
            config.resource_group,
            "--account-name",
            storage_account,
            "--query",
            "[0].value",
            "--output",
            "tsv",
        ],
        capture=True,
    )
    cli.run(
        [
            "storage",
            "share",
            "create",
            "--name",
            STORAGE_SHARE,
            "--account-name",
            storage_account,
            "--account-key",
            storage_key,
            "--protocol",
            "SMB",
            "--quota",
            "5",
            "--output",
            "none",
        ]
    )
    enable_file_share_soft_delete(cli, config.resource_group, storage_account)

    print("[4/8] Creating the Consumption-only Container Apps environment...")
    if not cli.resource_exists(
        [
            "containerapp",
            "env",
            "show",
            "--name",
            config.environment,
            "--resource-group",
            config.resource_group,
        ]
    ):
        cli.run(
            [
                "containerapp",
                "env",
                "create",
                "--name",
                config.environment,
                "--resource-group",
                config.resource_group,
                "--location",
                config.location,
                "--environment-mode",
                "ConsumptionOnly",
                "--logs-destination",
                "none",
                "--output",
                "none",
            ]
        )

    print("[5/8] Mounting Azure Files...")
    cli.run(
        [
            "containerapp",
            "env",
            "storage",
            "set",
            "--name",
            config.environment,
            "--resource-group",
            config.resource_group,
            "--storage-name",
            STORAGE_MOUNT,
            "--azure-file-account-name",
            storage_account,
            "--azure-file-account-key",
            storage_key,
            "--azure-file-share-name",
            STORAGE_SHARE,
            "--access-mode",
            "ReadWrite",
            "--output",
            "none",
        ]
    )
    del storage_key

    if cli.resource_exists(
        [
            "containerapp",
            "show",
            "--name",
            config.app_name,
            "--resource-group",
            config.resource_group,
        ]
    ):
        raise DeploymentError(
            f"Container App '{config.app_name}' already exists. The script will not "
            "replace a running Gitea server. Use destroy first or choose another name."
        )

    environment = cli.json(
        [
            "containerapp",
            "env",
            "show",
            "--name",
            config.environment,
            "--resource-group",
            config.resource_group,
        ]
    )
    environment_id = environment["id"]
    dns_suffix = environment["properties"]["defaultDomain"]
    root_url = f"https://{config.app_name}.{dns_suffix}/"
    gitea_secret = secrets.token_hex(32)
    admin_password = secrets.token_urlsafe(24)

    print("[6/8] Creating Gitea and its first administrator...")
    bootstrap_spec = build_app_spec(
        config,
        environment_id,
        root_url,
        gitea_secret,
        admin_password=admin_password,
        bootstrap_admin=True,
    )
    apply_app_spec(cli, config, bootstrap_spec, create=True)
    print("      Waiting for Gitea", end="", flush=True)
    wait_for_health(root_url)

    print("\nAdministrator created. Save these credentials now:")
    print(f"  Gitea URL:          {root_url}")
    print(f"  Admin username:     {config.admin_username}")
    print(f"  Temporary password: {admin_password}")

    print("[7/8] Removing the one-time administrator bootstrap...")
    final_spec = build_app_spec(
        config,
        environment_id,
        root_url,
        gitea_secret,
        admin_password=admin_password,
        bootstrap_admin=False,
    )
    apply_app_spec(cli, config, final_spec, create=False)
    print("      Waiting for the final revision", end="", flush=True)
    wait_for_latest_revision(cli, config)
    wait_for_health(root_url)
    cli.run(
        [
            "containerapp",
            "secret",
            "remove",
            "--name",
            config.app_name,
            "--resource-group",
            config.resource_group,
            "--secret-names",
            "gitea-admin-password",
            "--output",
            "none",
        ]
    )

    print("[8/8] Deployment complete.\n")
    print(f"Gitea URL:          {root_url}")
    print(f"Admin username:     {config.admin_username}")
    print(f"Temporary password: {admin_password}")
    print("\nSign in now. Gitea will require you to replace the temporary password.")
    print("Store repositories using HTTPS clone URLs; SSH is intentionally disabled.")
    print(
        "\nTo remove everything later:\n"
        f"  python3 scripts/gitea_aca.py destroy --subscription "
        f"{json.dumps(config.subscription)} --resource-group "
        f"{json.dumps(config.resource_group)}"
    )


def destroy_resources(
    cli: AzureCLI,
    subscription: str,
    resource_group: str,
    *,
    yes: bool,
    force: bool,
) -> None:
    validate_resource_group(resource_group)
    account = configure_account(cli, subscription)
    exists = cli.run(
        ["group", "exists", "--name", resource_group, "--output", "tsv"],
        capture=True,
    ).lower() == "true"
    if not exists:
        print(f"Resource group '{resource_group}' does not exist. Nothing to destroy.")
        return
    if not owned_resource_group(cli, resource_group) and not force:
        raise DeploymentError(
            f"Refusing to delete '{resource_group}' because it does not have the "
            f"managed-by={MANAGED_BY} tag. Use --force only after verifying the name."
        )

    subscription_name = str(account.get("name") or account.get("id") or subscription)
    print(
        "\nDANGER: this permanently deletes Gitea, every repository, "
        "and Azure Files."
    )
    print(f"  Subscription:   {subscription_name}")
    print(f"  Resource group: {resource_group}\n")
    if not yes:
        answer = input(f"Type '{resource_group}' to delete it: ").strip()
        if answer != resource_group:
            raise DeploymentError("Deletion cancelled; the name did not match.")

    print("Deleting the resource group and waiting for Azure to finish...")
    cli.run(["group", "delete", "--name", resource_group, "--yes", "--output", "none"])
    print("All deployment resources were deleted.")


def main(arguments: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(arguments)
    cli = AzureCLI()
    try:
        if parsed.action == "deploy":
            deploy(
                cli,
                DeployConfig(
                    subscription=parsed.subscription,
                    location=parsed.location,
                    resource_group=parsed.resource_group,
                    environment=parsed.environment,
                    app_name=parsed.app_name,
                    image=parsed.image,
                    storage_account=parsed.storage_account,
                    admin_username=parsed.admin_username,
                    admin_email=parsed.admin_email,
                    min_replicas=parsed.min_replicas,
                    yes=parsed.yes,
                ),
            )
        else:
            destroy_resources(
                cli,
                parsed.subscription,
                parsed.resource_group,
                yes=parsed.yes,
                force=parsed.force,
            )
    except (DeploymentError, KeyboardInterrupt) as error:
        message = str(error) if str(error) else "Cancelled."
        print(f"\nError: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
