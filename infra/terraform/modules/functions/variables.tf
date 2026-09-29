variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "name" {
  type = string
}

variable "plan_name" {
  type = string
}

variable "storage_account_name" {
  type = string
}

variable "identity_id" {
  type = string
}

variable "identity_client_id" {
  type = string
}

variable "identity_principal_id" {
  type = string
}

variable "app_insights_connection_string" {
  type      = string
  sensitive = true
}

variable "app_settings" {
  type    = map(string)
  default = {}
}

variable "maximum_instance_count" {
  type    = number
  default = 40
}

variable "instance_memory_mb" {
  type    = number
  default = 2048
}
