import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as cw from "aws-cdk-lib/aws-cloudwatch";
import * as cw_actions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as apigw from "aws-cdk-lib/aws-apigatewayv2";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as sqs from "aws-cdk-lib/aws-sqs";

export interface StreaklyObservabilityProps {
  stage: string;
  api: apigw.HttpApi;
  table: dynamodb.Table;
  streamDlq: sqs.Queue;
  functions: lambda.IFunction[];
  alarms: cw.Alarm[];
  alertEmail?: string;
}

export class StreaklyObservability extends Construct {
  public readonly alertsTopic: sns.Topic;
  public readonly dashboard: cw.Dashboard;

  constructor(scope: Construct, id: string, props: StreaklyObservabilityProps) {
    super(scope, id);

    // 1. SNS Topic for operational alerts
    this.alertsTopic = new sns.Topic(this, "AlertsTopic", {
      displayName: `Streakly ${props.stage} Alerts`,
      enforceSSL: true,
    });

    if (props.alertEmail) {
      this.alertsTopic.addSubscription(new subs.EmailSubscription(props.alertEmail));
    }

    const snsAction = new cw_actions.SnsAction(this.alertsTopic);

    // Wire all existing alarms to SNS
    props.alarms.forEach((alarm) => alarm.addAlarmAction(snsAction));

    // 2. API 5xx Alarm
    const api5xxAlarm = new cw.Alarm(this, "Api5xxErrors", {
      metric: props.api.metricServerError({ period: cdk.Duration.minutes(5) }),
      threshold: 5,
      evaluationPeriods: 1,
      alarmDescription: "HTTP API is returning 5xx server errors",
    });
    api5xxAlarm.addAlarmAction(snsAction);

    // 3. CloudWatch Dashboard
    this.dashboard = new cw.Dashboard(this, "Dashboard", {
      dashboardName: `Streakly-${props.stage}`,
    });

    this.dashboard.addWidgets(
      new cw.GraphWidget({
        title: "API Gateway Requests & Errors",
        width: 12,
        left: [props.api.metricCount({ period: cdk.Duration.minutes(5) })],
        right: [
          props.api.metricClientError({ period: cdk.Duration.minutes(5) }),
          props.api.metricServerError({ period: cdk.Duration.minutes(5) }),
        ],
      }),
      new cw.GraphWidget({
        title: "API Gateway Latency (ms)",
        width: 12,
        left: [
          props.api.metricLatency({ period: cdk.Duration.minutes(5), statistic: "Average" }),
          props.api.metricLatency({ period: cdk.Duration.minutes(5), statistic: "p99" }),
        ],
      })
    );

    this.dashboard.addWidgets(
      new cw.GraphWidget({
        title: "Lambda Errors",
        width: 12,
        left: props.functions.map((f) => f.metricErrors({ period: cdk.Duration.minutes(5) })),
      }),
      new cw.GraphWidget({
        title: "Lambda Invocations",
        width: 12,
        left: props.functions.map((f) => f.metricInvocations({ period: cdk.Duration.minutes(5) })),
      })
    );

    this.dashboard.addWidgets(
      new cw.GraphWidget({
        title: "DynamoDB Consumed Capacity",
        width: 12,
        left: [
          props.table.metricConsumedReadCapacityUnits({ period: cdk.Duration.minutes(5) }),
          props.table.metricConsumedWriteCapacityUnits({ period: cdk.Duration.minutes(5) }),
        ],
      }),
      new cw.GraphWidget({
        title: "Stream DLQ Messages",
        width: 12,
        left: [
          props.streamDlq.metricApproximateNumberOfMessagesVisible({
            period: cdk.Duration.minutes(5),
          }),
        ],
      })
    );

    new cdk.CfnOutput(this, "AlertsTopicArn", { value: this.alertsTopic.topicArn });
    new cdk.CfnOutput(this, "DashboardName", { value: this.dashboard.dashboardName });
  }
}