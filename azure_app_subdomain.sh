#!/bin/bash
# DentAI - Bind app.dentaiassist.com to dentai-app (run: bash azure_app_subdomain.sh)
# Safe to re-run. Tolerates the known az-cli cert-create error and polls for the cert.
set -e

RG=dentai-rg
APP=dentai-app
HOST=app.dentaiassist.com

az account show -o none 2>/dev/null || { echo "Not logged in. Run: az login"; exit 1; }

echo "==> Adding hostname $HOST (skipped if already added)"
az webapp config hostname list --webapp-name $APP --resource-group $RG \
  --query "[?name=='$HOST']" -o tsv | grep -q . || \
  az webapp config hostname add --webapp-name $APP --resource-group $RG --hostname $HOST -o none

echo "==> Creating free managed certificate for $HOST (skipped if it already exists)"
EXISTING=$(az webapp config ssl list --resource-group $RG \
  --query "[?subjectName=='$HOST'].thumbprint | [0]" -o tsv)
if [ -z "$EXISTING" ]; then
  az webapp config ssl create --resource-group $RG --name $APP --hostname $HOST -o none 2>/dev/null \
    || echo "    (ignoring known CLI error; verifying cert below)"
else
  echo "    Certificate already exists."
fi

echo "==> Waiting for certificate to appear (up to 4 min)"
THUMB=""
for i in $(seq 1 24); do
  THUMB=$(az webapp config ssl list --resource-group $RG \
    --query "[?subjectName=='$HOST'].thumbprint | [0]" -o tsv)
  [ -n "$THUMB" ] && break
  sleep 10
done
if [ -z "$THUMB" ]; then
  echo "    ERROR: no certificate yet. Wait a few minutes and re-run this script."
  exit 1
fi

echo "==> Binding certificate (SNI) for $HOST"
az webapp config ssl bind --resource-group $RG --name $APP \
  --certificate-thumbprint "$THUMB" --ssl-type SNI -o none

echo ""
echo "DONE. https://$HOST is live."
echo "Next: tell Claude — the A record flip to Netlify (marketing at root) happens after this."
