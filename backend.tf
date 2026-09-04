terraform {
  # Values are supplied by GitHub Actions (or with -backend-config locally).
  # Keeping them out of source lets each repository use its own state account.
  backend "azurerm" {}
}
