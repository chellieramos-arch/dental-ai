#!/bin/bash
# DentAI - Faculty/Admin dashboard deploy (run from the dental-ai folder: bash azure_deploy_admin.sh)
# Creates a SECOND web app (dentai-admin) on the EXISTING B2 plan — no extra hosting cost.
# Reuses the same Docker image as the student app; only the startup command differs.
# Prereq: run azure_deploy.sh at least once first (it builds/pushes the image).
set -e
cd "$(dirname "$0")"

echo "==> Checking Azure login"
az account show --query name -o tsv >/dev/null 2>&1 || { echo "Not logged in. Run: az login"; exit 1; }

echo "==> Web app (dentai-admin) on existing plan dentai-plan"
ACR_PASSWORD=$(az acr credential show --name dentaiacr --query "passwords[0].value" -o tsv)
az webapp show --name dentai-admin --resource-group dentai-rg -o none 2>/dev/null || \
  az webapp create \
    --resource-group dentai-rg \
    --plan dentai-plan \
    --name dentai-admin \
    --deployment-container-image-name dentaiacr.azurecr.io/dentai:latest \
    --docker-registry-server-url https://dentaiacr.azurecr.io \
    --docker-registry-server-user dentaiacr \
    --docker-registry-server-password "$ACR_PASSWORD" \
    -o none

echo "==> Startup command override: run admin_app.py instead of app.py"
az webapp config set --resource-group dentai-rg --name dentai-admin -o none \
  --startup-file "streamlit run admin_app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true"

echo "==> ADMIN_PASSWORD"
# Reuse ADMIN_PASSWORD from .env if present; otherwise generate one (shown ONCE below).
set -a; source ./.env; set +a
if [ -z "$ADMIN_PASSWORD" ]; then
  ADMIN_PASSWORD=$(openssl rand -base64 18)
  echo ""
  echo "  Generated ADMIN_PASSWORD: $ADMIN_PASSWORD"
  echo "  Save it now (e.g. add ADMIN_PASSWORD=... to your local .env). Faculty log in with it."
  echo ""
fi

echo "==> Setting environment variables"
az webapp config appsettings set --resource-group dentai-rg --name dentai-admin -o none --settings \
  MODE=cloud \
  WEBSITES_PORT=8501 \
  ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  OPENAI_API_KEY="$OPENAI_API_KEY" \
  PINECONE_API_KEY="$PINECONE_API_KEY" \
  PINECONE_INDEX="${PINECONE_INDEX:-dentai}" \
  SUPABASE_URL="$SUPABASE_URL" \
  SUPABASE_KEY="$SUPABASE_KEY"

echo "==> Always On + WebSockets (Streamlit needs both)"
az webapp config set --resource-group dentai-rg --name dentai-admin --always-on true --web-sockets-enabled true -o none

echo "==> Enabling continuous deployment from ACR (same image as student app)"
az webapp deployment container config --enable-cd true --resource-group dentai-rg --name dentai-admin -o none

echo "==> Restarting app"
az webapp restart --resource-group dentai-rg --name dentai-admin -o none

echo ""
echo "DONE. Admin dashboard will be live at: https://dentai-admin.azurewebsites.net"
echo "First boot can take 2-5 minutes while the container pulls."
echo "Note: future 'bash azure_deploy.sh' image pushes auto-update BOTH apps via CD."
