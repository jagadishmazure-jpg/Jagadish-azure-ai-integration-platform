variable "resource_group_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

variable "name" {
  type = string
}

variable "endpoint_name" {
  type = string
}

variable "origin_host_name" {
  type = string
}
