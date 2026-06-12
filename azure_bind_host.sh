#!/bin/bash
# DentAI - Bind any hostname to any web app with a free managed cert.
# Usage: bash azure_bind_host.sh <webapp-name> <hostname>
# Example: bash azure_bind_host.sh dentai-admin admin.dentaiassist.com
# Safe to re-run; tolerates flaky az-cli network errors.
set -e

RG=dentai-rg
APP=${1:?Usage: bash azure_bind_host.sh <webapp-name> <hostname>}
HOST=${2:?Usage: bash azure_bind_host.sh <webapp-name> <hostname>}

az account show -o none 2>/dev/null || { echo "Not logged in. Run: az login"; exit 1; }

echo "==> Adding hostname $HOST to $APP (skipped if already added)"
az webapp config hostname list --webapp-name $APP --resource-group $RG \
  --query "[?name=='$HOST']" -o tsv | grep -q . || \
  az webapp config hostname add --webapp-name $APP --resource-group $RG --hostname $HOST -o none

echo "==> Managed certificate for $HOST (skipped if it already exists)"
EXISTING=$(az webapp config ssl list --resource-group $RG \
  --query "[?subjectName=='$HOST'].thumbprint | [0]" -o tsv)
if [ -z "$EXISTING" ]; then
  az webapp config ssl create --resource-group $RG --name $APP --hostname $HOST \
    || echo "    (cert create reported an error; verifying below — often it succeeded anyway)"
fi

echo "==> Waiting for certificate (up to 4 min)"
THUMB=""
for i in $(seq 1 24); do
  THUMB=$(az webapp config ssl list --resource-group $RG \
    --query "[?subjectName=='$HOST'].thumbprint | [0]" -o tsv)
  [ -n "$THUMB" ] && break
  sleep 10
done
[ -z "$THUMB" ] && { echo "    ERROR: no cert yet. Re-run this script in a few minutes."; exit 1; }

echo "==> Binding certificate (SNI)"
az webapp config ssl bind --resource-group $RG --name $APP \
  --certificate-thumbprint "$THUMB" --ssl-type SNI -o none

echo ""
echo "DONE. https://$HOST is live on $APP."
