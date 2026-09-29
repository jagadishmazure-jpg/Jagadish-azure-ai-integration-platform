locals {
  use_aca    = var.compute_profile == "containerapps"
  sb_sku     = var.private_networking ? "Premium" : var.service_bus_sku
  public     = !var.private_networking
  tenant_id  = var.entra_tenant_id == "" ? data.azurerm_client_config.current.tenant_id : var.entra_tenant_id
  n          = module.naming.prefix_for
  public_dom = local.use_aca ? module.aca_env[0].default_domain : ""

  tags = merge({
    env           = var.environment
    owner         = var.owner
    project       = var.project
    "cost-center" = var.cost_center
    workload      = var.workload
    "managed-by"  = "terraform"
  }, var.extra_tags)

  # ---- workloads (same names, registrations and commands as infra/main.bicep) ----
  gateways = {
    identity-gateway = { reg = "identity-gateway", external = false, command = ["python", "-m", "uvicorn", "aiip.identity.gateway:app", "--host", "0.0.0.0", "--port", "8080"] }
    tool-gateway     = { reg = "tool-gateway", external = true, command = ["python", "-m", "uvicorn", "aiip.tools.gateway:app", "--host", "0.0.0.0", "--port", "8080"] }
    mcp-gateway      = { reg = "mcp-gateway", external = true, command = ["python", "-m", "uvicorn", "aiip.mcp.gateway:app", "--host", "0.0.0.0", "--port", "8080"] }
    a2a-gateway      = { reg = "a2a-gateway", external = true, command = ["python", "-m", "uvicorn", "aiip.a2a.gateway:app", "--host", "0.0.0.0", "--port", "8080"] }
    event-gateway    = { reg = "event-gateway", external = true, command = ["python", "-m", "uvicorn", "aiip.events.gateway:app", "--host", "0.0.0.0", "--port", "8080"] }
  }
  mcp_servers = {
    mcp-sap-orders           = { reg = "mcp-gateway", external = false, command = ["python", "-m", "aiip.mcp_servers", "sap-orders"] }
    mcp-servicenow-incidents = { reg = "mcp-gateway", external = false, command = ["python", "-m", "aiip.mcp_servers", "servicenow-incidents"] }
    mcp-sql-warehouse        = { reg = "mcp-gateway", external = false, command = ["python", "-m", "aiip.mcp_servers", "sql-warehouse"] }
  }
  agents = {
    agent-care-planner = { reg = "care-planner", external = false, command = ["python", "-m", "aiip.agents", "care-planner"] }
    agent-crm          = { reg = "crm-agent", external = false, command = ["python", "-m", "aiip.agents", "crm-agent"] }
    agent-erp          = { reg = "erp-agent", external = false, command = ["python", "-m", "aiip.agents", "erp-agent"] }
    agent-data         = { reg = "data-agent", external = false, command = ["python", "-m", "aiip.agents", "data-agent"] }
    agent-ap-invoice   = { reg = "ap-invoice-agent", external = false, command = ["python", "-m", "aiip.agents", "ap-invoice-agent"] }
  }
  workers = {
    worker-order-events    = { reg = "worker-order-events", queue = "order-events", command = ["python", "-m", "aiip.events.worker", "order-events"] }
    worker-shipment-events = { reg = "worker-shipment-events", queue = "shipment-events", command = ["python", "-m", "aiip.events.worker", "shipment-events"] }
  }
  http_apps = merge(local.gateways, local.mcp_servers, local.agents)

  # one identity per workload (plus the BPM Functions host)
  identity_keys = concat(keys(local.http_apps), keys(local.workers), ["bpm-functions"])
  registrations = merge(
    { for k, v in local.http_apps : k => v.reg },
    { for k, v in local.workers : k => v.reg },
    { bpm-functions = "bpm-invoice-orchestrator" },
  )

  secret_readers = ["tool-gateway", "identity-gateway", "mcp-sap-orders", "mcp-servicenow-incidents", "mcp-sql-warehouse"]

  service_urls  = { for k, _ in local.http_apps : "AIIP_${upper(replace(k, "-", "_"))}_URL" => "http://${k}" }
  mi_principals = join(",", [for k in local.identity_keys : "${module.identity.principal_ids[k]}=${local.registrations[k]}"])

  common_env = merge({
    AIIP_MODE                             = "azure"
    HOST                                  = "0.0.0.0"
    AZURE_TENANT_ID                       = local.tenant_id
    APPLICATIONINSIGHTS_CONNECTION_STRING = module.monitoring.app_insights_connection_string
    AIIP_KEYVAULT_NAME                    = module.keyvault.name
    AIIP_SERVICEBUS_NAMESPACE             = module.servicebus.fqdn
    AIIP_EVENTGRID_TOPIC_ENDPOINT         = module.eventgrid.endpoint
    AIIP_MI_PRINCIPALS                    = local.mi_principals
  }, local.service_urls)
}
