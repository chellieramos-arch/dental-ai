#!/bin/bash
# DentAI - Azure Phase 1 deploy (run from the dental-ai folder: bash azure_deploy.sh)
set -e
cd "$(dirname "$0")"

echo "==> Checking Azure login"
az account show --query name -o tsv >/dev/null 2>&1 || { echo "Not logged in. Run: az login"; exit 1; }

echo "==> Resource group (dentai-rg, East US 2)"
az group create --name dentai-rg --location eastus2 -o none

echo "==> Container registry (dentaiacr)"
az acr show --name dentaiacr -o none 2>/dev/null || \
  az acr create --resource-group dentai-rg --name dentaiacr --sku Basic --admin-enabled true -o none

echo "==> Rebuilding image with latest code and pushing to ACR"
az acr login --name dentaiacr
docker build --platform linux/amd64 -t dentaiacr.azurecr.io/dentai:latest .
docker push dentaiacr.azurecr.io/dentai:latest

echo "==> App Service plan (B2 Linux)"
az appservice plan show --name dentai-plan --resource-group dentai-rg -o none 2>/dev/null || \
  az appservice plan create --name dentai-plan --resource-group dentai-rg --sku B2 --is-linux -o none

echo "==> Web app (dentai-app)"
ACR_PASSWORD=$(az acr credential show --name dentaiacr --query "passwords[0].value" -o tsv)
az webapp show --name dentai-app --resource-group dentai-rg -o none 2>/dev/null || \
  az webapp create \
    --resource-group dentai-rg \
    --plan dentai-plan \
    --name dentai-app \
    --deployment-container-image-name dentaiacr.azurecr.io/dentai:latest \
    --docker-registry-server-url https://dentaiacr.azurecr.io \
    --docker-registry-server-user dentaiacr \
    --docker-registry-server-password "$ACR_PASSWORD" \
    -o none

echo "==> Setting environment variables from local .env"
set -a; source ./.env; set +a
az webapp config appsettings set --resource-group dentai-rg --name dentai-app -o none --settings \
  MODE=cloud \
  WEBSITES_PORT=8501 \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  OPENAI_API_KEY="$OPENAI_API_KEY" \
  PINECONE_API_KEY="$PINECONE_API_KEY" \
  PINECONE_INDEX="${PINECONE_INDEX:-dentai}" \
  SUPABASE_URL="$SUPABASE_URL" \
  SUPABASE_KEY="$SUPABASE_KEY" \
  SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY"

echo "==> Enabling continuous deployment from ACR"
az webapp deployment container config --enable-cd true --resource-group dentai-rg --name dentai-app -o none

echo "==> Restarting app"
az webapp restart --resource-group dentai-rg --name dentai-app -o none

echo ""
echo "DONE. App will be live at: https://dentai-app.azurewebsites.net"
echo "First boot can take 2-5 minutes while the container pulls."
