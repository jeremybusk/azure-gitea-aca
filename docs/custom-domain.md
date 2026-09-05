# Custom domain and managed TLS certificate

Using the default `azurecontainerapps.io` hostname requires no action. These
steps apply only when `custom_domain` is set, preferably to a subdomain such
as `git.example.com`.

## Python deployment utility

Provide the hostname on the initial deployment:

```bash
python3 scripts/gitea_aca.py deploy \
  --subscription "<subscription name or ID>" \
  --admin-email "<your email address>" \
  --custom-domain git.example.com
```

The utility configures Gitea's canonical `ROOT_URL` and prints the exact CNAME,
domain-validation TXT record, and `az containerapp hostname` commands after
the app is created. The CNAME must point to the stable app hostname, such as
`gitea-app.<environment-id>.<region>.azurecontainerapps.io`, and never to a
revision hostname containing `--0000001` or another revision suffix.

After creating the printed DNS records, wait for them to resolve and run the
printed `hostname add` and `hostname bind` commands. Azure Container Apps
issues and renews the free managed TLS certificate; Gitea itself remains on
HTTP port 3000 behind Azure ingress. See the complete
[Python instructions](python-deploy.md#use-a-custom-dns-name-and-managed-tls).

## Terraform deployment

### 1. Create the app and inspect the DNS values

Set the hostname but leave its binding disabled, then apply once:

```hcl
custom_domain = "git.example.com"
enable_custom_domain = false
```

```bash
terraform apply
terraform output -json custom_domain_dns_records
```

The sensitive output contains the stable ACA hostname, environment public IP,
and domain-verification token.

### 2. Create public DNS records

For a subdomain, create:

| Type | Name | Value |
| --- | --- | --- |
| CNAME | `git` | the output `aca_fqdn` |
| TXT | `asuid.git` | the output `verification_txt` |

For an apex domain, use an A record to `environment_static_ip` and a TXT
record named `asuid`. Keep the TXT record while the hostname remains bound;
it prevents a dangling-domain takeover and may be needed for revalidation.

Wait until public DNS returns both records. Then set
`enable_custom_domain = true` and apply again to create the hostname binding.

### 3. Issue and bind the free managed certificate

```bash
az containerapp hostname bind \
  --resource-group "$(terraform output -raw resource_group_name)" \
  --name "$(terraform output -raw container_app_name)" \
  --environment "$(terraform output -raw container_app_environment_name)" \
  --hostname "$(terraform output -json custom_domain_dns_records | jq -r .hostname)" \
  --validation-method CNAME
```

For an apex A record, use `--validation-method HTTP`. Certificate issuance can
take several minutes. Terraform ignores the certificate fields on the custom
domain so a later apply does not undo this one-time binding.

Verify it:

```bash
az containerapp hostname list \
  --resource-group "$(terraform output -raw resource_group_name)" \
  --name "$(terraform output -raw container_app_name)" \
  --output table
```

The binding type should become `SniEnabled`.

### Changing an existing hostname

Gitea persists its generated `app.ini` on Azure Files. Changing
`custom_domain` after the first successful start creates a new ACA revision,
but the persisted canonical URL may still need to be updated. In the running
container, inspect `/data/gitea/conf/app.ini`, update `[server] ROOT_URL`, and
restart the revision. Back up the share first.
