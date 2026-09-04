# Easiest deployment: one Python command

The included Python utility creates a small Gitea server and its first
administrator, or removes the entire deployment later. It uses Azure CLI and
Python's standard library, so there are no Python packages to install.

This is the recommended path for someone who does not need Terraform, GitHub
Actions, or a custom domain.

## What it creates

- one resource group containing every deployment resource;
- an Azure Container Apps Consumption-only environment;
- Gitea at 0.25 vCPU and 0.5 GiB, with zero or one replica;
- a persistent 5 GiB Standard LRS Azure Files share; and
- the first administrator account with a generated temporary password.

The ACA app can scale to zero when idle. Azure Files storage and transactions
are billed separately from the ACA free grant, so the deployment is designed
to be inexpensive rather than literally cost-free.

## Before you begin

The easiest place to run the script is **Azure Cloud Shell**:

1. Sign in to the [Azure portal](https://portal.azure.com/).
2. Open **Cloud Shell** from the portal toolbar and choose **Bash**.
3. Clone this repository, or upload and extract it in Cloud Shell.
4. Change into the repository directory.

Cloud Shell already includes Azure CLI and Python and is signed in as your
portal user. That user needs permission to create resource groups, storage
accounts, and Container Apps resources.

If running on your own computer instead, install Azure CLI and Python 3.10 or
newer, then run `az login` first.

To see your subscription names and IDs:

```bash
az account list --query "[].{Name:name, ID:id}" --output table
```

## Deploy

From the repository directory, run the following command. Replace the two
values in angle brackets:

```bash
python3 scripts/gitea_aca.py deploy \
  --subscription "<subscription name or ID>" \
  --admin-email "<your email address>"
```

Review the summary and enter `y`. Deployment normally takes several minutes.
When it finishes, the script prints:

- the Gitea web address;
- the administrator username (`admin` by default); and
- a generated temporary password.

Save the password, sign in immediately, and replace it when Gitea asks. Public
registration and SSH are disabled; use HTTPS URLs to clone and push.

The defaults create `gitea-rg`, `gitea-env`, and `gitea-app` in `westus2`.
For a different region or names, add options such as:

```bash
python3 scripts/gitea_aca.py deploy \
  --subscription "<subscription name or ID>" \
  --admin-email "<your email address>" \
  --location eastus2 \
  --resource-group my-gitea-rg \
  --app-name my-gitea
```

Use `--min-replicas 1` if you prefer no scale-to-zero cold start and accept
the additional compute usage. Use `--yes` only when you intentionally want to
skip the creation confirmation.

All deploy options are documented by the script:

```bash
python3 scripts/gitea_aca.py deploy --help
```

## Destroy everything

This deletes the entire resource group, including every repository and the
Azure Files share. Back up anything important first.

```bash
python3 scripts/gitea_aca.py destroy \
  --subscription "<subscription name or ID>" \
  --resource-group gitea-rg
```

For safety, type the resource-group name again when prompted. The script also
refuses to delete a group that it did not tag as one of its own deployments.
`--force` overrides that tag check and should be used only after carefully
checking the subscription and resource-group name.

## If deployment stops partway through

Rerun the same deploy command. The script can reuse the resource group,
storage account, and Container Apps environment that it created. It will not
replace an existing Container App, which prevents an accidental overwrite.

If the Gitea app was already created, first check its status and logs:

```bash
az containerapp show --resource-group gitea-rg --name gitea-app --output table
az containerapp logs show --resource-group gitea-rg --name gitea-app --follow
```

If you do not need to preserve it, use the destroy command and deploy again.
Do not manage the same resource group with both this script and Terraform.

