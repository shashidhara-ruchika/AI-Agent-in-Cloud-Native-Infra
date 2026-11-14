# AWS Lambda Serverless Event Processor (SNS / SQS + RDS PostgreSQL)

Event-driven **AWS Lambda** function subscribed to **AWS SNS** and **AWS SQS** for asynchronous email verification and AI Agent alert notifications, backed by **AWS RDS PostgreSQL**.

## Architecture Highlights
- **Trigger Pipeline**: FastAPI Backend / AI Agent publishes JSON events (`VERIFY_EMAIL` or `AGENT_TASK_ALERT`) to an encrypted **AWS SNS Topic**, which fans out to an **AWS SQS Queue** (with a Dead-Letter Queue) that triggers this **AWS Lambda** function via `aws_lambda_event_source_mapping`.
- **Database Integration**: Runs inside private VPC subnets with security group access to **AWS RDS PostgreSQL** (`Sequelize` + `pg`) to record verification token expiration timestamps and prevent duplicate dispatches.
- **Partial Batch Failure Reporting**: Returns `batchItemFailures` so failed SQS messages are retried independently before moving to the DLQ.

## Environment Variables
```env
AWS_REGION=us-east-1
MAILGUN_API_KEY=your-mailgun-key
MAILGUN_DOMAIN=yourdomain.com
MAILGUN_FROM="Cloud-Native AI Agent <noreply@yourdomain.com>"
VERIFY_EMAIL_LINK=https://yourdomain.com/v1/user/verify
VERIFY_EMAIL_EXPIRY_SECONDS=120
DATABASE_HOST=your-rds-instance.xxxx.us-east-1.rds.amazonaws.com
DATABASE_PORT=5432
DATABASE_NAME=webapp
DATABASE_USER=webapp
DATABASE_PASSWORD=your-rds-password
DATABASE_SSL=true
```

## Packaging for AWS Lambda & Terraform S3 Upload
```bash
npm install
npm run package
```
