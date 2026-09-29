# prod: warm replicas, private endpoints (forces Service Bus Premium), Front Door, purge protection.
environment                = "prod"
location                   = "eastus2"
instance                   = "001"
apim_sku                   = "StandardV2"
service_bus_sku            = "Standard"
log_daily_quota_gb         = -1
min_replicas               = 1
deploy_front_door          = true
private_networking         = true
compute_profile            = "containerapps"
key_vault_purge_protection = true
