# Deploy ProofLoop Blast Radius Zero on Vultr

This guide deploys the FastAPI control plane and the Docker sandbox on a Vultr Cloud Compute VM. The Vultr Serverless Inference key stays on the VM; the sandbox never receives it.

## 1. Create the VM

- Product: **Vultr Cloud Compute**
- OS: **Ubuntu 24.04 LTS**
- Plan: 2 vCPU / 4 GB RAM / 80 GB SSD (one demo run at a time; the sandbox uses 512 MB and 1 CPU)
- Region: close to your judges or users
- Enable IPv4; add your SSH key

In the Vultr firewall, allow:

| Port | Purpose |
| --- | --- |
| 22/tcp | SSH |
| 80/tcp | HTTP (Caddy ACME / plain HTTP) |
| 443/tcp | HTTPS (Caddy) |

Do not expose the app port 8000 publicly; Caddy proxies to it on loopback.

## 2. Bootstrap the VM

```bash
ssh root@<VM_IP>
curl -fsSL https://raw.githubusercontent.com/sijie-Z/proofloop-blast-radius/main/deploy/bootstrap.sh -o /root/bootstrap.sh
bash /root/bootstrap.sh
```

The script installs Docker, clones the repository to `/opt/proofloop-blast-radius`, builds `proofloop-sandbox:latest`, creates a virtualenv, installs the web dependencies and starts `proofloop-web.service`.

If you prefer to clone first:

```bash
git clone https://github.com/sijie-Z/proofloop-blast-radius.git /opt/proofloop-blast-radius
sudo bash /opt/proofloop-blast-radius/deploy/bootstrap.sh
```

## 3. Put the Vultr key on the control plane

```bash
sudo nano /etc/proofloop/proofloop.env
```

Set:

```text
VULTR_INFERENCE_API_KEY=<your Serverless Inference key>
```

Save, then:

```bash
sudo systemctl restart proofloop-web
curl http://127.0.0.1:8000/health
```

The file is `640 root:proofloop`. Never commit it or place the key in the sandbox.

## 4. Publish the demo URL

### With a domain (recommended)

Point an A/AAAA record at the VM, install Caddy, then:

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
sudo cp /opt/proofloop-blast-radius/deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/demo.example.com/<your-domain>/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Open `https://<your-domain>/`.

### Without a domain (quick demo)

Use the plain-HTTP block in `deploy/Caddyfile` and open `http://<VM_IP>/`. HTTPS is strongly preferred for a public submission.

## 5. Verify the deployment

```bash
bash /opt/proofloop-blast-radius/deploy/verify-demo.sh http://127.0.0.1:8000
```

In the browser:

1. Click **Probe the sandbox** — expect `network: blocked`, `secret_env: []`, 512 MB, 1 CPU.
2. Run **Bundled fix (keyless self-test)** — expect scene 1 PASS and scene 2 FAIL.
3. Run **Vultr agent (real LLM)** — the real agent run; expect scene 1 PASS and scene 2 FAIL.

This is the containment moment to record for the demo video: the probe runs inside a fresh container, tries the network, and is blocked; no secret is visible.

## 6. Operations

```bash
sudo systemctl status proofloop-web
sudo journalctl -u proofloop-web -f
```

Update:

```bash
sudo git -C /opt/proofloop-blast-radius pull --ff-only
sudo /opt/proofloop-blast-radius/.venv/bin/pip install -r /opt/proofloop-blast-radius/webapp/requirements.txt
sudo systemctl restart proofloop-web
```

The demo is public and rate-limited to 20 runs/hour per process. Lower it with `PROOFLOOP_DEMO_MAX_RUNS_PER_HOUR` if the URL is widely shared.

## 7. Optional: gVisor

For stronger isolation, install gVisor and run the sandbox with the `runsc` runtime:

```bash
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" | sudo tee /etc/apt/sources.list.d/gvisor.list
sudo apt update && sudo apt install -y runsc
sudo runsc install
sudo systemctl restart docker
echo "PROOFLOOP_DOCKER_RUNTIME=runsc" | sudo tee -a /etc/proofloop/proofloop.env
sudo systemctl restart proofloop-web
```

The containment probe still reports `network: blocked`; the kernel surface is reduced by gVisor.
