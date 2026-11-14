terraform {
  required_version = ">= 1.3.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" {
  state = "available"
}

# ============================================================================
# 1. AWS KMS CUSTOMER-MANAGED KEYS (EBS, RDS PostgreSQL, DocumentDB, S3, SNS/SQS)
# ============================================================================

resource "random_string" "kms_suffix" {
  length  = var.kms_config.suffix_length
  special = false
  upper   = false
}

resource "aws_kms_key" "ebs_kms_key" {
  description             = "KMS Key for EC2 Auto Scaling EBS volume encryption"
  deletion_window_in_days = var.kms_config.deletion_window_in_days
  enable_key_rotation     = true
  rotation_period_in_days = var.kms_config.rotation_period_in_days

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableIAMUserPermissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowAutoScalingServiceLinkedRole"
        Effect = "Allow"
        Principal = {
          AWS = [
            "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/aws-service-role/autoscaling.amazonaws.com/AWSServiceRoleForAutoScaling"
          ]
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
          "kms:CreateGrant"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "ebs_kms_alias" {
  name          = "alias/${var.project_name}-ebs-${random_string.kms_suffix.result}"
  target_key_id = aws_kms_key.ebs_kms_key.key_id
}

resource "aws_kms_key" "rds_kms_key" {
  description             = "KMS Key for AWS RDS PostgreSQL encryption at rest"
  deletion_window_in_days = var.kms_config.deletion_window_in_days
  enable_key_rotation     = true
  rotation_period_in_days = var.kms_config.rotation_period_in_days
}

resource "aws_kms_alias" "rds_kms_alias" {
  name          = "alias/${var.project_name}-rds-${random_string.kms_suffix.result}"
  target_key_id = aws_kms_key.rds_kms_key.key_id
}

resource "aws_kms_key" "docdb_kms_key" {
  description             = "KMS Key for AWS DocumentDB conversation history store encryption"
  deletion_window_in_days = var.kms_config.deletion_window_in_days
  enable_key_rotation     = true
  rotation_period_in_days = var.kms_config.rotation_period_in_days
}

resource "aws_kms_alias" "docdb_kms_alias" {
  name          = "alias/${var.project_name}-docdb-${random_string.kms_suffix.result}"
  target_key_id = aws_kms_key.docdb_kms_key.key_id
}

resource "aws_kms_key" "s3_kms_key" {
  description             = "KMS Key for S3 Lambda deployment artifacts bucket"
  deletion_window_in_days = var.kms_config.deletion_window_in_days
  enable_key_rotation     = true
  rotation_period_in_days = var.kms_config.rotation_period_in_days
}

resource "aws_kms_alias" "s3_kms_alias" {
  name          = "alias/${var.project_name}-s3-${random_string.kms_suffix.result}"
  target_key_id = aws_kms_key.s3_kms_key.key_id
}

resource "aws_kms_key" "messaging_kms_key" {
  description             = "KMS Key for SNS, SQS, and Secrets Manager encryption"
  deletion_window_in_days = var.kms_config.deletion_window_in_days
  enable_key_rotation     = true
  rotation_period_in_days = var.kms_config.rotation_period_in_days

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EnableRootPermissions"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      {
        Sid    = "AllowSNSToUseKMSForSQS"
        Effect = "Allow"
        Principal = {
          Service = ["sns.amazonaws.com", "sqs.amazonaws.com"]
        }
        Action = [
          "kms:GenerateDataKey*",
          "kms:Decrypt"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_kms_alias" "messaging_kms_alias" {
  name          = "alias/${var.project_name}-messaging-${random_string.kms_suffix.result}"
  target_key_id = aws_kms_key.messaging_kms_key.key_id
}

# ============================================================================
# 2. VPC, MULTI-AZ SUBNETS (PUBLIC, WEBAPP, DB), IGW, NAT GATEWAY & ROUTES
# ============================================================================

resource "aws_vpc" "main" {
  cidr_block           = var.vpc.cidr_block
  enable_dns_support   = var.vpc.enable_dns_support
  enable_dns_hostnames = var.vpc.enable_dns_hostnames

  tags = {
    Name = "${var.vpc.name}-vpc"
  }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.vpc.name}-igw"
  }
}

resource "aws_subnet" "public" {
  count                   = length(var.vpc.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.vpc.public_subnet_cidrs[count.index]
  availability_zone       = data.aws_availability_zones.available.names[count.index % length(data.aws_availability_zones.available.names)]
  map_public_ip_on_launch = true

  tags = {
    Name = "${var.vpc.name}-public-subnet-${count.index}"
    Tier = "Public"
  }
}

resource "aws_subnet" "webapp" {
  count             = length(var.vpc.webapp_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.vpc.webapp_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index % length(data.aws_availability_zones.available.names)]

  tags = {
    Name = "${var.vpc.name}-webapp-subnet-${count.index}"
    Tier = "Private-App"
  }
}

resource "aws_subnet" "db" {
  count             = length(var.vpc.db_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.vpc.db_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index % length(data.aws_availability_zones.available.names)]

  tags = {
    Name = "${var.vpc.name}-db-subnet-${count.index}"
    Tier = "Private-DB"
  }
}

resource "aws_eip" "nat_eip" {
  domain     = "vpc"
  depends_on = [aws_internet_gateway.igw]

  tags = {
    Name = "${var.vpc.name}-nat-eip"
  }
}

resource "aws_nat_gateway" "nat_gw" {
  allocation_id = aws_eip.nat_eip.id
  subnet_id     = aws_subnet.public[0].id
  depends_on    = [aws_internet_gateway.igw]

  tags = {
    Name = "${var.vpc.name}-nat-gw"
  }
}

resource "aws_route_table" "public_rt" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "${var.vpc.name}-public-rt"
  }
}

resource "aws_route_table_association" "public_assoc" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public_rt.id
}

resource "aws_route_table" "private_rt" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat_gw.id
  }

  tags = {
    Name = "${var.vpc.name}-private-rt"
  }
}

resource "aws_route_table_association" "webapp_assoc" {
  count          = length(aws_subnet.webapp)
  subnet_id      = aws_subnet.webapp[count.index].id
  route_table_id = aws_route_table.private_rt.id
}

resource "aws_route_table_association" "db_assoc" {
  count          = length(aws_subnet.db)
  subnet_id      = aws_subnet.db[count.index].id
  route_table_id = aws_route_table.private_rt.id
}

# ============================================================================
# 3. SECURITY GROUPS (ALB, EC2 AI AGENT / FASTAPI, RDS POSTGRES, DOCUMENTDB, LAMBDA)
# ============================================================================

resource "aws_security_group" "alb_sg" {
  name        = "${var.project_name}-alb-sg"
  description = "Security group for Application Load Balancer allowing HTTP/HTTPS"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Allow HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Allow HTTPS from anywhere"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-alb-sg"
  }
}

resource "aws_security_group" "webapp_sg" {
  name        = "${var.project_name}-webapp-sg"
  description = "Security group for EC2 Auto Scaling instances running Python FastAPI & TypeScript AI Agent"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Allow FastAPI / AI Agent HTTP traffic strictly from ALB"
    from_port       = var.ec2.application_port
    to_port         = var.ec2.application_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_sg.id]
  }

  ingress {
    description = "Allow SSH access from authorized CIDR"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.ec2.ssh_allowed_cidrs
  }

  egress {
    description = "Allow outbound HTTPS (GitHub Models GPT-4o-mini, AWS APIs) and DB traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-webapp-sg"
  }
}

resource "aws_security_group" "lambda_sg" {
  name        = "${var.project_name}-lambda-sg"
  description = "Security group for AWS Lambda event processor in VPC"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Allow outbound access to RDS PostgreSQL, DocumentDB, and Mailgun/SES HTTPS"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-lambda-sg"
  }
}

resource "aws_security_group" "rds_sg" {
  name        = "${var.project_name}-rds-postgres-sg"
  description = "Security group for AWS RDS PostgreSQL allowing traffic only from EC2 Webapp and Lambda"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL access from EC2 AI Agent & FastAPI"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.webapp_sg.id, aws_security_group.lambda_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-rds-postgres-sg"
  }
}

resource "aws_security_group" "docdb_sg" {
  name        = "${var.project_name}-documentdb-sg"
  description = "Security group for AWS DocumentDB conversation store allowing traffic from EC2 Webapp and Lambda"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "DocumentDB (27017) access from EC2 AI Agent & FastAPI"
    from_port       = 27017
    to_port         = 27017
    protocol        = "tcp"
    security_groups = [aws_security_group.webapp_sg.id, aws_security_group.lambda_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-documentdb-sg"
  }
}

# ============================================================================
# 4. AWS RDS POSTGRESQL (RELATIONAL STORE FOR USERS, TOKENS, AND AUDIT LOGS)
# ============================================================================

resource "random_password" "rds_password" {
  length           = var.database.password_length
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_secretsmanager_secret" "rds_credentials" {
  name                    = "${var.project_name}-rds-credentials-${random_string.kms_suffix.result}"
  kms_key_id              = aws_kms_key.messaging_kms_key.arn
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "rds_credentials_version" {
  secret_id = aws_secretsmanager_secret.rds_credentials.id
  secret_string = jsonencode({
    username = var.database.database_user
    password = random_password.rds_password.result
    dbname   = var.database.database_name
    host     = aws_db_instance.webapp_rds_postgres.address
    port     = aws_db_instance.webapp_rds_postgres.port
  })
}

resource "aws_db_subnet_group" "rds_subnet_group" {
  name       = "${var.project_name}-rds-subnet-group"
  subnet_ids = aws_subnet.db[*].id

  tags = {
    Name = "${var.project_name}-rds-subnet-group"
  }
}

resource "aws_db_parameter_group" "rds_pg_params" {
  name   = "${var.project_name}-pg16-params"
  family = var.database.parameter_group_family

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}

resource "aws_db_instance" "webapp_rds_postgres" {
  identifier             = var.database.identifier
  engine                 = "postgres"
  engine_version         = var.database.engine_version
  instance_class         = var.database.instance_class
  allocated_storage      = var.database.allocated_storage
  storage_type           = var.database.storage_type
  storage_encrypted      = true
  kms_key_id             = aws_kms_key.rds_kms_key.arn
  db_name                = var.database.database_name
  username               = var.database.database_user
  password               = random_password.rds_password.result
  db_subnet_group_name   = aws_db_subnet_group.rds_subnet_group.name
  vpc_security_group_ids = [aws_security_group.rds_sg.id]
  parameter_group_name   = aws_db_parameter_group.rds_pg_params.name
  multi_az               = var.database.multi_az
  publicly_accessible    = false
  skip_final_snapshot    = true
  deletion_protection    = var.database.deletion_protection
}

# ============================================================================
# 5. AWS DOCUMENTDB CLUSTER (WINDOWED PERSISTENT CONVERSATION HISTORY STORE)
# ============================================================================

resource "random_password" "docdb_password" {
  length           = 20
  special          = false
}

resource "aws_docdb_subnet_group" "docdb_subnet_group" {
  name       = "${var.project_name}-docdb-subnet-group"
  subnet_ids = aws_subnet.db[*].id

  tags = {
    Name = "${var.project_name}-docdb-subnet-group"
  }
}

resource "aws_docdb_cluster_parameter_group" "docdb_params" {
  family      = "docdb5.0"
  name        = "${var.project_name}-docdb-params"
  description = "DocumentDB cluster parameter group enforcing TLS"

  parameter {
    name  = "tls"
    value = "enabled"
  }
}

resource "aws_docdb_cluster" "conversation_history_cluster" {
  cluster_identifier              = "${var.project_name}-docdb-cluster"
  engine                          = "docdb"
  engine_version                  = var.docdb.engine_version
  master_username                 = var.docdb.master_username
  master_password                 = random_password.docdb_password.result
  db_subnet_group_name            = aws_docdb_subnet_group.docdb_subnet_group.name
  db_cluster_parameter_group_name = aws_docdb_cluster_parameter_group.docdb_params.name
  vpc_security_group_ids          = [aws_security_group.docdb_sg.id]
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.docdb_kms_key.arn
  skip_final_snapshot             = true
  deletion_protection             = false
}

resource "aws_docdb_cluster_instance" "conversation_history_instances" {
  count              = var.docdb.instance_count
  identifier         = "${var.project_name}-docdb-instance-${count.index}"
  cluster_identifier = aws_docdb_cluster.conversation_history_cluster.id
  instance_class     = var.docdb.instance_class
}

# ============================================================================
# 6. AWS SNS TOPIC & AWS SQS QUEUE + DLQ (EVENT-DRIVEN NOTIFICATION PIPELINE)
# ============================================================================

resource "aws_sns_topic" "verify_email_topic" {
  name              = "${var.project_name}-verify-email-topic"
  kms_master_key_id = aws_kms_key.messaging_kms_key.id
}

resource "aws_sqs_queue" "verify_email_dlq" {
  name                      = "${var.project_name}-verify-email-dlq"
  message_retention_seconds = 1209600 # 14 days
  kms_master_key_id         = aws_kms_key.messaging_kms_key.id
}

resource "aws_sqs_queue" "verify_email_queue" {
  name                       = "${var.project_name}-verify-email-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 86400
  kms_master_key_id          = aws_kms_key.messaging_kms_key.id

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.verify_email_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_policy" "allow_sns_to_sqs" {
  queue_url = aws_sqs_queue.verify_email_queue.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowSNSPublishToSQS"
        Effect    = "Allow"
        Principal = { Service = "sns.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.verify_email_queue.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.verify_email_topic.arn
          }
        }
      }
    ]
  })
}

resource "aws_sns_topic_subscription" "sns_to_sqs_subscription" {
  topic_arn = aws_sns_topic.verify_email_topic.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.verify_email_queue.arn
}

# ============================================================================
# 7. IAM ROLES & INSTANCE PROFILES (EC2 AI AGENT + AWS LAMBDA)
# ============================================================================

resource "aws_iam_role" "ec2_webapp_role" {
  name = "${var.project_name}-ec2-webapp-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_cloudwatch_agent" {
  role       = aws_iam_role.ec2_webapp_role.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_role_policy_attachment" "ec2_ssm_core" {
  role       = aws_iam_role.ec2_webapp_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "ec2_app_permissions" {
  name = "${var.project_name}-ec2-app-policy"
  role = aws_iam_role.ec2_webapp_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PublishVerifyAndAgentEventsToSNSAndSQS"
        Effect   = "Allow"
        Action   = ["sns:Publish", "sqs:SendMessage", "sqs:GetQueueUrl"]
        Resource = [aws_sns_topic.verify_email_topic.arn, aws_sqs_queue.verify_email_queue.arn]
      },
      {
        Sid    = "ReadSecretsAndDecryptKMS"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "kms:Decrypt",
          "kms:GenerateDataKey"
        ]
        Resource = [
          aws_secretsmanager_secret.rds_credentials.arn,
          aws_kms_key.messaging_kms_key.arn,
          aws_kms_key.ebs_kms_key.arn
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_webapp_profile" {
  name = "${var.project_name}-ec2-instance-profile"
  role = aws_iam_role.ec2_webapp_role.name
}

resource "aws_iam_role" "lambda_execution_role" {
  name = "${var.project_name}-lambda-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_vpc_access" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_sqs_kms_policy" {
  name = "${var.project_name}-lambda-sqs-kms-policy"
  role = aws_iam_role.lambda_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeSQSMessages"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility"
        ]
        Resource = [
          aws_sqs_queue.verify_email_queue.arn,
          aws_sqs_queue.verify_email_dlq.arn
        ]
      },
      {
        Sid    = "DecryptSecretsAndMessages"
        Effect = "Allow"
        Action = [
          "kms:Decrypt",
          "kms:GenerateDataKey",
          "secretsmanager:GetSecretValue",
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = "*"
      }
    ]
  })
}

# ============================================================================
# 8. S3 ARTIFACT BUCKET & AWS LAMBDA FUNCTION (TRIGGERED BY SQS / SNS)
# ============================================================================

resource "aws_s3_bucket" "lambda_artifacts_bucket" {
  bucket        = "${var.lambda_config.s3_bucket_prefix}-${random_string.kms_suffix.result}"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lambda_bucket_private" {
  bucket                  = aws_s3_bucket.lambda_artifacts_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lambda_bucket_encryption" {
  bucket = aws_s3_bucket.lambda_artifacts_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.s3_kms_key.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_object" "serverless_zip" {
  bucket = aws_s3_bucket.lambda_artifacts_bucket.id
  key    = var.lambda_config.s3_object_key
  source = var.lambda_config.local_zip_path
}

resource "aws_lambda_function" "verify_email_lambda" {
  function_name = "${var.project_name}-${var.lambda_config.function_name}"
  description   = "AWS Lambda function triggered by SNS/SQS to send verification & AI agent emails and update RDS PostgreSQL"
  role          = aws_iam_role.lambda_execution_role.arn
  handler       = var.lambda_config.handler
  runtime       = var.lambda_config.runtime
  timeout       = var.lambda_config.timeout_seconds
  memory_size   = var.lambda_config.memory_size

  s3_bucket = aws_s3_bucket.lambda_artifacts_bucket.id
  s3_key    = aws_s3_object.serverless_zip.key

  vpc_config {
    subnet_ids         = aws_subnet.db[*].id
    security_group_ids = [aws_security_group.lambda_sg.id]
  }

  environment {
    variables = {
      MAILGUN_API_KEY             = var.lambda_config.mailgun_api_key
      MAILGUN_DOMAIN              = var.lambda_config.mailgun_domain
      MAILGUN_FROM                = var.lambda_config.mailgun_from
      VERIFY_EMAIL_LINK           = var.lambda_config.verify_email_link
      VERIFY_EMAIL_EXPIRY_SECONDS = tostring(var.lambda_config.verify_email_expiry_seconds)
      DATABASE_HOST               = aws_db_instance.webapp_rds_postgres.address
      DATABASE_PORT               = tostring(aws_db_instance.webapp_rds_postgres.port)
      DATABASE_NAME               = var.database.database_name
      DATABASE_USER               = var.database.database_user
      DATABASE_PASSWORD           = random_password.rds_password.result
      DATABASE_SSL                = "true"
    }
  }

  depends_on = [
    aws_db_instance.webapp_rds_postgres,
    aws_s3_object.serverless_zip,
    aws_iam_role_policy_attachment.lambda_vpc_access
  ]
}

resource "aws_lambda_event_source_mapping" "sqs_lambda_trigger" {
  event_source_arn                   = aws_sqs_queue.verify_email_queue.arn
  function_name                      = aws_lambda_function.verify_email_lambda.arn
  batch_size                         = 5
  enabled                            = true
  function_response_types            = ["ReportBatchItemFailures"]
}

resource "aws_lambda_permission" "allow_sns_invoke" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.verify_email_lambda.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.verify_email_topic.arn
}

# ============================================================================
# 9. EC2 LAUNCH TEMPLATE (PACKER AMI), AUTO SCALING GROUP & CLOUDWATCH ALARMS
# ============================================================================

locals {
  docdb_connection_uri = "mongodb://${var.docdb.master_username}:${random_password.docdb_password.result}@${aws_docdb_cluster.conversation_history_cluster.endpoint}:${aws_docdb_cluster.conversation_history_cluster.port}/?tls=true&tlsCAFile=/opt/ai-agent/certs/global-bundle.pem&replicaSet=rs0&readPreference=secondaryPreferred&retryWrites=false"
}

resource "aws_launch_template" "webapp_launch_template" {
  name          = "${var.project_name}-launch-template"
  image_id      = var.ec2.ami_id
  instance_type = var.ec2.instance_type
  key_name      = var.ec2.key_name != "" ? var.ec2.key_name : null

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2_webapp_profile.name
  }

  network_interfaces {
    associate_public_ip_address = false
    security_groups             = [aws_security_group.webapp_sg.id]
  }

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = var.ec2.root_volume_size_gb
      volume_type           = var.ec2.root_volume_type
      delete_on_termination = true
      encrypted             = true
      kms_key_id            = aws_kms_key.ebs_kms_key.arn
    }
  }

  user_data = base64encode(<<-EOF
    #!/bin/bash
    set -e
    mkdir -p /opt/ai-agent
    cat <<EOT > /opt/ai-agent/.env
    GITHUB_TOKEN=${var.ai_agent.github_token}
    GITHUB_MODELS_ENDPOINT=https://models.github.ai/inference
    AI_MODEL=${var.ai_agent.model_name}
    MEMORY_WINDOW_SIZE=${var.ai_agent.memory_window_size}
    DOCDB_URI="${local.docdb_connection_uri}"
    DOCDB_DB_NAME=${var.docdb.database_name}
    DOCDB_TLS_CA_FILE=/opt/ai-agent/certs/global-bundle.pem
    DATABASE_HOST=${aws_db_instance.webapp_rds_postgres.address}
    DATABASE_PORT=${aws_db_instance.webapp_rds_postgres.port}
    DATABASE_NAME=${var.database.database_name}
    DATABASE_USER=${var.database.database_user}
    DATABASE_PASSWORD=${random_password.rds_password.result}
    AWS_REGION=${var.region}
    SNS_TOPIC_ARN=${aws_sns_topic.verify_email_topic.arn}
    SQS_QUEUE_URL=${aws_sqs_queue.verify_email_queue.id}
    EOT

    chown -R ubuntu:ubuntu /opt/ai-agent
    systemctl daemon-reload
    systemctl enable ai-agent-backend.service || true
    systemctl restart ai-agent-backend.service || true
  EOF
  )

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-ec2-instance"
    }
  }

  depends_on = [
    aws_db_instance.webapp_rds_postgres,
    aws_docdb_cluster.conversation_history_cluster,
    aws_docdb_cluster_instance.conversation_history_instances,
    aws_sns_topic.verify_email_topic
  ]
}

resource "aws_lb" "webapp_alb" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = aws_subnet.public[*].id

  tags = {
    Name = "${var.project_name}-alb"
  }
}

resource "aws_lb_target_group" "webapp_tg" {
  name        = "${var.project_name}-tg"
  port        = var.ec2.application_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "instance"

  health_check {
    enabled             = true
    path                = var.health_check.request_path
    port                = "traffic-port"
    protocol            = "HTTP"
    healthy_threshold   = var.health_check.healthy_threshold
    unhealthy_threshold = var.health_check.unhealthy_threshold
    timeout             = var.health_check.timeout_sec
    interval            = var.health_check.check_interval_sec
    matcher             = "200"
  }
}

resource "aws_lb_listener" "https_listener" {
  load_balancer_arn = aws_lb.webapp_alb.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.webapp_tg.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.webapp_alb.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_autoscaling_group" "webapp_asg" {
  name                      = "${var.project_name}-asg"
  min_size                  = var.auto_scaler.min_replicas
  max_size                  = var.auto_scaler.max_replicas
  desired_capacity          = var.auto_scaler.desired_replicas
  default_cooldown          = var.auto_scaler.cooldown_period
  vpc_zone_identifier       = aws_subnet.webapp[*].id
  target_group_arns         = [aws_lb_target_group.webapp_tg.arn]
  health_check_type         = "ELB"
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.webapp_launch_template.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.project_name}-asg-instance"
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_policy" "scale_up" {
  name                   = "${var.project_name}-asg-scale-up"
  scaling_adjustment     = 1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = var.auto_scaler.cooldown_period
  autoscaling_group_name = aws_autoscaling_group.webapp_asg.name
}

resource "aws_autoscaling_policy" "scale_down" {
  name                   = "${var.project_name}-asg-scale-down"
  scaling_adjustment     = -1
  adjustment_type        = "ChangeInCapacity"
  cooldown               = var.auto_scaler.cooldown_period
  autoscaling_group_name = aws_autoscaling_group.webapp_asg.name
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "${var.project_name}-cpu-utilization-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = var.auto_scaler.cpu_high_threshold_percent
  alarm_description   = "Scale up EC2 instances when average CPU exceeds threshold"
  alarm_actions       = [aws_autoscaling_policy.scale_up.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.webapp_asg.name
  }
}

resource "aws_cloudwatch_metric_alarm" "cpu_low" {
  alarm_name          = "${var.project_name}-cpu-utilization-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = var.auto_scaler.cpu_low_threshold_percent
  alarm_description   = "Scale down EC2 instances when average CPU drops below threshold"
  alarm_actions       = [aws_autoscaling_policy.scale_down.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.webapp_asg.name
  }
}

# ============================================================================
# 10. ROUTE 53 DNS RECORD (ALIAS TO APPLICATION LOAD BALANCER)
# ============================================================================

resource "aws_route53_record" "webapp_dns" {
  zone_id = var.dns_record.hosted_zone_id
  name    = var.dns_record.domain_name
  type    = "A"

  alias {
    name                   = aws_lb.webapp_alb.dns_name
    zone_id                = aws_lb.webapp_alb.zone_id
    evaluate_target_health = true
  }
}

# ============================================================================
# VARIABLES
# ============================================================================

variable "region" {
  description = "AWS region to provision cloud-native infrastructure"
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile"
  type        = string
  default     = "default"
}

variable "project_name" {
  description = "Project identifier prefix for all AWS resources"
  type        = string
  default     = "ai-agent-cloud-native"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

variable "vpc" {
  description = "VPC and Multi-AZ subnet CIDR configuration"
  type = object({
    name                 = string
    cidr_block           = string
    enable_dns_support   = bool
    enable_dns_hostnames = bool
    public_subnet_cidrs  = list(string)
    webapp_subnet_cidrs  = list(string)
    db_subnet_cidrs      = list(string)
  })
  default = {
    name                 = "ai-agent"
    cidr_block           = "10.0.0.0/16"
    enable_dns_support   = true
    enable_dns_hostnames = true
    public_subnet_cidrs  = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
    webapp_subnet_cidrs  = ["10.0.11.0/24", "10.0.12.0/24", "10.0.13.0/24"]
    db_subnet_cidrs      = ["10.0.21.0/24", "10.0.22.0/24", "10.0.23.0/24"]
  }
}

variable "ec2" {
  description = "EC2 Launch Template configuration using custom Packer AMI"
  type = object({
    ami_id              = string
    instance_type       = string
    key_name            = string
    application_port    = number
    root_volume_size_gb = number
    root_volume_type    = string
    ssh_allowed_cidrs   = list(string)
  })
  default = {
    ami_id              = "ami-0123456789abcdef0"
    instance_type       = "t3.medium"
    key_name            = ""
    application_port    = 8000
    root_volume_size_gb = 25
    root_volume_type    = "gp3"
    ssh_allowed_cidrs   = ["0.0.0.0/0"]
  }
}

variable "ai_agent" {
  description = "OpenAI GPT-4o mini via GitHub Models & DocumentDB window settings"
  type = object({
    github_token       = string
    model_name         = string
    memory_window_size = number
  })
  default = {
    github_token       = "ghp_replace_with_github_models_token"
    model_name         = "openai/gpt-4o-mini"
    memory_window_size = 10
  }
}

variable "database" {
  description = "AWS RDS PostgreSQL configuration"
  type = object({
    identifier             = string
    engine_version         = string
    parameter_group_family = string
    instance_class         = string
    allocated_storage      = number
    storage_type           = string
    database_name          = string
    database_user          = string
    password_length        = number
    multi_az               = bool
    deletion_protection    = bool
  })
  default = {
    identifier             = "ai-agent-rds-postgres"
    engine_version         = "16.3"
    parameter_group_family = "postgres16"
    instance_class         = "db.t3.micro"
    allocated_storage      = 20
    storage_type           = "gp3"
    database_name          = "webapp"
    database_user          = "webapp"
    password_length        = 20
    multi_az               = false
    deletion_protection    = false
  }
}

variable "docdb" {
  description = "AWS DocumentDB cluster configuration for windowed conversation history"
  type = object({
    engine_version  = string
    master_username = string
    database_name   = string
    instance_class  = string
    instance_count  = number
  })
  default = {
    engine_version  = "5.0.0"
    master_username = "docdbadmin"
    database_name   = "ai_agent_memory"
    instance_class  = "db.t3.medium"
    instance_count  = 1
  }
}

variable "lambda_config" {
  description = "AWS Lambda & S3 artifact configuration"
  type = object({
    function_name               = string
    handler                     = string
    runtime                     = string
    timeout_seconds             = number
    memory_size                 = number
    s3_bucket_prefix            = string
    s3_object_key               = string
    local_zip_path              = string
    mailgun_api_key             = string
    mailgun_domain              = string
    mailgun_from                = string
    verify_email_link           = string
    verify_email_expiry_seconds = number
  })
  default = {
    function_name               = "verify-email-processor"
    handler                     = "index.handler"
    runtime                     = "nodejs20.x"
    timeout_seconds             = 30
    memory_size                 = 256
    s3_bucket_prefix            = "ai-agent-lambda-artifacts"
    s3_object_key               = "serverless.zip"
    local_zip_path              = "../serverless-main/serverless.zip"
    mailgun_api_key             = "replace_me"
    mailgun_domain              = "example.com"
    mailgun_from                = "AI Agent <noreply@example.com>"
    verify_email_link           = "https://example.com/v1/user/verify"
    verify_email_expiry_seconds = 120
  }
}

variable "health_check" {
  description = "ALB Target Group health check parameters (/healthz)"
  type = object({
    request_path        = string
    check_interval_sec  = number
    timeout_sec         = number
    healthy_threshold   = number
    unhealthy_threshold = number
  })
  default = {
    request_path        = "/healthz"
    check_interval_sec  = 30
    timeout_sec         = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

variable "auto_scaler" {
  description = "EC2 Auto Scaling Group & CloudWatch CPU alarm policies"
  type = object({
    min_replicas               = number
    max_replicas               = number
    desired_replicas           = number
    cooldown_period            = number
    cpu_high_threshold_percent = number
    cpu_low_threshold_percent  = number
  })
  default = {
    min_replicas               = 2
    max_replicas               = 5
    desired_replicas           = 2
    cooldown_period            = 60
    cpu_high_threshold_percent = 70
    cpu_low_threshold_percent  = 30
  }
}

variable "dns_record" {
  description = "Route53 Hosted Zone and Domain configuration"
  type = object({
    hosted_zone_id = string
    domain_name    = string
  })
  default = {
    hosted_zone_id = "Z0123456789ABCDEF"
    domain_name    = "agent.example.com"
  }
}

variable "acm_certificate_arn" {
  description = "AWS Certificate Manager (ACM) SSL certificate ARN for HTTPS ALB listener"
  type        = string
  default     = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
}

variable "kms_config" {
  description = "AWS KMS key rotation and deletion window parameters"
  type = object({
    suffix_length           = number
    deletion_window_in_days = number
    rotation_period_in_days = number
  })
  default = {
    suffix_length           = 6
    deletion_window_in_days = 7
    rotation_period_in_days = 90
  }
}

# ============================================================================
# OUTPUTS
# ============================================================================

output "alb_dns_name" {
  description = "Public DNS name of the AWS Application Load Balancer"
  value       = aws_lb.webapp_alb.dns_name
}

output "rds_postgres_endpoint" {
  description = "Private endpoint of the AWS RDS PostgreSQL instance"
  value       = aws_db_instance.webapp_rds_postgres.endpoint
}

output "documentdb_cluster_endpoint" {
  description = "Private endpoint of the AWS DocumentDB conversation store cluster"
  value       = aws_docdb_cluster.conversation_history_cluster.endpoint
}

output "sns_topic_arn" {
  description = "ARN of the AWS SNS Topic for email verification & agent notifications"
  value       = aws_sns_topic.verify_email_topic.arn
}

output "sqs_queue_url" {
  description = "URL of the AWS SQS Queue subscribed to SNS and triggering AWS Lambda"
  value       = aws_sqs_queue.verify_email_queue.id
}

output "lambda_function_name" {
  description = "Name of the deployed AWS Lambda event processor"
  value       = aws_lambda_function.verify_email_lambda.function_name
}
