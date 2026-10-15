# Ember Dashboard

Browser-based admin panel for Ember. Log in with Discord, pick a server, and configure any loaded feature — no slash commands required.

---

## What it does

- Discord OAuth2 login (only shows servers where you have **Manage Server** and Ember is present)
- Auto-generates config forms from the feature registry — every cog's settings appear automatically
- Auto-saves on change (no Save button)
- Reads/writes directly through Ember's Store — changes are live immediately

---

## Setup

### 1. Install dependencies

```bash
pip install -r Ember-extras/dashboard/requirements.txt
```

### 2. Create a Discord OAuth2 application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Open your bot application → **OAuth2** → **Redirects**
3. Add: `http://your-server-ip:8080/callback`
4. Copy the **Client ID** and **Client Secret**

### 3. Add env vars to your `.env`

```env
EMBER_DASHBOARD_SECRET=<random 32-char string>
DISCORD_OAUTH2_CLIENT_ID=<your client id>
DISCORD_OAUTH2_CLIENT_SECRET=<your client secret>
DISCORD_OAUTH2_REDIRECT_URI=http://your-server-ip:8080/callback

# Optional — defaults shown
EMBER_DASHBOARD_HOST=0.0.0.0
EMBER_DASHBOARD_PORT=8080
```

Generate a secret: `python3 -c "import secrets; print(secrets.token_hex(32))"`

### 4. Link and load

```bash
cd /path/to/Ember
ln -s ../../Ember-extras/dashboard cogs/admin/dashboard
,cog load dashboard
```

The dashboard starts on `http://your-server-ip:8080`.

---

## Docker

The dashboard runs inside the bot container. Expose port 8080 in `docker-compose.yml`:

```yaml
services:
  ember:
    ports:
      - "8080:8080"
```

---

## Reverse proxy (recommended for production)

Put nginx or Caddy in front so you get HTTPS. Example Caddyfile:

```
dashboard.yourserver.com {
    reverse_proxy localhost:8080
}
```

Update `DISCORD_OAUTH2_REDIRECT_URI` to match the HTTPS URL and add it in the Discord dev portal.
