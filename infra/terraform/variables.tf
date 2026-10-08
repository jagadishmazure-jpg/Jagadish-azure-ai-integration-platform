# ---- naming / tagging ----
variable "workload" {
  type    = string
  default = "aiip"
}

variable "environment" {
  description = "dev | test | prod"
  type        = string
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "instance" {
  type    = string
  default = "001"
}

variable "name_suffix" {
  description = "Optional suffix for globally unique names (set when forking)."
  type        = string
  default     = ""
}

variable "owner" {
  type    = string
  default = "jagadish.meduri"
}

variable "project" {
  type    = string
  default = "azure-ai-integration-platform"
}

variable "cost_center" {
  type    = string
  default = "portfolio"
}

variable "extra_tags" {
  type    = map(string)
  default = {}
}

# ---- shape (mirrors infra/main.bicep parameters) ----
variable "entra_tenant_id" {
  description = "Tenant for token validation in APIM and the gateways. Empty = current tenant for app settings, no APIM JWT check (dev only)."
  type        = string
  default     = ""
}

variable "apim_publisher_email" {
  type    = string
  default = "noreply@example.com"
}

variable "apim_sku" {
  type    = string
  default = "Consumption"
}

variable "service_bus_sku" {
  type    = string
  default = "Basic"
}

variable "log_daily_quota_gb" {
  description = "Log Analytics daily ingestion cap (GB). -1 = no cap."
  type        = number
  default     = 1
}

variable "min_replicas" {
  type    = number
  default = 0
}

variable "deploy_front_door" {
  type    = bool
  default = false
}

variable "private_networking" {
  description = "VNet + private endpoints (forces Service Bus Premium, internal Container Apps environment)."
  type        = bool
  default     = false
}

variable "compute_profile" {
  description = "containerapps (default) or aks (provisions the cluster only)."
  type        = string
  default     = "containerapps"
  validation {
    condition     = contains(["containerapps", "aks"], var.compute_profile)
    error_message = "compute_profile must be containerapps or aks."
  }
}

variable "key_vault_purge_protection" {
  type    = bool
  default = false
}

variable "container_image" {
  description = "Initial image for every app; the deploy workflow rolls the platform image afterwards."
  type        = string
  default     = "mcr.microsoft.com/k8se/quickstart:latest"
}

# ---- monitoring, alerting and Defender for Cloud ----
variable "enable_alerts" {
  description = "Action group, metric + log alert rules and diagnostic settings to Log Analytics. Cheap; on by default."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Optional on-call email for the action group. Empty = alerts fire in Azure Monitor only."
  type        = string
  default     = ""
}

variable "enable_defender" {
  description = "Turn on Microsoft Defender for Cloud plans. SUBSCRIPTION-WIDE and billed per resource, so off by default."
  type        = bool
  default     = false
}

variable "defender_plans" {
  description = "Defender for Cloud plans enabled when enable_defender = true."
  type        = set(string)
  default     = ["Arm", "Containers", "KeyVaults"]
}
