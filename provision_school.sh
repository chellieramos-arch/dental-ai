#!/bin/bash
# DentAI - Provision a NEW SCHOOL deployment on Azure (instance-per-school model)
#
# Usage:
#   bash provision_school.sh <school_id>
#
# Reads per-school config from a file named  .env.<school_id>  in this folder.
# Copy .env.school.example to .env.<school_id> and fill it in first.
#
# What it does:
#   1. Creates web apps <school_id>-app and <school_id>-admin on the EXISTING
#      dentai-plan (no extra hosting cost), using the same Docker image.
#   2. Sets all env vars (school identity + that school's own Supabase/Pinecone).
#   3. Binds <school_id>.dentaiassist.com + managed cert (after you add DNS).
#   4. Prints the DNS records to add at GoDaddy.
#
# Per-school isolation checklist (do these BEFORE running):
#   - Create a new Supabase project for the school (run supabase_profiles_migration.sql)
#   - Create a new Pinecone index for the school
#   - Copy .env.school.example -> .env.<school_id> and fill everything in
set -e
cd "$(dirname "$0")"

RG=dentai-rg
PLAN=dentai-plan
ACR=dentaiacr
IMAGE=dentaiacr.azurecr.io/dentai:latest
ROOT_DOMAIN=dentaiassist.com

SCHOOL_ID=${1:?Usage: bash provision_school.sh <school_id>   (e.g. provision_school.sh ufl)}
ENVFILE=".env.$SCHOOL_ID"
[ -f "$ENVFILE" ] || { echo "Missing $ENVFILE — copy .env.school.example and fill it in."; exit 1; }

az account show -o none 2>/dev/null || { echo "Not logged in. Run: az login"; exit 1; }

set -a; source "./$ENVFILE"; set +a
: "${SCHOOL_NAME:?$ENVFILE must set SCHOOL_NAME}"
: "${ALLOWED_EMAIL_DOMAINS:?$ENVFILE must set ALLOWED_EMAIL_DOMAINS}"
: "${SUPABASE_URL:?$ENVFILE must set SUPABASE_URL (this school's own Supabase project)}"
: "${SUPABASE_KEY:?$ENVFILE must set SUPABASE_KEY}"
: "${PINECONE_INDEX:?$ENVFILE must set PINECONE_INDEX (this school's own index)}"

APP_NAME="dentai-$SCHOOL_ID"
ADMIN_NAME="dentai-$SCHOOL_ID-admin"
HOST="$SCHOOL_ID.$ROOT_DOMAIN"
ADMIN_HOST="$SCHOOL_ID-admin.$ROOT_DOMAIN"

ACR_PASSWORD=$(az acr credential show --name $ACR --query "passwords[0].value" -o tsv)
VERIFY_ID=$(az webapp show --name dentai-app --resource-group $RG --query customDomainVerificationId -o tsv)

make_app () {  # $1 = app name, $2 = startup command override ("" for default)
  echo "==> Web app $1 on $PLAN"
  az webapp show --name "$1" --resource-group $RG -o none 2>/dev/null || \
    az webapp create \
      --resource-group $RG --plan $PLAN --name "$1" \
      --deployment-container-image-name $IMAGE \
      --docker-registry-server-url https://$ACR.azurecr.io \
      --docker-registry-server-user $ACR \
      --docker-registry-server-password "$ACR_PASSWORD" \
      -o none
  if [ -n "$2" ]; then
    az webapp config set --resource-group $RG --name "$1" --startup-file "$2" -o none
  fi
  az webapp config set --resource-group $RG --name "$1" --always-on true --web-sockets-enabled true -o none
  az webapp deployment container config --enable-cd true --resource-group $RG --name "$1" -o none
}

set_school_env () {  # $1 = app name, $2 = app url
  az webapp config appsettings set --resource-group $RG --name "$1" -o none --settings \
    MODE=cloud \
    WEBSITES_PORT=8501 \
    SCHOOL_ID="$SCHOOL_ID" \
    SCHOOL_NAME="$SCHOOL_NAME" \
    SCHOOL_NAME_ES="${SCHOOL_NAME_ES:-}" \
    SCHOOL_SHORT="${SCHOOL_SHORT:-$(echo $SCHOOL_ID | tr a-z A-Z)}" \
    UNIVERSITY_NAME="${UNIVERSITY_NAME:-$SCHOOL_NAME}" \
    PROGRAM_LABEL="${PROGRAM_LABEL:-Student Study Portal}" \
    ALLOWED_EMAIL_DOMAINS="$ALLOWED_EMAIL_DOMAINS" \
    EMAIL_PLACEHOLDER="${EMAIL_PLACEHOLDER:-}" \
    SEAL_FILE="${SEAL_FILE:-}" \
    APP_URL="$2" \
    ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    OPENAI_API_KEY="$OPENAI_API_KEY" \
    PINECONE_API_KEY="$PINECONE_API_KEY" \
    PINECONE_INDEX="$PINECONE_INDEX" \
    SUPABASE_URL="$SUPABASE_URL" \
    SUPABASE_KEY="$SUPABASE_KEY" \
    ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
}

make_app "$APP_NAME" ""
set_school_env "$APP_NAME" "https://$HOST"

make_app "$ADMIN_NAME" "streamlit run admin_app.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true"
set_school_env "$ADMIN_NAME" "https://$HOST"

az webapp restart --resource-group $RG --name "$APP_NAME" -o none
az webapp restart --resource-group $RG --name "$ADMIN_NAME" -o none

echo ""
echo "==> Apps created:"
echo "    Student: https://$APP_NAME.azurewebsites.net"
echo "    Admin:   https://$ADMIN_NAME.azurewebsites.net"
echo ""
echo "==> Add these DNS records at GoDaddy ($ROOT_DOMAIN > Manage DNS):"
echo "    CNAME  $SCHOOL_ID            $APP_NAME.azurewebsites.net"
echo "    TXT    asuid.$SCHOOL_ID      $VERIFY_ID"
echo "    CNAME  $SCHOOL_ID-admin      $ADMIN_NAME.azurewebsites.net"
echo "    TXT    asuid.$SCHOOL_ID-admin  $VERIFY_ID"
echo ""
echo "==> After DNS propagates (~15 min), bind the domains:"
echo "    bash azure_bind_host.sh $APP_NAME $HOST"
echo "    bash azure_bind_host.sh $ADMIN_NAME $ADMIN_HOST"
echo ""
echo "==> Then:"
echo "    1. Supabase Auth: set Site URL + redirect URLs to https://$HOST"
echo "    2. Ingest the school's documents: MODE=cloud PINECONE_INDEX=$PINECONE_INDEX python ingest.py"
echo "    3. Add the school to the find-your-school page (schools.html) and redeploy Netlify"
