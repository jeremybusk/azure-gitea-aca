# Gitea on Azure Container Apps

A small, persistent Gitea server on the Azure Container Apps (ACA)
Consumption plan. Terraform deploys the official Gitea image directly from
Gitea's public registry, so there is no paid Azure Container Registry and no
custom image to maintain.

## Architecture

```text
Browser / Git HTTPS
        |
        v
ACA public ingress (TLS, scale-to-zero)
        |
        v
Gitea 1.27.3 (0.25 vCPU, 0.5 GiB, max 1 replica)
        |
        v
Azure Files SMB share mounted at /data
  - SQLite database
  - repositories and LFS objects
  - configuration, sessions, and attachments
```

The app uses HTTPS for both the web UI and Git. SSH cloning is intentionally
disabled: ACA exposes one ingress target cleanly in this minimal design, while
Git over HTTPS works without another proxy, public IP, or custom network.

## Why there is no WAF

The previous Caddy/Coraza/OWASP CRS layer was removed. It would consume part
of the same small CPU and memory allocation, require rules tuned for Git and
LFS uploads, and would not be Azure's managed WAF. ACA ingress already handles
TLS. For a personal, low-traffic Gitea instance, prompt upgrades, strong
authentication, disabled public registration, and backups provide more value.

If this becomes an important public service, put Azure Front Door Premium with
WAF in front of it and move the database to managed PostgreSQL. That is a
production architecture, but it is not a free one.

## Cost and availability tradeoff

ACA's monthly Consumption-plan grant is shared across the subscription. This
configuration sets `min_replicas = 0`, so Gitea costs no ACA compute while it
is scaled to zero. The first request after an idle period has a cold start.

Azure Files capacity and transactions are billed separately and are not part
of the ACA grant. The deployment uses Standard LRS and a 5 GiB quota to keep
that cost small. Azure-managed custom-domain certificates are free, but DNS
registration is not.

This is a budget/personal deployment. SQLite lives on an SMB file share, so
the app is hard-limited to one replica. Do not increase `max_replicas`. For an
always-available or business-critical service, use `min_replicas = 1`, a
managed PostgreSQL database, monitoring, and tested off-share backups.

## Prerequisites

- An Azure subscription
- Azure CLI
- Terraform 1.6 or newer
- Permission to create resource groups, storage, and Container Apps resources

Sign in and select the subscription:

```bash
az login
az account set --subscription "<subscription-name-or-id>"
export ARM_SUBSCRIPTION_ID="$(az account show --query id -o tsv | tr -d '\r\n')"
```

## Deploy

For the easiest guided path with no Terraform or GitHub setup, use the
[Python deploy/destroy utility](docs/python-deploy.md). It creates the first
administrator automatically, supports `--custom-domain`, prints the required
DNS and managed-certificate commands, and has deletion safeguards.

For a copy-and-paste-only alternative, use the
[Azure CLI Bash deployment](docs/az-cli-deploy.md).

### Terraform deployment

This repository is configured for an Azure Blob Terraform backend. If you do
not already have one, follow [bootstrap/README.md](bootstrap/README.md) once.

Then configure and apply the Gitea stack:

```bash
cp backend.hcl.example backend.hcl
cp terraform.tfvars.example terraform.tfvars

# Fill in backend.hcl. terraform.tfvars defaults are ready for a small server.
terraform init -backend-config=backend.hcl
terraform plan -out=tfplan
terraform apply tfplan
```

The image is pinned to an exact Gitea release. Review Gitea's upgrade notes and
change `gitea_image` deliberately when updating it.

## Create the first administrator

The web installer is locked and public registration is disabled, preventing a
stranger from claiming the first account. Wake the app, then open a shell in
the running replica:

```bash
curl -fsS "$(terraform output -raw aca_url)/api/healthz"

az containerapp exec \
  --resource-group "$(terraform output -raw resource_group_name)" \
  --name "$(terraform output -raw container_app_name)" \
  --command /bin/bash
```

Inside the container, create the administrator. Replace the email address;
the command prints a random temporary password and requires it to be changed
at first login.

```bash
su-exec git gitea admin user create \
  --config /data/gitea/conf/app.ini \
  --admin \
  --username admin \
  --email you@example.com \
  --random-password \
  --random-password-length 24
exit
```

Open the URL:

```bash
terraform output -raw gitea_url
```

## Custom domain

The free ACA hostname works without any DNS configuration. To use a custom
hostname with the Python utility, provide it during the first deployment:

```bash
python3 scripts/gitea_aca.py deploy \
  --subscription "<subscription-name-or-id>" \
  --admin-email "you@example.com" \
  --custom-domain git.example.com
```

The utility prints the CNAME, validation TXT record, and Azure commands needed
to obtain and bind the free managed TLS certificate. See the
[Python deployment guide](docs/python-deploy.md#use-a-custom-dns-name-and-managed-tls).

For Terraform, set the hostname before the first apply so Gitea writes the
correct canonical URL:

```hcl
custom_domain = "git.example.com"
enable_custom_domain = false
```

Custom domains require a two-phase DNS-validation and managed-certificate
binding. Follow [docs/custom-domain.md](docs/custom-domain.md), then set
`enable_custom_domain = true` for the second apply.

## Operations

Show live logs:

```bash
az containerapp logs show \
  --resource-group "$(terraform output -raw resource_group_name)" \
  --name "$(terraform output -raw container_app_name)" \
  --follow
```

The storage account enables seven-day file-share soft delete. That helps with
accidental share deletion, but it is not an application-consistent backup.
Regularly run `gitea dump` while writes are stopped and copy the archive away
from this storage account. See [Gitea's backup and restore documentation](https://docs.gitea.com/administration/backup-and-restore).

Terraform state contains the Azure Files access key and Gitea's generated
secret key. Keep state in the private backend, retain the bootstrap access
controls, and never commit state or saved plan files.

To keep the public surface small, the defaults also:

- disable account registration;
- force HTTPS at ACA ingress;
- disable SSH;
- use one replica only; and
- omit Log Analytics, a VNet, Front Door, and ACR.

## Remove the deployment

Destroying the stack deletes the storage account and all Gitea repositories.
Take and verify a backup first:

```bash
terraform destroy
```

## GitHub Actions

The workflow validates Terraform on pull requests and deploys from the
protected `azure` environment on pushes to `main` or manual dispatch. The
bootstrap stack creates its secretless GitHub OIDC identity.

Add these repository variables from
`terraform -chdir=bootstrap output -json github_actions_variables`:

- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`
- `TF_STATE_RESOURCE_GROUP`
- `TF_STATE_STORAGE_ACCOUNT`
- `TF_STATE_CONTAINER`

No registry or MaxMind credentials are needed.
