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

variable "dns_prefix" {
  type = string
}

variable "node_vm_size" {
  type    = string
  default = "Standard_B2s"
}

variable "log_analytics_workspace_id" {
  type = string
}

variable "tenant_id" {
  description = "Entra tenant for Kubernetes RBAC."
  type        = string
}
