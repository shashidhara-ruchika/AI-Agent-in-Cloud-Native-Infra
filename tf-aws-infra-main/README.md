# tf-aws-infra: AWS Cloud-Native Infrastructure (Packer + Terraform)

Cloud-native **AWS** infrastructure provisioned with **Packer** and **Terraform** to deploy the TypeScript & Python FastAPI AI Agent (`openai/gpt-4o-mini` via GitHub Models), **AWS DocumentDB** (windowed persistent conversation history), **AWS RDS PostgreSQL**, **EC2 Auto Scaling**, **Application Load Balancer**, and **AWS SNS / SQS / Lambda** event pipelines.

---

## Architecture Highlights

### 1. Custom AMI Baking with Packer (`packer/aws-ai-agent.pkr.hcl`)
- Uses `amazon-ebs` on Ubuntu 24.04 LTS to bake an immutable, encrypted EC2 AMI.
- Pre-installs Python 3, FastAPI/Uvicorn, Node.js 20 LTS, TypeScript (`tsx`), the AWS DocumentDB global TLS CA bundle (`/opt/ai-agent/certs/global-bundle.pem`), and Amazon CloudWatch Agent.
- Registers `ai-agent-backend.service` with `systemd` for automatic startup on boot.

### 2. VPC & Multi-AZ Networking (`iaac.tf`)
- **VPC (`10.0.0.0/16`)** with DNS support and hostnames enabled across 3 Availability Zones.
- **Public Subnets**: Host the Application Load Balancer (`aws_lb.webapp_alb`) and NAT Gateway (`aws_nat_gateway.nat_gw`).
- **Private App Subnets (`webapp`)**: Host the **EC2 Auto Scaling Group** instances running the Python FastAPI Backend & TypeScript AI Agent.
- **Private Isolated DB Subnets (`db`)**: Host **AWS RDS PostgreSQL**, **AWS DocumentDB**, and **AWS Lambda** VPC ENIs.
- **Security Groups**:
  - `alb_sg`: Ingress `80`/`443` from `0.0.0.0/0`.
  - `webapp_sg`: Ingress `8000` strictly from `alb_sg`.
  - `rds_sg`: Ingress `5432` strictly from `webapp_sg` and `lambda_sg`.
  - `docdb_sg`: Ingress `27017` strictly from `webapp_sg` and `lambda_sg`.
  - `lambda_sg`: Outbound access to RDS, DocumentDB, and HTTPS.

### 3. Databases: AWS DocumentDB + AWS RDS PostgreSQL
- **AWS DocumentDB (`aws_docdb_cluster.conversation_history_cluster`)**:
  - MongoDB-compatible cluster with TLS enforced (`tls = enabled`) and KMS encryption at rest (`aws_kms_key.docdb_kms_key`).
  - Persists full multi-session AI Agent conversations and serves **sliding-window context history** to OpenAI `gpt-4o-mini`.
- **AWS RDS PostgreSQL (`aws_db_instance.webapp_rds_postgres`)**:
  - PostgreSQL 16 instance with enforced SSL (`rds.force_ssl = 1`), KMS storage encryption (`aws_kms_key.rds_kms_key`), and credentials stored in **AWS Secrets Manager**.
  - Stores user accounts, verification tokens, dynamically registered REST API tool definitions, and agent execution audit logs.

### 4. Compute, Auto Scaling & Load Balancing
- **EC2 Launch Template (`aws_launch_template.webapp_launch_template`)**:
  - Boots the custom Packer AMI with KMS-encrypted GP3 EBS volumes, IAM Instance Profile (`aws_iam_instance_profile.ec2_webapp_profile`), and `user_data` injecting runtime endpoints (`DOCDB_URI`, `DATABASE_HOST`, `SNS_TOPIC_ARN`, `SQS_QUEUE_URL`, `GITHUB_TOKEN`).
- **EC2 Auto Scaling Group (`aws_autoscaling_group.webapp_asg`)**:
  - Multi-AZ scaling with `ELB` health checks against `GET /healthz`, rolling instance refresh, and **CloudWatch Metric Alarms** (`CPUUtilization > 70%` scale-up, `< 30%` scale-down).
- **Application Load Balancer & Route 53**:
  - HTTPS listener (`443`) using **AWS Certificate Manager (ACM)** TLS 1.3 policy, HTTP->HTTPS redirect, and Route 53 Alias `A` record.

### 5. Event-Driven Serverless Pipeline (AWS SNS + SQS + Lambda)
- **AWS SNS Topic (`aws_sns_topic.verify_email_topic`)**: Receives verification & agent alert events published by the FastAPI backend.
- **AWS SQS Queue (`aws_sqs_queue.verify_email_queue`) + DLQ (`aws_sqs_queue.verify_email_dlq`)**: Subscribed to the SNS topic with KMS encryption and a 3-attempt redrive policy.
- **AWS Lambda (`aws_lambda_function.verify_email_lambda`)**: Node.js 20.x function deployed from KMS-encrypted S3 (`aws_s3_bucket.lambda_artifacts_bucket`), triggered via `aws_lambda_event_source_mapping` with partial batch failure reporting (`ReportBatchItemFailures`), updating RDS PostgreSQL and dispatching emails.

### 6. Customer-Managed KMS Keys (`aws_kms_key`)
- Dedicated KMS keys with automatic key rotation enabled for EBS volumes, RDS PostgreSQL, DocumentDB, S3 buckets, and SNS/SQS/Secrets Manager.

---

## Deployment Steps

### Step 1: Build Custom EC2 AMI with Packer
```bash
cd packer
packer init aws-ai-agent.pkr.hcl
packer build -var "aws_region=us-east-1" aws-ai-agent.pkr.hcl
```

### Step 2: Provision AWS Infrastructure with Terraform
```bash
cd ..
terraform init
terraform validate
terraform apply -var='ec2={ami_id="ami-xxxxxxxxxxxxxxxxx",instance_type="t3.medium",key_name="",application_port=8000,root_volume_size_gb=25,root_volume_type="gp3",ssh_allowed_cidrs=["0.0.0.0/0"]}'
```
