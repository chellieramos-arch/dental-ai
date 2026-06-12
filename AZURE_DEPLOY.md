# DentAI — Azure Deployment Guide (Phase 1)

> **Goal:** Run DentAI on Azure App Service B2 so NSU students can reach it at a public URL.
> Run these commands from your terminal. You need the [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) installed.

---

## Step 0 — One-time login

```bash
az login
az account set --subscription "<YOUR_SUBSCRIPTION_ID>"
```

---

## Step 1 — Create a Resource Group

```bash
az group create \
  --name dentai-rg \
  --location eastus2
```

---

## Step 2 — Create Azure Container Registry (ACR)

```bash
az acr create \
  --resource-group dentai-rg \
  --name dentaiacr \
  --sku Basic \
  --admin-enabled true
```

Get your ACR login server (you'll use this in the next step):

```bash
az acr show --name dentaiacr --query loginServer --output tsv
# → dentaiacr.azurecr.io
```

---

## Step 3 — Build and Push the Docker Image

```bash
# Log in to ACR
az acr login --name dentaiacr

# Build the image (run from your dental-ai/ directory)
cd /path/to/dental-ai
docker build -t dentaiacr.azurecr.io/dentai:latest .

# Push to ACR
docker push dentaiacr.azurecr.io/dentai:latest
```

> **Apple Silicon (M-series Mac):** Add `--platform linux/amd64` to the build command:
> ```bash
> docker build --platform linux/amd64 -t dentaiacr.azurecr.io/dentai:latest .
> ```

---

## Step 4 — Create App Service Plan (B2 tier)

```bash
az appservice plan create \
  --name dentai-plan \
  --resource-group dentai-rg \
  --sku B2 \
  --is-linux
```

---

## Step 5 — Create the Web App

```bash
# Get ACR credentials
ACR_PASSWORD=$(az acr credential show --name dentaiacr --query passwords[0].value --output tsv)

az webapp create \
  --resource-group dentai-rg \
  --plan dentai-plan \
  --name dentai-app \
  --deployment-container-image-name dentaiacr.azurecr.io/dentai:latest \
  --docker-registry-server-url https://dentaiacr.azurecr.io \
  --docker-registry-server-user dentaiacr \
  --docker-registry-server-password "$ACR_PASSWORD"
```

> Your app will be at: `https://dentai-app.azurewebsites.net`
> (Name must be globally unique — change `dentai-app` if it's taken.)

---

## Step 6 — Set Environment Variables

Paste your actual API keys in place of the placeholders:

```bash
az webapp config appsettings set \
  --resource-group dentai-rg \
  --name dentai-app \
  --settings \
    MODE=cloud \
    WEBSITES_PORT=8501 \
    ANTHROPIC_API_KEY="<your-key>" \
    OPENAI_API_KEY="<your-key>" \
    PINECONE_API_KEY="<your-key>" \
    PINECONE_INDEX="dentai" \
    SUPABASE_URL="<your-supabase-url>" \
    SUPABASE_KEY="<your-supabase-anon-key>"
```

> **Never commit these values to git.** They live only in App Service config.

---

## Step 7 — Enable Continuous Deployment from ACR (optional but recommended)

```bash
az webapp deployment container config \
  --enable-cd true \
  --resource-group dentai-rg \
  --name dentai-app
```

This auto-redeploys whenever you push a new image to ACR.

---

## Step 8 — Verify

```bash
# Stream the startup logs
az webapp log tail --resource-group dentai-rg --name dentai-app
```

Then open `https://dentai-app.azurewebsites.net` in a browser.

You should see the DentAI login screen with the Supabase OTP flow.

If the page loads but OTP emails don't arrive, go to Supabase → **Authentication → URL Configuration**
and add `https://dentai-app.azurewebsites.net` as the Site URL and a Redirect URL.

---

## Step 9 — Update Supabase Allowed URLs

In Supabase → Authentication → URL Configuration:
- **Site URL:** `https://dentai-app.azurewebsites.net`
- **Redirect URLs:** `https://dentai-app.azurewebsites.net`

---

## Redeploy After Code Changes

```bash
cd /path/to/dental-ai
docker build --platform linux/amd64 -t dentaiacr.azurecr.io/dentai:latest .
docker push dentaiacr.azurecr.io/dentai:latest
# If CD is enabled, App Service picks it up automatically within ~1 min.
# Otherwise: az webapp restart --resource-group dentai-rg --name dentai-app
```

---

## Cost Reference

| Resource | SKU | Est. Monthly |
|----------|-----|-------------|
| App Service Plan | B2 Linux | ~$75 |
| Container Registry | Basic | ~$5 |
| **Total** | | **~$80/mo** |

FERPA coverage: Microsoft's Online Services Terms (OST) covers Azure App Service automatically — no separate DPA needed. Sign DPAs for Pinecone and Supabase separately.

---

## Phase 1 Checklist

- [ ] `az login` done
- [ ] Resource group `dentai-rg` created (East US 2)
- [ ] ACR `dentaiacr` created
- [ ] Image built and pushed
- [ ] App Service Plan B2 created
- [ ] Web App created and pointing to ACR image
- [ ] All env vars set in App Service config (not .env)
- [ ] App loads at azurewebsites.net
- [ ] OTP login works (NSU email → 6-digit code → logged in)
- [ ] Supabase Site URL updated to Azure URL
