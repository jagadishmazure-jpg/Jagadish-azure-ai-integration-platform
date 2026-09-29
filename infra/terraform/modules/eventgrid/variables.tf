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

variable "service_bus_namespace_id" {
  type = string
}

variable "queue_ids" {
  description = "queue name -> queue resource id"
  type        = map(string)
}

variable "routes" {
  description = "subscription name -> { event_type, queue }"
  type = map(object({
    event_type = string
    queue      = string
  }))
}

variable "publisher_principal_ids" {
  type    = map(string)
  default = {}
}

variable "public_network_access_enabled" {
  type    = bool
  default = true
}
