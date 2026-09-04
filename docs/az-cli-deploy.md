# Ultra-simple deployment with Azure CLI

This path skips Terraform, remote state, GitHub Actions, and custom domains.
It creates the same small Gitea shape using Bash and Azure CLI:

- ACA Consumption-only environment with no Log Analytics workspace;
- official pinned Gitea image at 0.25 vCPU and 0.5 GiB;
- scale-to-zero with at most one replica;
- persistent 5 GiB Standard LRS Azure Files share;
- HTTPS web and Git access through the free ACA hostname; and
- locked installer, disabled public registration, and disabled SSH.

Azure Files is billed separately from the ACA free grant. This is intended for
a personal or test server, not a highly available production deployment.

## Prerequisites

Use Bash with Azure CLI and OpenSSL installed. Sign in and select the target
subscription first:

```bash
az login
az account set --subscription "<subscription-name-or-id>"
```

The signed-in account needs permission to create resource groups, storage
accounts, and Container Apps resources.

## Deploy

Paste this entire block into Bash. Change only the values in the first section
if desired. The storage account name is generated because Azure Storage names
must be globally unique.

Run the block once per deployment. To rerun it against an existing deployment,
first set `GITEA_STORAGE_ACCOUNT` to the account name printed by the original
run; otherwise the timestamp-based default creates another storage account.

```bash
set -euo pipefail
umask 077

# ---- settings you may change ----
GITEA_LOCATION="${GITEA_LOCATION:-westus2}"
GITEA_RESOURCE_GROUP="${GITEA_RESOURCE_GROUP:-gitea-rg}"
GITEA_ENVIRONMENT="${GITEA_ENVIRONMENT:-gitea-env}"
GITEA_APP="${GITEA_APP:-gitea-app}"
GITEA_IMAGE="${GITEA_IMAGE:-docker.gitea.com/gitea:1.27.3}"
# ---------------------------------

GITEA_SHARE="gitea-data"
GITEA_STORAGE_MOUNT="gitea-data"
GITEA_STORAGE_ACCOUNT="${GITEA_STORAGE_ACCOUNT:-gitea$(date +%s)${RANDOM}}"
GITEA_SECRET_KEY="$(openssl rand -hex 32)"
GITEA_SPEC="$(mktemp)"
trap 'rm -f "${GITEA_SPEC}"' EXIT

az extension add --name containerapp --upgrade --only-show-errors
az provider register --namespace Microsoft.App --wait
az provider register --namespace Microsoft.Storage --wait

az group create \
  --name "${GITEA_RESOURCE_GROUP}" \
  --location "${GITEA_LOCATION}" \
  --output none

az storage account create \
  --name "${GITEA_STORAGE_ACCOUNT}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --location "${GITEA_LOCATION}" \
  --kind StorageV2 \
  --sku Standard_LRS \
  --https-only true \
  --min-tls-version TLS1_2 \
  --allow-blob-public-access false \
  --allow-shared-key-access true \
  --public-network-access Enabled \
  --output none

GITEA_STORAGE_KEY="$(az storage account keys list \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --account-name "${GITEA_STORAGE_ACCOUNT}" \
  --query '[0].value' \
  --output tsv)"

az storage share create \
  --name "${GITEA_SHARE}" \
  --account-name "${GITEA_STORAGE_ACCOUNT}" \
  --account-key "${GITEA_STORAGE_KEY}" \
  --protocol SMB \
  --quota 5 \
  --output none

az storage account file-service-properties update \
  --name "${GITEA_STORAGE_ACCOUNT}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --enable-delete-retention true \
  --delete-retention-days 7 \
  --output none

az containerapp env create \
  --name "${GITEA_ENVIRONMENT}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --location "${GITEA_LOCATION}" \
  --environment-mode ConsumptionOnly \
  --logs-destination none \
  --output none

az containerapp env storage set \
  --name "${GITEA_ENVIRONMENT}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --storage-name "${GITEA_STORAGE_MOUNT}" \
  --azure-file-account-name "${GITEA_STORAGE_ACCOUNT}" \
  --azure-file-account-key "${GITEA_STORAGE_KEY}" \
  --azure-file-share-name "${GITEA_SHARE}" \
  --access-mode ReadWrite \
  --output none

GITEA_ENVIRONMENT_ID="$(az containerapp env show \
  --name "${GITEA_ENVIRONMENT}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --query id \
  --output tsv)"

GITEA_DNS_SUFFIX="$(az containerapp env show \
  --name "${GITEA_ENVIRONMENT}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --query properties.defaultDomain \
  --output tsv)"

GITEA_DOMAIN="${GITEA_APP}.${GITEA_DNS_SUFFIX}"
GITEA_URL="https://${GITEA_DOMAIN}"

cat >"${GITEA_SPEC}" <<YAML
location: ${GITEA_LOCATION}
name: ${GITEA_APP}
properties:
  managedEnvironmentId: ${GITEA_ENVIRONMENT_ID}
  configuration:
    activeRevisionsMode: Single
    secrets:
      - name: gitea-secret-key
        value: ${GITEA_SECRET_KEY}
    ingress:
      external: true
      allowInsecure: false
      targetPort: 3000
      transport: auto
      traffic:
        - latestRevision: true
          weight: 100
  template:
    containers:
      - name: gitea
        image: ${GITEA_IMAGE}
        resources:
          cpu: 0.25
          memory: 0.5Gi
        env:
          - name: SECRET_KEY
            secretRef: gitea-secret-key
          - name: INSTALL_LOCK
            value: "true"
          - name: USER_UID
            value: "1000"
          - name: USER_GID
            value: "1000"
          - name: DOMAIN
            value: ${GITEA_DOMAIN}
          - name: ROOT_URL
            value: ${GITEA_URL}/
          - name: HTTP_PORT
            value: "3000"
          - name: DISABLE_SSH
            value: "true"
          - name: LFS_START_SERVER
            value: "true"
          - name: DISABLE_REGISTRATION
            value: "true"
          - name: GITEA__server__PROTOCOL
            value: http
          - name: GITEA__server__PUBLIC_URL_DETECTION
            value: auto
          - name: GITEA__security__REVERSE_PROXY_LIMIT
            value: "1"
          - name: GITEA__security__REVERSE_PROXY_TRUSTED_PROXIES
            value: "*"
          - name: GITEA__database__DB_TYPE
            value: sqlite3
          - name: GITEA__database__SQLITE_JOURNAL_MODE
            value: DELETE
        volumeMounts:
          - volumeName: gitea-data
            mountPath: /data
    scale:
      minReplicas: 0
      maxReplicas: 1
    volumes:
      - name: gitea-data
        storageName: ${GITEA_STORAGE_MOUNT}
        storageType: AzureFile
        mountOptions: uid=1000,gid=1000,dir_mode=0770,file_mode=0660,mfsymlinks,nobrl
YAML

az containerapp create \
  --name "${GITEA_APP}" \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --yaml "${GITEA_SPEC}" \
  --output none

unset GITEA_SECRET_KEY GITEA_STORAGE_KEY

printf 'Gitea URL: %s\n' "${GITEA_URL}"
printf 'Storage account: %s\n' "${GITEA_STORAGE_ACCOUNT}"
```

The first request can take a minute while ACA starts the initial replica and
Gitea creates its SQLite database.

## Create the first administrator

Wake the app and open its console:

```bash
curl -fsS --retry 30 --retry-all-errors --retry-delay 2 \
  "${GITEA_URL}/api/healthz"

az containerapp exec \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --name "${GITEA_APP}" \
  --command bash
```

Run this inside the container, replacing the email address. It prints a random
temporary password and requires you to change it at first login:

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

Open the URL printed by the deployment and sign in. Clone repositories using
their HTTPS URLs.

## Later shell sessions

If you open a new terminal, restore the three values needed by common commands:

```bash
GITEA_RESOURCE_GROUP="gitea-rg"
GITEA_APP="gitea-app"
GITEA_URL="https://$(az containerapp show \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --name "${GITEA_APP}" \
  --query properties.configuration.ingress.fqdn \
  --output tsv)"
```

View logs:

```bash
az containerapp logs show \
  --resource-group "${GITEA_RESOURCE_GROUP}" \
  --name "${GITEA_APP}" \
  --follow
```

## Delete everything

Deleting the resource group permanently deletes Gitea and its Azure Files
data. Back up repositories first:

```bash
az group delete --name "${GITEA_RESOURCE_GROUP}" --yes
```

This CLI deployment is independent of Terraform. Do not later run the
Terraform configuration against these resources unless you first import every
resource into Terraform state.

## Azure references

- [Create and mount Azure Files in Container Apps](https://learn.microsoft.com/azure/container-apps/storage-mounts-azure-files)
- [Azure CLI Container Apps environment storage commands](https://learn.microsoft.com/cli/azure/containerapp/env/storage)
- [Azure Container Apps console access](https://learn.microsoft.com/azure/container-apps/container-console)
