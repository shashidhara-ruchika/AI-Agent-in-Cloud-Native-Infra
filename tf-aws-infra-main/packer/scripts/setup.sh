#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

echo "[Packer] Updating apt repositories and installing core system dependencies..."
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get install -y \
  curl \
  wget \
  unzip \
  git \
  jq \
  python3 \
  python3-pip \
  python3-venv \
  postgresql-client \
  ca-certificates

echo "[Packer] Installing Node.js 20.x LTS for TypeScript AI Agent..."
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

echo "[Packer] Downloading AWS DocumentDB Global TLS Certificate Bundle..."
sudo mkdir -p /opt/ai-agent/certs
sudo wget -q https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -O /opt/ai-agent/certs/global-bundle.pem
sudo chmod 644 /opt/ai-agent/certs/global-bundle.pem

echo "[Packer] Installing Amazon CloudWatch Agent..."
wget -q https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb -O /tmp/amazon-cloudwatch-agent.deb
sudo dpkg -i -E /tmp/amazon-cloudwatch-agent.deb
rm -f /tmp/amazon-cloudwatch-agent.deb

echo "[Packer] Setting up Python FastAPI virtual environment and dependencies..."
cd /opt/ai-agent
python3 -m venv /opt/ai-agent/.venv
/opt/ai-agent/.venv/bin/pip install --upgrade pip
/opt/ai-agent/.venv/bin/pip install -r /opt/ai-agent/backend/requirements.txt

echo "[Packer] Installing TypeScript AI Agent npm dependencies..."
npm install --prefix /opt/ai-agent

echo "[Packer] Configuring systemd service for FastAPI + TypeScript AI Agent..."
sudo mv /tmp/ai-agent-backend.service /etc/systemd/system/ai-agent-backend.service
sudo chmod 644 /etc/systemd/system/ai-agent-backend.service
sudo systemctl daemon-reload
sudo systemctl enable ai-agent-backend.service

echo "[Packer] AMI provisioning complete!"
