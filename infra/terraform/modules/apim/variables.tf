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
  default = "Consumption"
  validation {
    condition     = contains(["Consumption", "Developer", "BasicV2", "StandardV2"], var.sku)
    error_message = "sku must be Consumption, Developer, BasicV2 or StandardV2."
  }
}

variable "publisher_name" {
  type    = string
  default = "AI Integration Platform"
}

variable "publisher_email" {
  type = string
}

variable "backends" {
  description = "api name -> backend base URL"
  type        = map(string)
}

variable "audiences" {
  description = "api name -> expected token audience"
  type        = map(string)
  default     = {}
}

variable "entra_tenant_id" {
  type    = string
  default = ""
}
