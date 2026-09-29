# dev: cost-min (scale to zero, Consumption APIM, Basic Service Bus, Flex Functions, 1 GB/day log cap).
environment        = "dev"
location           = "eastus2"
instance           = "001"
apim_sku           = "Consumption"
service_bus_sku    = "Basic"
log_daily_quota_gb = 1
min_replicas       = 0
deploy_front_door  = false
private_networking = false
compute_profile    = "containerapps"
