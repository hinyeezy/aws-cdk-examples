# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import os
from aws_cdk import (
    Stack,
    aws_dynamodb as dynamodb_,
    aws_lambda as lambda_,
    aws_apigateway as apigw_,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_cloudwatch as cloudwatch,
    aws_cloudtrail as cloudtrail,
    aws_s3 as s3,
    aws_logs as logs,
    Duration,
)
from constructs import Construct

TABLE_NAME = "demo_table"


class ApigwHttpApiLambdaDynamodbPythonCdkStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # CloudTrail S3 bucket for security audit logs
        cloudtrail_bucket = s3.Bucket(
            self,
            "CloudTrailBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioning=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="CloudTrailLogRetention",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30)
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90)
                        )
                    ]
                )
            ]
        )

        # CloudTrail for API call logging
        trail = cloudtrail.Trail(
            self,
            "SecurityAuditTrail",
            bucket=cloudtrail_bucket,
            include_global_service_events=True,
            is_multi_region_trail=True,
            enable_file_validation=True,
            trail_name="security-audit-trail"
        )

        # VPC
        vpc = ec2.Vpc(
            self,
            "Ingress",
            cidr="10.1.0.0/16",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Private-Subnet", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24
                )
            ],
        )

        # CloudWatch Log Group for VPC Flow Logs
        vpc_flow_log_group = logs.LogGroup(
            self,
            "VpcFlowLogGroup",
            retention=logs.RetentionDays.ONE_MONTH,
            log_group_name="/aws/vpc/flowlogs"
        )

        # Enable VPC Flow Logs
        vpc_flow_log = ec2.FlowLog(
            self,
            "VpcFlowLog",
            resource_type=ec2.FlowLogResourceType.from_vpc(vpc),
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(vpc_flow_log_group)
        )
        
        # Create VPC endpoint
        dynamo_db_endpoint = ec2.GatewayVpcEndpoint(
            self,
            "DynamoDBVpce",
            service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
            vpc=vpc,
        )

        # This allows to customize the endpoint policy
        dynamo_db_endpoint.add_to_policy(
            iam.PolicyStatement(  # Restrict to listing and describing tables
                principals=[iam.AnyPrincipal()],
                actions=[                "dynamodb:DescribeStream",
                "dynamodb:DescribeTable",
                "dynamodb:Get*",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:CreateTable",
                "dynamodb:Delete*",
                "dynamodb:Update*",
                "dynamodb:PutItem"],
                resources=["*"],
            )
        )

        # Create DynamoDb Table with audit logging
        demo_table = dynamodb_.Table(
            self,
            TABLE_NAME,
            partition_key=dynamodb_.Attribute(
                name="id", type=dynamodb_.AttributeType.STRING
            ),
            point_in_time_recovery=True,  # Enable point-in-time recovery
            stream=dynamodb_.StreamViewType.NEW_AND_OLD_IMAGES  # Enable DynamoDB Streams for audit
        )

        # Add DynamoDB data events to CloudTrail
        trail.add_event_selector(
            read_write_type=cloudtrail.ReadWriteType.ALL,
            include_management_events=False,
            data_resource_type=cloudtrail.DataResourceType.DYNAMO_DB_TABLE,
            data_resource_values=[demo_table.table_arn]
        )

        # Create the Lambda function to receive the request
        api_hanlder = lambda_.Function(
            self,
            "ApiHandler",
            function_name="apigw_handler",
            runtime=lambda_.Runtime.PYTHON_3_9,
            code=lambda_.Code.from_asset("lambda/apigw-handler"),
            handler="index.handler",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            memory_size=1024,
            timeout=Duration.minutes(5),
            tracing=lambda_.Tracing.ACTIVE,  # Enable X-Ray tracing
        )

        # grant permission to lambda to write to demo table
        demo_table.grant_write_data(api_hanlder)
        api_hanlder.add_environment("TABLE_NAME", demo_table.table_name)

        # Create API Gateway with X-Ray tracing enabled
        api = apigw_.LambdaRestApi(
            self,
            "Endpoint",
            handler=api_hanlder,
            deploy_options=apigw_.StageOptions(
                tracing_enabled=True  # Enable X-Ray tracing
            )
        )

        # Create usage plan with per-client throttling (REL05-BP02)
        usage_plan = api.add_usage_plan(
            "DefaultUsagePlan",
            name="default-usage-plan",
            description="Default usage plan with throttling limits",
            throttle=apigw_.ThrottleSettings(
                rate_limit=50,    # 50 requests per second per API key
                burst_limit=100   # 100 burst capacity per API key
            ),
            quota=apigw_.QuotaSettings(
                limit=10000,      # 10,000 requests per day
                period=apigw_.Period.DAY
            )
        )

        # Create API key for throttling control
        api_key = api.add_api_key(
            "DefaultApiKey",
            api_key_name="default-api-key",
            description="Default API key for throttling"
        )

        # Associate API key with usage plan
        usage_plan.add_api_key(api_key)

        # CloudWatch Alarms for monitoring
        lambda_error_alarm = cloudwatch.Alarm(
            self,
            "LambdaErrorAlarm",
            metric=api_hanlder.metric_errors(),
            threshold=1,
            evaluation_periods=2,
            alarm_description="Lambda function errors - indicates issues with request processing"
        )

        lambda_duration_alarm = cloudwatch.Alarm(
            self,
            "LambdaDurationAlarm",
            metric=api_hanlder.metric_duration(),
            threshold=Duration.seconds(30).to_seconds(),
            evaluation_periods=2,
            alarm_description="Lambda function duration - indicates performance issues"
        )

        lambda_throttle_alarm = cloudwatch.Alarm(
            self,
            "LambdaThrottleAlarm",
            metric=api_hanlder.metric_throttles(),
            threshold=1,
            evaluation_periods=1,
            alarm_description="Lambda function throttles - indicates capacity issues"
        )