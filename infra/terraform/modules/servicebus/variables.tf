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

variable "sku" {
  type    = string
  default = "Basic"
  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.sku)
    error_message = "sku must be Basic, Standard or Premium."
  }
}

variable "queues" {
  type = list(string)
}

variable "max_delivery_count" {
  type    = number
  default = 5
}

variable "default_message_ttl" {
  type    = string
  default = "P7D"
}

variable "duplicate_detection" {
  type    = bool
  default = true
}

variable "receiver_principal_ids" {
  type    = map(string)
  default = {}
}

variable "sender_principal_ids" {
  type    = map(string)
  default = {}
}

variable "public_network_access_enabled" {
  type    = bool
  default = true
}
