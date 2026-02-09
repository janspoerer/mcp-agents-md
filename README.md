# MCP Agent Memory Server

A lightweight, secure **Model Context Protocol (MCP)** server designed to act as your centralized, persistent memory for AI agents (like Claude Code). It runs on a VPS, exposes tools to read and write to a markdown file (`AGENTS.md`), and automatically backs up data to Google Drive or via email.

## Features

* **Shared Memory:** A central `AGENTS.md` file that persists across sessions
* **MCP Compliant:** Exposes `read_memory` and `write_memory` tools compatible with Claude Desktop and Claude Code
* **Secure:** Protected via HTTPS and a custom `X-API-Key` header with Starlette middleware
* **Thread-Safe:** Uses file locking (`fcntl`) to prevent race conditions during simultaneous access
* **Append-Only:** Prevents accidental deletion of history; agents can only add to the log
* **Rate Limited:** Configurable rate limiting to prevent abuse (default: 60 requests/minute)
* **Input Validation:** Maximum rule size limit (default: 10KB) with automatic rejection of oversized writes
* **Audit Logging:** All security events and writes logged to separate audit file
* **Automated Backups:** Nightly cron job uploads the memory file to Google Drive with automatic rotation
* **Gitignored Data:** The memory file acts as a database and is not committed to the repo

---

## Prerequisites

* **Hetzner VPS** (Ubuntu 22.04+ recommended) or any Linux server
* **Domain Name** pointed to your VPS IP (e.g., `mcp.yourdomain.com`)
* **Google Cloud Service Account** with access to a shared Google Drive folder (for backups)

---

## Quick Start (Local Development)

### 1. Clone the Repository

```bash
git clone https://github.com/youruser/mcp-agent-memory.git
cd mcp-agent-memory
```

### 2. Run Setup Script

```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Create a virtual environment
- Install dependencies
- Generate a random API key
- Create `.env` from template

### 3. Start the Server

```bash
source venv/bin/activate
uvicorn app:app --reload
```

The server will start at `http://127.0.0.1:8000`.

### 4. Test the Server

```bash
pip install httpx  # For async HTTP client
python test_server.py
```

---

## Project Structure

```
/opt/mcp-server
├── app.py                # Main MCP server (FastAPI + official MCP SDK)
├── backup.py             # Backup logic (Google Drive with rotation)
├── test_server.py        # Test script for verification
├── setup.sh              # Local setup script
├── AGENTS.md             # The memory file (gitignored)
├── service_account.json  # Google credentials (gitignored)
├── .env                  # Secrets (gitignored)
├── .env.example          # Template for .env
├── .gitignore
└── requirements.txt
```

---

## Configuration

All configuration is done via environment variables in `.env`:

```ini
# Security (REQUIRED)
MCP_API_KEY=your_very_secure_random_string_here

# Server Configuration
MEMORY_FILE_PATH=AGENTS.md
AUDIT_LOG_PATH=audit.log
MAX_RULE_SIZE=10000

# Rate Limiting
RATE_LIMIT_REQUESTS=60
RATE_LIMIT_WINDOW=60

# Google Drive Backup
GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
GOOGLE_DRIVE_FOLDER_ID=your_folder_id_here
BACKUP_RETENTION_DAYS=30
```

---

## Deployment Guide (Hetzner VPS)

### 1. System Setup

SSH into your server and install dependencies:

```bash
apt update && apt upgrade -y
apt install python3-pip python3-venv nginx git certbot python3-certbot-nginx -y
```

### 2. Create Non-Root User (Security)

```bash
useradd -r -s /bin/false mcp-server
mkdir -p /opt/mcp-server
chown mcp-server:mcp-server /opt/mcp-server
```

### 3. Deploy Code

```bash
cd /opt/mcp-server
git clone https://github.com/youruser/mcp-agent-memory.git .

# Setup Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create configuration
cp .env.example .env
nano .env  # Fill in your values

# Upload service_account.json via SCP or create with nano
```

### 4. Systemd Service

Create `/etc/systemd/system/mcp-server.service`:

```ini
[Unit]
Description=MCP Agent Memory Server
After=network.target

[Service]
User=mcp-server
Group=mcp-server
WorkingDirectory=/opt/mcp-server
Environment="PATH=/opt/mcp-server/venv/bin"
ExecStart=/opt/mcp-server/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
systemctl daemon-reload
systemctl enable --now mcp-server
systemctl status mcp-server  # Verify it's running
```

### 5. Nginx Reverse Proxy (HTTPS)

Create `/etc/nginx/sites-available/mcp`:

```nginx
server {
    server_name mcp.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # SSE Support (Critical for MCP)
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
    }
}
```

Activate and secure with Certbot:

```bash
ln -s /etc/nginx/sites-available/mcp /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
certbot --nginx -d mcp.yourdomain.com
```

---

## Backup Configuration

The system uses `backup.py` to upload `AGENTS.md` to Google Drive with automatic rotation.

### Google Cloud Setup

1. Create a Project in Google Cloud Console
2. Enable the **Google Drive API**
3. Create a **Service Account** and download the JSON key
4. **Share** your target Google Drive folder with the Service Account's email (give it "Editor" permission)
5. Get the **Folder ID** from the URL of the folder

### Cron Job

Run `crontab -e` and add:

```cron
# Run backup every night at 3:00 AM
0 3 * * * cd /opt/mcp-server && ./venv/bin/python backup.py >> /var/log/mcp_backup.log 2>&1
```

### Manual Backup Commands

```bash
cd /opt/mcp-server
source venv/bin/activate

# Create backup now
python backup.py backup

# List all backups
python backup.py list

# View backup stats
python backup.py stats

# Cleanup old backups manually
python backup.py cleanup
```

---

## Connecting Agents

### Claude Code (CLI)

```bash
claude --mcp-server-url "https://mcp.yourdomain.com/mcp/sse" \
       --mcp-server-header "X-API-Key: your_secure_key"
```

### Claude Desktop App

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "remote-memory": {
      "command": "",
      "url": "https://mcp.yourdomain.com/mcp/sse",
      "headers": {
        "X-API-Key": "your_secure_key"
      }
    }
  }
}
```

---

## API Reference

### MCP Tools

#### `read_memory`
- **Description:** Read the AGENTS.md memory file containing shared agent knowledge
- **Returns:** Full markdown content of the memory file

#### `write_memory`
- **Description:** Append a new rule, learning, or note to the memory file
- **Arguments:**
  - `rule` (string): The text to append (max 10KB, automatically timestamped)
- **Returns:** Success message or error description

### REST API Endpoints

Alternative endpoints for clients that don't support MCP:

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/` | GET | No | Service info |
| `/health` | GET | No | Public health check |
| `/health/secure` | GET | Yes | Authenticated health check with config |
| `/api/memory` | GET | Yes | Read memory file |
| `/api/memory?rule=...` | POST | Yes | Write to memory file |
| `/docs` | GET | No | OpenAPI documentation |

---

## Security Notes

* **API Key:** The `X-API-Key` header is required for all MCP and protected REST requests
* **Rate Limiting:** Default 60 requests per minute per IP address
* **Input Validation:** Rules exceeding 10KB are rejected
* **Audit Logging:** All security events logged to `audit.log`
* **Data Persistence:** `AGENTS.md` is gitignored and exists only on disk
* **Non-Root Service:** Systemd runs as dedicated `mcp-server` user
* **Concurrency:** Write operations use exclusive file locks (`fcntl.LOCK_EX`)

---

## Monitoring

### View Logs

```bash
# Application logs
journalctl -u mcp-server -f

# Audit log (security events)
tail -f /opt/mcp-server/audit.log

# Backup log
tail -f /var/log/mcp_backup.log
```

### Health Check

```bash
# Public health check
curl https://mcp.yourdomain.com/health

# Authenticated health check
curl -H "X-API-Key: your_key" https://mcp.yourdomain.com/health/secure
```

---

## Troubleshooting

### Server won't start
- Check logs: `journalctl -u mcp-server -e`
- Verify `.env` exists and has `MCP_API_KEY` set
- Ensure port 8000 is available

### MCP connection fails
- Verify the SSE endpoint: `curl -H "X-API-Key: your_key" https://mcp.yourdomain.com/mcp/sse`
- Check nginx is proxying correctly: `nginx -t`
- Ensure SSL certificate is valid

### Backup fails
- Verify `service_account.json` exists and is valid
- Check the service account has access to the Google Drive folder
- Review backup log: `cat /var/log/mcp_backup.log`

---

## License

MIT

---

## Sources

This implementation uses:
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) - Protocol implementation
- [FastMCP Documentation](https://gofastmcp.com/integrations/fastapi) - FastAPI integration patterns
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework
