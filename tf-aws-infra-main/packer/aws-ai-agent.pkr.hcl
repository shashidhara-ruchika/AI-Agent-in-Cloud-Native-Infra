packer {
  required_plugins {
    amazon = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "aws_profile" {
  type    = string
  default = "default"
}

variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "source_ami_owner" {
  type    = string
  default = "099720109477" # Canonical (Ubuntu 24.04 LTS)
}

variable "ssh_username" {
  type    = string
  default = "ubuntu"
}

variable "ami_prefix" {
  type    = string
  default = "cloud-native-ai-agent"
}

data "amazon-ami" "ubuntu_2404" {
  filters = {
    name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
    root-device-type    = "ebs"
    virtualization-type = "hvm"
  }
  most_recent = true
  owners      = [var.source_ami_owner]
  region      = var.aws_region
}

source "amazon-ebs" "ai_agent_ami" {
  ami_name        = "${var.ami_prefix}-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  ami_description = "Custom AWS EC2 AMI with TypeScript & Python FastAPI AI Agent (GPT-4o mini via GitHub Models), DocumentDB TLS CA, and CloudWatch Agent"
  instance_type   = var.instance_type
  region          = var.aws_region
  profile         = var.aws_profile
  source_ami      = data.amazon-ami.ubuntu_2404.id
  ssh_username    = var.ssh_username

  launch_block_device_mappings {
    device_name           = "/dev/sda1"
    volume_size           = 25
    volume_type           = "gp3"
    delete_on_termination = true
    encrypted             = true
  }

  tags = {
    Name      = "${var.ami_prefix}-ami"
    BuiltWith = "Packer"
    Stack     = "TypeScript-Python-FastAPI-GPT4oMini-DocumentDB-RDS"
  }
}

build {
  name    = "build-cloud-native-ai-agent-ami"
  sources = ["source.amazon-ebs.ai_agent_ami"]

  provisioner "shell" {
    inline = [
      "sudo mkdir -p /opt/ai-agent /opt/ai-agent/certs",
      "sudo chown -R ubuntu:ubuntu /opt/ai-agent"
    ]
  }

  provisioner "file" {
    source      = "../../ai-agent-main/"
    destination = "/opt/ai-agent/"
  }

  provisioner "file" {
    source      = "./systemd/ai-agent-backend.service"
    destination = "/tmp/ai-agent-backend.service"
  }

  provisioner "shell" {
    script = "./scripts/setup.sh"
  }
}
