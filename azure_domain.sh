#!/bin/bash
# DentAI - Custom domain setup for dentaiassist.com (run from dental-ai folder)
#
# Two phases:
#   bash azure_domain.sh records   -> prints the DNS records to add at GoDaddy
#   bash azure_domain.sh bind      -> after DNS propagates, binds domain + free managed SSL
set -e

RG=dentai-rg
APP=dentai-app
DOMAIN=dentaiassist.com

az account show -o none 2>/dev/null || { echo "Not logged in. Run: az login"; exit 1; }

case "${1:-records}" in

records)
  echo "==> Values for your GoDaddy DNS records (My Products > $DOMAIN > Manage DNS)"
  IP=$(az webapp show --name $APP --resource-group $RG --query inboundIpAddress -o tsv)
  VERIFY_ID=$(az webapp show --name $APP --resource-group $RG --query customDomainVerificationId -o tsv)
  echo ""
  echo "  Add these 4 records:"
  echo "  ┌──────┬──────────────┬─────────────────────────────────────┐"
  echo "  Type   Name           Value"
  echo "  A      @              $IP"
  echo "  TXT    asuid          $VERIFY_ID"
  echo "  CNAME  www            $APP.azurewebsites.net"
  echo "  TXT    asuid.www      $VERIFY_ID"
  echo ""
  echo "  Delete any existing A/CNAME records for @ and www first (e.g. GoDaddy 'Parked' A record)."
  echo "  Then wait ~10-30 min for DNS to propagate and run: bash azure_domain.sh bind"
  ;;

bind)
  echo "==> Checking DNS propagation"
  IP=$(az webapp show --name $APP --resource-group $RG --query inboundIpAddress -o tsv)
  RESOLVED=$(dig +short $DOMAIN A | tail -1)
  if [ "$RESOLVED" != "$IP" ]; then
    echo "  WARNING: $DOMAIN resolves to '$RESOLVED', expected $IP. DNS may still be propagating."
    echo "  Continuing anyway — Azure will reject the hostname if verification fails."
  fi

  for HOST in $DOMAIN www.$DOMAIN; do
    echo "==> Adding hostname $HOST (skipped if already added)"
    az webapp config hostname list --webapp-name $APP --resource-group $RG \
      --query "[?name=='$HOST']" -o tsv | grep -q . || \
      az webapp config hostname add --webapp-name $APP --resource-group $RG --hostname $HOST -o none

    echo "==> Creating free managed certificate for $HOST"
    # Known azure-cli bug: this preview command can throw a JSON deserialization
    # error even when the cert IS created. Ignore the error; verify by thumbprint below.
    az webapp config ssl create --resource-group $RG --name $APP --hostname $HOST -o none 2>/dev/null \
      || echo "    (ignoring known CLI deserialization error; verifying cert below)"

    echo "==> Waiting for certificate to appear"
    THUMB=""
    for i in $(seq 1 12); do
      THUMB=$(az webapp config ssl list --resource-group $RG \
        --query "[?subjectName=='$HOST'].thumbprint | [0]" -o tsv)
      [ -n "$THUMB" ] && break
      sleep 10
    done
    if [ -z "$THUMB" ]; then
      echo "    ERROR: no certificate found for $HOST after 2 min. Re-run 'bash azure_domain.sh bind' in a few minutes."
      exit 1
    fi

    echo "==> Binding certificate (SNI) for $HOST"
    az webapp config ssl bind --resource-group $RG --name $APP \
      --certificate-thumbprint "$THUMB" --ssl-type SNI -o none
  done

  echo "==> Enforcing HTTPS"
  az webapp update --resource-group $RG --name $APP --https-only true -o none

  echo ""
  echo "DONE. Test: https://$DOMAIN and https://www.$DOMAIN"
  echo "Remember: update Supabase Auth Site URL + redirect URLs to https://$DOMAIN"
  ;;

*)
  echo "Usage: bash azure_domain.sh [records|bind]"
  exit 1
  ;;
esac
