# Durable Functions host for the vendor-invoice process on Flex Consumption (FC1, Python 3.13).
# Storage uses identity-based connections only: shared keys are disabled on the account.
resource "azurerm_storage_account" "this" {
  name                            = var.storage_account_name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  tags                            = var.tags
  account_kind                    = "StorageV2"
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  default_to_oauth_authentication = true
  local_user_enabled              = false

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }
}

resource "azurerm_storage_container" "deployments" {
  name                  = "deployments"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# Durable Functions (Azure Storage provider) needs blob, queue and table data access.
resource "azurerm_role_assignment" "storage" {
  for_each                         = toset(["Storage Blob Data Owner", "Storage Queue Data Contributor", "Storage Table Data Contributor"])
  scope                            = azurerm_storage_account.this.id
  role_definition_name             = each.value
  principal_id                     = var.identity_principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_service_plan" "this" {
  name                = var.plan_name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
  os_type             = "Linux"
  sku_name            = "FC1"
}

resource "azurerm_function_app_flex_consumption" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = merge(var.tags, { "service-name" = "bpm-functions" })
  service_plan_id     = azurerm_service_plan.this.id
  https_only          = true

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.this.primary_blob_endpoint}${azurerm_storage_container.deployments.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = var.identity_id

  runtime_name           = "python"
  runtime_version        = "3.13"
  maximum_instance_count = var.maximum_instance_count
  instance_memory_in_mb  = var.instance_memory_mb

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  site_config {
    application_insights_connection_string = var.app_insights_connection_string
  }

  app_settings = merge({
    AzureWebJobsStorage__accountName          = azurerm_storage_account.this.name
    AzureWebJobsStorage__credential           = "managedidentity"
    AzureWebJobsStorage__clientId             = var.identity_client_id
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING = "ClientId=${var.identity_client_id};Authorization=AAD"
    AZURE_CLIENT_ID                           = var.identity_client_id
    AIIP_MODE                                 = "azure"
  }, var.app_settings)

  depends_on = [azurerm_role_assignment.storage]
}
