# API Management as the single front door for callers of the integration plane. It validates the
# Entra ID token (when a tenant is configured), strips spoofable identity headers, injects a
# traceparent if missing and applies a coarse rate limit. The gateways still validate the token
# themselves: APIM is defense in depth, not the only check.
locals {
  sku_name = var.sku == "Consumption" ? "Consumption_0" : "${var.sku}_1"
  ops = merge([for api in keys(var.backends) : {
    "${api}-post" = { api = api, method = "POST", id = "all-post", name = "POST passthrough" }
    "${api}-get"  = { api = api, method = "GET", id = "all-get", name = "GET passthrough" }
  }]...)
}

resource "azurerm_api_management" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
  sku_name            = local.sku_name
  publisher_name      = var.publisher_name
  publisher_email     = var.publisher_email

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_api_management_api" "this" {
  for_each              = var.backends
  name                  = each.key
  resource_group_name   = var.resource_group_name
  api_management_name   = azurerm_api_management.this.name
  revision              = "1"
  display_name          = "AI integration plane: ${each.key}"
  path                  = each.key
  protocols             = ["https"]
  service_url           = each.value
  subscription_required = false
}

resource "azurerm_api_management_api_operation" "this" {
  for_each            = local.ops
  operation_id        = each.value.id
  api_name            = azurerm_api_management_api.this[each.value.api].name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = var.resource_group_name
  display_name        = each.value.name
  method              = each.value.method
  url_template        = "/*"
}

resource "azurerm_api_management_api_policy" "this" {
  for_each            = var.backends
  api_name            = azurerm_api_management_api.this[each.key].name
  api_management_name = azurerm_api_management.this.name
  resource_group_name = var.resource_group_name

  xml_content = <<-XML
    <policies>
      <inbound>
        <base />
        <set-header name="x-caller-agent" exists-action="delete" />
        <set-header name="x-client-assertion" exists-action="delete" />
        %{if var.entra_tenant_id != ""}
        <validate-jwt header-name="Authorization" failed-validation-httpcode="401">
          <openid-config url="https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0/.well-known/openid-configuration" />
          <audiences><audience>${lookup(var.audiences, each.key, "api://aiip-${each.key}")}</audience></audiences>
        </validate-jwt>
        %{endif}
        <rate-limit calls="120" renewal-period="60" />
        <set-header name="traceparent" exists-action="skip">
          <value>@($"00-{Guid.NewGuid().ToString("N")}-{Guid.NewGuid().ToString("N").Substring(0,16)}-01")</value>
        </set-header>
      </inbound>
      <backend><base /></backend>
      <outbound><base /><set-header name="X-Powered-By" exists-action="delete" /></outbound>
      <on-error><base /></on-error>
    </policies>
  XML
}
