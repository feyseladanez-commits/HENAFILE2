# Deploying to AletCloud

## Which AletCloud product to use

AletCloud has two relevant products: **App Hosting** (`git push`, they build
it, one HTTPS URL - like Heroku/Render) and **Cloud VPS** (a plain Ubuntu
server with root access, billed hourly).

This bot needs **Cloud VPS**, not App Hosting. App Hosting gives an app a
single public port/process. This project is a single long-running Python
process that:
- keeps a persistent Telegram **long-poll** connection open (no inbound
  webhook needed for Telegram itself),
- dynamically spawns one background task *per host* the first time you run
  `/newhost`, and
- opens a **separate local HTTP port per host** (starting at `9001`) so each
  host's SMS-forwarder app can push payment SMS to it, plus one more local
  port per host for its internal control API.

That "one process, N locally-bound ports, fronted by Nginx" shape needs full
root + Nginx, which is exactly what Cloud VPS gives you and App Hosting
doesn't. If you'd rather use App Hosting, the code would need to be
restructured around one shared public port (a single internal router
dispatching by URL path to the right host) - ask if you want that version
instead.

## 1. Create the VPS

1. Sign in at aletcloud.com and open the console.
2. Create a server: pick **Ubuntu** (22.04 or 24.04), 1 vCPU / 1-2GB RAM is
   plenty to start (each host adds a lightweight asyncio task + aiohttp
   server, not a new OS process).
3. Point a domain (or subdomain, e.g. `sms.yourdomain.com`) at the server's
   IP from the AletCloud Domains page, or your existing DNS provider - an
   A record is enough. This domain is only used for the SMS-webhook
   endpoint; the bot itself doesn't need a domain since Telegram talks to it
   via outbound long-polling.
4. SSH in: `ssh root@<your-server-ip>`.

## 2. System setup

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git

# Run the bot as its own unprivileged user, not root
useradd --system --create-home --shell /usr/sbin/nologin jemo
mkdir -p /opt/jemo
chown jemo:jemo /opt/jemo
```

## 3. Upload the code

From your machine (not the server):

```bash
scp superadmin_all_in_one.py requirements.txt .env.example root@<your-server-ip>:/opt/jemo/
scp -r deploy root@<your-server-ip>:/opt/jemo/
```

(Or `git clone` your own repo into `/opt/jemo` if you keep this in git -
either way, `superadmin_all_in_one.py` must end up directly inside
`/opt/jemo/`, since it writes its state files - `superadmin_state.json` and
each host's `hosts/<HOST_ID>/lottery_state.json` - next to itself.)

## 4. Python environment

```bash
cd /opt/jemo
sudo -u jemo python3 -m venv venv
sudo -u jemo venv/bin/pip install --upgrade pip
sudo -u jemo venv/bin/pip install -r requirements.txt
```

## 5. Configure secrets

```bash
cd /opt/jemo
cp .env.example .env
nano .env   # fill in SUPERADMIN_BOT_TOKEN, SUPER_ADMIN_ID, seller payment
            # accounts, and CREDIT_SELLER_API_SECRET (generate with:
            # python3 -c "import secrets; print(secrets.token_urlsafe(32))")
chown jemo:jemo .env
chmod 600 .env
```

> ⚠️ `superadmin_all_in_one.py` ships with a hardcoded fallback bot token
> and admin ID in the source (used only if the env vars above are unset).
> Once your `.env` is in place those fallbacks are never used, but treat the
> file as sensitive anyway - don't commit `.env` to git, don't leave the
> hardcoded fallback token active in production, and rotate that token with
> @BotFather since it's already been shared in this conversation.

## 6. Run it as a systemd service

```bash
cp /opt/jemo/deploy/jemo-superadmin.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jemo-superadmin
systemctl status jemo-superadmin   # should show "active (running)"
journalctl -u jemo-superadmin -f   # live logs
```

The service restarts automatically on crash or reboot. State files
(`lottery_state.json` per host, `superadmin_state.json`) live on disk in
`/opt/jemo`, so a restart doesn't lose rounds/tickets/credit - only an
`rm -rf /opt/jemo/hosts` or deleting the VPS would.

## 7. Expose the SMS webhook publicly (Nginx + HTTPS)

```bash
cp /opt/jemo/deploy/nginx_jemo.conf.template /etc/nginx/sites-available/jemo
nano /etc/nginx/sites-available/jemo   # replace sms.yourdomain.com
ln -s /etc/nginx/sites-available/jemo /etc/nginx/sites-enabled/jemo
nginx -t && systemctl reload nginx

# Get a free HTTPS cert (auto-renews via a systemd timer certbot installs)
certbot --nginx -d sms.yourdomain.com
```

## 8. Register a host and wire up its webhook

1. Message your Super Admin bot on Telegram: `/newhost`, give it the
   host's bot token + the host admin's Telegram ID.
2. Run `/host <HOST_ID>` in the Super Admin bot - it now shows the host's
   **SMS Webhook port**, e.g. `9001`.
3. On the server:
   ```bash
   sudo /opt/jemo/deploy/add_host_nginx.sh H1 9001
   ```
   This appends the Nginx `location` block for that host and reloads
   Nginx. Repeat step 2-3 for every new host.
4. Give the host their public webhook URL to paste into their SMS-forwarder
   Android app (e.g. `SMS Forwarder`, `MacroDroid`):
   ```
   https://sms.yourdomain.com/H1/sms-webhook?token=<their WEBHOOK_SECRET>
   ```
   (The default `WEBHOOK_SECRET` is a fixed value baked into `jemo_2.py`
   shared by every host - fine to start with, but if you want per-host
   secrets so one leaked URL can't be replayed against other hosts, that's
   a follow-up change, not part of this deployment setup.)

The optional **`/setsmswebhook`** command (added earlier) is a *different,
outbound* relay - a host can point it at their own external URL to get a
copy of every SMS the bot receives, forwarded onward. It doesn't need
Nginx or any of the steps above; it only needs outbound internet access,
which the VPS already has.

## 9. Updating the code later

```bash
scp superadmin_all_in_one.py root@<your-server-ip>:/opt/jemo/
ssh root@<your-server-ip> "systemctl restart jemo-superadmin"
```

## Firewall

Only ports 80/443 (Nginx) and 22 (SSH) need to be open publicly. The
per-host webhook ports (`9001+`) and the Host Control API ports (`8082+`)
bind to `127.0.0.1` only in the code, so they're unreachable from outside
even without a firewall rule - Nginx (steps 7-8) is what makes the
`/sms-webhook` path reachable. If AletCloud's console offers a firewall/
security-group panel, restricting inbound to `22, 80, 443` is good hygiene
anyway.
