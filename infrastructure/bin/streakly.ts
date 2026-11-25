#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { AwsSolutionsChecks } from "cdk-nag";
import { StreaklyStack } from "../lib/streakly-stack";

const app = new cdk.App();
const stage = app.node.tryGetContext("stage") ?? "dev";

const stack = new StreaklyStack(app, `Streakly-${stage}`, {
  stage,
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
});

const nag = new AwsSolutionsChecks(app, { verbose: true });
const report = nag.validateScope(stack);
if (!report.success) {
  console.warn(`\n[cdk-nag] Found ${report.violations.length} rule violation(s):`);
  for (const v of report.violations) {
    console.warn(`- ${v.ruleName} (${v.violatingResources.length} resource(s)): ${v.description}`);
  }
}