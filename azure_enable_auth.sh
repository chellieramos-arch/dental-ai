#!/bin/bash
# DentAI - Enable Azure Easy Auth (Microsoft login gate)
# Run: bash azure_enable_auth.sh
set -e

RESOURCE_GROUP="dentai-rg"
APP_NAME="dentai-app"
AUTH_APP_NAME="dentai-app-auth"
REDIRECT_URI="https://dentai-app.azurewebsites.net/.auth/login/aad/callback"

echo "==> Checking Azure login"
az account show --query name -o tsv >/dev/null 2>&1 || { echo "Not logged in — run: az login"; exit 1; }

TENANT_ID=$(az account show --query tenantId -o tsv)
echo "Tenant: $TENANT_ID"

# --- 1. App registration (reuse if exists) ---
echo ""
echo "==> Checking Entra ID app registration ($AUTH_APP_NAME)"
APP_ID=$(az ad app list --display-name "$AUTH_APP_NAME" --query '[0].appId' -o tsv 2>/dev/null)

if [ -z "$APP_ID" ] || [ "$APP_ID" = "None" ]; then
  echo "Creating new app registration..."
  APP_ID=$(az ad app create \
    --display-name "$AUTH_APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$REDIRECT_URI" \
    --query appId -o tsv)
  echo "Created: $APP_ID"
else
  echo "Found existing: $APP_ID"
fi

# --- 2. Client secret (always regenerate so we have it) ---
echo ""
echo "==> Generating client secret"
CLIENT_SECRET=$(az ad app credential reset \
  --id "$APP_ID" \
  --display-name "easy-auth-secret" \
  --years 2 \
  --query password -o tsv)

# --- 3. Enable auth using classic (v1) commands ---
echo ""
echo "==> Enabling Easy Auth on $APP_NAME (auth-classic)"
az webapp auth-classic update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$APP_NAME" \
  --enabled true \
  --action LoginWithAzureActiveDirectory \
  --aad-client-id "$APP_ID" \
  --aad-client-secret "$CLIENT_SECRET" \
  --aad-token-issuer-url "https://login.microsoftonline.com/$TENANT_ID/" \
  -o none

echo ""
echo "Done. https://dentai-app.azurewebsites.net now requires Microsoft login."
echo ""
echo "App ID : $APP_ID"
echo "Tenant : $TENANT_ID"
