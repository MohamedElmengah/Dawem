import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { StreaklyStack } from "../lib/streakly-stack";

describe("StreaklyStack", () => {
  let template: Template;

  beforeAll(() => {
    const app = new cdk.App({
      context: {
        alertEmail: "devops@streakly.app",
        aiServiceUrl: "https://ai.example.com",
        aiServiceApiKey: "test-key",
      },
    });
    const stack = new StreaklyStack(app, "StreaklyTestStack", {
      stage: "dev",
    });
    template = Template.fromStack(stack);
  });

  test("creates DynamoDB single-table with GSI1 and Streams", () => {
    template.hasResourceProperties("AWS::DynamoDB::Table", {
      BillingMode: "PAY_PER_REQUEST",
      StreamSpecification: {
        StreamViewType: "NEW_IMAGE",
      },
      GlobalSecondaryIndexes: Match.arrayWith([
        Match.objectLike({
          IndexName: "GSI1",
        }),
      ]),
    });
  });

  test("creates private, encrypted S3 bucket with SSL enforced", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      BucketEncryption: {
        ServerSideEncryptionConfiguration: Match.arrayWith([
          Match.objectLike({
            ServerSideEncryptionByDefault: {
              SSEAlgorithm: "AES256",
            },
          }),
        ]),
      },
    });
  });

  test("creates Cognito UserPool with password policy", () => {
    template.hasResourceProperties("AWS::Cognito::UserPool", {
      Policies: {
        PasswordPolicy: Match.objectLike({
          MinimumLength: 8,
          RequireNumbers: true,
          RequireLowercase: true,
        }),
      },
    });
  });

  test("routes the AI endpoints through the Cognito-protected HTTP API", () => {
    const routeKeys = Object.values(template.findResources("AWS::ApiGatewayV2::Route")).map(
      (r) => (r.Properties as { RouteKey: string }).RouteKey
    );
    for (const key of [
      "POST /files/index",
      "POST /files/index-s3",
      "POST /files/index-notes",
      "GET /files",
      "DELETE /files/{fileId}",
      "POST /plans/generate",
      "POST /quizzes/generate",
      "POST /quizzes/{quizId}/submit",
    ]) {
      expect(routeKeys).toContain(key);
    }
    template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
      RouteKey: "POST /plans/generate",
      AuthorizationType: "JWT",
    });
  });

  test("AI Lambdas read AI_SERVICE_URL / AI_SERVICE_API_KEY from the environment and fit the 30 s API limit", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Handler: "index.generatePlan",
      Timeout: 29,
      Environment: {
        Variables: Match.objectLike({
          AI_SERVICE_URL: "https://ai.example.com",
          AI_SERVICE_API_KEY: "test-key",
          AI_TIMEOUT_MS: "25000",
          TABLE_NAME: Match.anyValue(),
          FILES_BUCKET: Match.anyValue(),
        }),
      },
    });
  });

  test("AI Lambdas get table access only, no bucket or wildcard permissions", () => {
    const policies = JSON.stringify(template.findResources("AWS::IAM::Policy"));
    expect(policies).not.toContain('"s3:*"');
    expect(policies).not.toContain('"Action":"*"');
  });

  test("creates all 26 Node.js 24 Lambda functions with X-Ray Active tracing", () => {
    const appFunctions = template.findResources("AWS::Lambda::Function", {
      Properties: {
        Runtime: "nodejs24.x",
        TracingConfig: {
          Mode: "Active",
        },
      },
    });
    expect(Object.keys(appFunctions).length).toBeGreaterThanOrEqual(26);
  });

  test("creates HTTP API with Cognito JWT Authorizer", () => {
    template.hasResourceProperties("AWS::ApiGatewayV2::Api", {
      ProtocolType: "HTTP",
    });
    template.hasResourceProperties("AWS::ApiGatewayV2::Authorizer", {
      AuthorizerType: "JWT",
    });
  });

  // --- التعديل هنا: تحديث عدد الـ Queues لـ 5 ---
  test("creates SQS Queues (DLQs, AI, Retry) and 15-minute EventBridge schedule", () => {
    template.resourceCountIs("AWS::SQS::Queue", 5);
    template.hasResourceProperties("AWS::Events::Rule", {
      ScheduleExpression: "rate(15 minutes)",
    });
  });

  test("creates CloudWatch Dashboard, 4 Alarms, and SNS email alerts", () => {
    template.resourceCountIs("AWS::CloudWatch::Dashboard", 1);
    template.resourceCountIs("AWS::CloudWatch::Alarm", 4);
    template.resourceCountIs("AWS::SNS::Topic", 1);
    template.hasResourceProperties("AWS::SNS::Subscription", {
      Protocol: "email",
      Endpoint: "devops@streakly.app",
    });
  });
});