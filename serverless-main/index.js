import mailgun from "mailgun-js";
import { DataTypes, Sequelize } from "sequelize";
import { v4 as uuidv4 } from "uuid";
import dotenv from "dotenv";

dotenv.config();

// Environment Configuration (Provisioned by AWS Terraform)
const mailgunApiKey = process.env.MAILGUN_API_KEY || "xxx";
const mailgunDomain = process.env.MAILGUN_DOMAIN || "firstnamelastname.me";
const mailgunFrom =
  process.env.MAILGUN_FROM || "Cloud-Native AI Agent <noreply@firstnamelastname.me>";
const verifyEmailLink =
  process.env.VERIFY_EMAIL_LINK || "https://example.com/v1/user/verify";
const postgresDBName = process.env.DATABASE_NAME || "webapp";
const postgresDBUser = process.env.DATABASE_USER || "webapp";
const postgresDBPassword = process.env.DATABASE_PASSWORD || "password";
const postgresDBHost = process.env.DATABASE_HOST || "localhost";
const postgresDBPort = parseInt(process.env.DATABASE_PORT || "5432", 10);
const postgresUseSsl = process.env.DATABASE_SSL === "true";
const verifyEmailExpiryInSeconds =
  parseInt(process.env.VERIFY_EMAIL_EXPIRY_SECONDS || "120", 10);

// Mailgun & AWS RDS PostgreSQL Clients
const mailgunClient = mailgun({ apiKey: mailgunApiKey, domain: mailgunDomain });

export const postgresDBConnection = new Sequelize(
  postgresDBName,
  postgresDBUser,
  postgresDBPassword,
  {
    host: postgresDBHost,
    port: postgresDBPort,
    dialect: "postgres",
    logging: false,
    dialectOptions: postgresUseSsl
      ? {
          ssl: {
            require: true,
            rejectUnauthorized: false,
          },
        }
      : {},
  }
);

// RDS PostgreSQL User Model
export const User = postgresDBConnection.define(
  "User",
  {
    id: {
      type: DataTypes.UUID,
      defaultValue: () => uuidv4(),
      primaryKey: true,
      allowNull: false,
    },
    first_name: {
      type: DataTypes.STRING,
      allowNull: false,
    },
    last_name: {
      type: DataTypes.STRING,
      allowNull: false,
    },
    password: {
      type: DataTypes.STRING,
      allowNull: false,
    },
    username: {
      type: DataTypes.STRING,
      allowNull: false,
      unique: true,
      validate: {
        isEmail: true,
      },
    },
    verified: {
      type: DataTypes.BOOLEAN,
      allowNull: false,
      defaultValue: false,
    },
    verification_token: {
      type: DataTypes.STRING,
      allowNull: true,
    },
    verification_expiry_timestamp: {
      type: DataTypes.DATE,
      allowNull: true,
    },
  },
  {
    tableName: "users",
    indexes: [
      {
        unique: true,
        fields: ["username"],
      },
    ],
    timestamps: true,
    createdAt: "account_created",
    updatedAt: "account_updated",
  }
);

/**
 * Extracts message payload from either an AWS SQS Record (subscribed to AWS SNS or direct)
 * or a direct AWS SNS Record.
 */
export const parseAwsEventRecord = (record) => {
  // Case 1: AWS SQS Record (aws:sqs)
  if (record.eventSource === "aws:sqs" || record.body) {
    const parsedBody =
      typeof record.body === "string" ? JSON.parse(record.body) : record.body;

    // If SQS queue is subscribed to an SNS topic, unwrap the SNS envelope
    if (parsedBody && parsedBody.Type === "Notification" && parsedBody.Message) {
      return typeof parsedBody.Message === "string"
        ? JSON.parse(parsedBody.Message)
        : parsedBody.Message;
    }
    return parsedBody;
  }

  // Case 2: Direct AWS SNS Record (aws:sns)
  if (record.EventSource === "aws:sns" || record.Sns) {
    const rawMsg = record.Sns.Message;
    return typeof rawMsg === "string" ? JSON.parse(rawMsg) : rawMsg;
  }

  return record;
};

/**
 * AWS Lambda Handler triggered by AWS SQS / AWS SNS.
 * Processes user email verification events and AI Agent task alerts alongside RDS PostgreSQL.
 */
export const handler = async (event, context) => {
  const records = event?.Records || [];
  const batchItemFailures = [];

  console.info(
    `[AWS Lambda: Event Processor] Invoked with ${records.length} SNS/SQS record(s), requestId=${context?.awsRequestId || "local"}`
  );

  for (const record of records) {
    try {
      const payload = parseAwsEventRecord(record);
      const email = payload.email || payload.username;
      const token = payload.token || payload.verification_token;
      const eventType = payload.eventType || "VERIFY_EMAIL";

      if (!email) {
        console.warn("[AWS Lambda] Skipping record with missing email:", payload);
        continue;
      }

      if (eventType === "AGENT_TASK_ALERT") {
        await sendAgentNotificationEmail(
          email,
          payload.message || "AI Agent completed your requested task."
        );
      } else {
        await sendVerificationEmail(email, token);
      }
    } catch (error) {
      console.error(
        `[AWS Lambda] Failed processing record ${record.messageId || "unknown"}:`,
        error
      );
      if (record.messageId) {
        batchItemFailures.push({ itemIdentifier: record.messageId });
      }
    }
  }

  return {
    statusCode: 200,
    batchItemFailures,
  };
};

export const sendVerificationEmail = async (email, token) => {
  const verificationUrl = `${verifyEmailLink}?token=${encodeURIComponent(token)}`;
  const emailData = {
    from: mailgunFrom,
    to: email,
    subject: "Cloud-Native AI Agent: Verify your email address",
    text: `Welcome to the Cloud-Native AI Agent platform!\n\nClick the link below to verify your email address (valid for ${verifyEmailExpiryInSeconds} seconds):\n${verificationUrl}\n`,
  };

  return new Promise((resolve, reject) => {
    mailgunClient.messages().send(emailData, async (error, body) => {
      if (error) {
        console.error(
          `[AWS Lambda: Send Verification Email] Error sending email to ${email}: ${error.message}`
        );
        reject(error);
      } else {
        console.info(
          `[AWS Lambda: Send Verification Email] Sent to ${email} with token ${token}, messageId=${body?.id}`
        );
        try {
          await updateVerificationExpiryTimestamp(email, token);
          resolve(body);
        } catch (dbErr) {
          reject(dbErr);
        }
      }
    });
  });
};

export const sendAgentNotificationEmail = async (email, messageText) => {
  const emailData = {
    from: mailgunFrom,
    to: email,
    subject: "Cloud-Native AI Agent Notification",
    text: messageText,
  };

  return new Promise((resolve, reject) => {
    mailgunClient.messages().send(emailData, (error, body) => {
      if (error) {
        console.error(
          `[AWS Lambda: Agent Alert] Error sending notification to ${email}: ${error.message}`
        );
        reject(error);
      } else {
        console.info(
          `[AWS Lambda: Agent Alert] Notification sent to ${email}, messageId=${body?.id}`
        );
        resolve(body);
      }
    });
  });
};

export const updateVerificationExpiryTimestamp = async (email, token) => {
  try {
    const user = await User.findOne({
      where: {
        username: email,
      },
    });

    if (!user) {
      console.warn(
        `[AWS Lambda] User ${email} not found in RDS PostgreSQL when updating expiry timestamp.`
      );
      return;
    }

    user.verification_token = token || user.verification_token;
    user.verification_expiry_timestamp = new Date(
      Date.now() + verifyEmailExpiryInSeconds * 1000
    );
    await user.save();

    console.info(
      `[AWS Lambda] Updated RDS PostgreSQL user ${user.id} verification_expiry_timestamp=${user.verification_expiry_timestamp.toISOString()}`
    );
  } catch (error) {
    console.error(
      `[AWS Lambda] Error updating RDS PostgreSQL verification expiry timestamp for token ${token}: ${error.message}`
    );
    throw error;
  }
};
