#!/bin/bash
# One-time: push SUPABASE_SERVICE_KEY to Azure App Service and restart
# Run from dental-ai folder: bash set_service_key.sh
set -e
cd "$(dirname "$0")"
set -a; source ./.env; set +a

echo "==> Setting SUPABASE_SERVICE_KEY on Azure..."
az webapp config appsettings set \
  --resource-group dentai-rg \
  --name dentai-app \
  --settings SUPABASE_SERVICE_KEY="$SUPABASE_SERVICE_KEY" \
  -o none

echo "==> Restarting app..."
az webapp restart --resource-group dentai-rg --name dentai-app -o none

echo "DONE. App restarting — give it ~60 seconds."
