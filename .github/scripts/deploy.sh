#!/usr/bin/env bash
# Deployment steps used by .github/workflows/deploy.yml and teardown.yml. Each subcommand is
# idempotent and reads its inputs from the environment:
#   DEPLOY_TOOL   terraform | bicep
#   TARGET_ENV    dev | prod
#   LOCATION      Azure region (default eastus2)
#   IMAGE_TAG     tag of the images built by the build job
#   ARM_* / AZURE_*  set by azure/login (OIDC) and the workflow env
#
#   deploy.sh provision   create/update infra, write rg/acr/url to $GITHUB_OUTPUT
#   deploy.sh push        load the image artifacts and push them to this environment's ACR
#   deploy.sh import      copy images from SOURCE_ACR (dev) into this environment's ACR (prod)
#   deploy.sh roll        point every Container App (gateways, MCP servers, agents, workers) at the image
#   deploy.sh functions   package and zip-deploy the Durable Functions app
#   deploy.sh smoke       post-deploy checks against the public endpoint
#   deploy.sh destroy     tear the environment down (teardown workflow only)
set -euo pipefail

TOOL="${DEPLOY_TOOL:-terraform}"
ENV_NAME="${TARGET_ENV:?TARGET_ENV is required}"
LOCATION="${LOCATION:-eastus2}"
STACK="infra/terraform"
AZD_ENV="aiip-${ENV_NAME}"
IMAGES=(aiip)
OUT="${GITHUB_OUTPUT:-/dev/stdout}"

log() { echo "::group::$*"; }
end() { echo "::endgroup::"; }

tf_init() {
  : "${TFSTATE_RESOURCE_GROUP:?set repo/environment variable TFSTATE_RESOURCE_GROUP}"
  : "${TFSTATE_STORAGE_ACCOUNT:?set repo/environment variable TFSTATE_STORAGE_ACCOUNT}"
  terraform -chdir="$STACK" init -input=false \
    -backend-config="envs/${ENV_NAME}.backend.hcl" \
    -backend-config="resource_group_name=${TFSTATE_RESOURCE_GROUP}" \
    -backend-config="storage_account_name=${TFSTATE_STORAGE_ACCOUNT}" \
    -backend-config="container_name=${TFSTATE_CONTAINER:-tfstate}"
}

deployer_object_id() { az ad sp show --id "$ARM_CLIENT_ID" --query id -o tsv; }

provision() {
  if [[ "$TOOL" == "terraform" ]]; then
    log "terraform apply ($ENV_NAME)"
    tf_init
    terraform -chdir="$STACK" apply -auto-approve -input=false \
      -var-file="envs/${ENV_NAME}.tfvars" -var "location=${LOCATION}"
    rg=$(terraform -chdir="$STACK" output -raw AZURE_RESOURCE_GROUP)
    acr=$(terraform -chdir="$STACK" output -raw AZURE_CONTAINER_REGISTRY_NAME)
    fn=$(terraform -chdir="$STACK" output -raw FUNCTION_APP_NAME)
    end
  else
    log "bicep: az deployment sub create ($AZD_ENV)"
    if [[ "$ENV_NAME" == "prod" ]]; then
      shape=(minReplicas=1 privateNetworking=true deployFrontDoor=true apimSku=StandardV2 logDailyQuotaGb=-1)
    else
      shape=(minReplicas=0)
    fi
    outputs=$(az deployment sub create --name "${AZD_ENV}-${GITHUB_RUN_ID:-local}" \
      --location "$LOCATION" --template-file infra/main.bicep \
      --parameters environmentName="$AZD_ENV" location="$LOCATION" "${shape[@]}" \
      --query properties.outputs -o json)
    rg=$(jq -r .AZURE_RESOURCE_GROUP.value <<<"$outputs")
    acr=$(jq -r .AZURE_CONTAINER_REGISTRY_ENDPOINT.value <<<"$outputs" | cut -d. -f1)
    fn=$(jq -r .FUNCTION_APP_NAME.value <<<"$outputs")
    end
  fi
  fqdn=$(az containerapp show -g "$rg" -n tool-gateway --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null || true)
  url=${fqdn:+https://$fqdn}
  { echo "resource_group=$rg"; echo "acr_name=$acr"; echo "app_url=$url"; echo "function_app=$fn"; } >>"$OUT"
}

push() {
  : "${ACR_NAME:?}" "${IMAGE_TAG:?}"
  az acr login --name "$ACR_NAME"
  for img in "${IMAGES[@]}"; do
    docker load -i "images/${img}.tar"
    docker tag "${img}:${IMAGE_TAG}" "${ACR_NAME}.azurecr.io/${img}:${IMAGE_TAG}"
    docker push "${ACR_NAME}.azurecr.io/${img}:${IMAGE_TAG}"
  done
}

import() {
  : "${ACR_NAME:?}" "${SOURCE_ACR:?}" "${IMAGE_TAG:?}"
  for img in "${IMAGES[@]}"; do
    az acr import --name "$ACR_NAME" --source "${SOURCE_ACR}.azurecr.io/${img}:${IMAGE_TAG}" \
      --image "${img}:${IMAGE_TAG}" --force
  done
}

image_for() { echo aiip; } # one image for every workload; the command differs per app

roll() {
  : "${RESOURCE_GROUP:?}" "${ACR_NAME:?}" "${IMAGE_TAG:?}"
  for app in $(az containerapp list -g "$RESOURCE_GROUP" --query "[].name" -o tsv); do
    img="${ACR_NAME}.azurecr.io/$(image_for "$app"):${IMAGE_TAG}"
    echo "rolling $app -> $img"
    az containerapp update -g "$RESOURCE_GROUP" -n "$app" --image "$img" --only-show-errors -o none
  done
}

smoke() {
  : "${APP_URL:?}"
  for i in $(seq 1 30); do
    if curl -fsS "${APP_URL}/healthz"; then echo; break; fi
    [[ $i == 30 ]] && { echo "::error::${APP_URL}/healthz never became healthy"; exit 1; }
    sleep 10
  done
}

functions() {
  : "${RESOURCE_GROUP:?}" "${FUNCTION_APP:?}"
  sh scripts/package_functions.sh
  (cd functions && zip -qr ../functions.zip . -x '__pycache__/*')
  az functionapp deployment source config-zip -g "$RESOURCE_GROUP" -n "$FUNCTION_APP" \
    --src functions.zip --build-remote true -o none
}

destroy() {
  if [[ "$TOOL" == "terraform" ]]; then
    tf_init
    terraform -chdir="$STACK" destroy -auto-approve -input=false \
      -var-file="envs/${ENV_NAME}.tfvars" -var "location=${LOCATION}"
  else
    az group delete --name "rg-${AZD_ENV}" --yes
  fi
}

"$@"
