# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import os
import json
import logging
import uuid
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS services for X-Ray tracing
patch_all()

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb_client = boto3.client("dynamodb")


@xray_recorder.capture('lambda_handler')
def handler(event, context):
    table = os.environ.get("TABLE_NAME")
    logging.info(f"## Loaded table name from environemt variable DDB_TABLE: {table}")
    
    # Add custom annotation for X-Ray filtering
    xray_recorder.put_annotation("table_name", table)
    xray_recorder.put_metadata("request_context", {
        "function_name": context.function_name,
        "request_id": context.aws_request_id
    })
    
    if event["body"]:
        with xray_recorder.in_subsegment('process_request_with_payload'):
            item = json.loads(event["body"])
            logging.info(f"## Received payload: {item}")
            year = str(item["year"])
            title = str(item["title"])
            id = str(item["id"])
            
            # Add annotation for payload processing
            xray_recorder.put_annotation("has_payload", True)
            xray_recorder.put_annotation("item_id", id)
            
            dynamodb_client.put_item(
                TableName=table,
                Item={"year": {"N": year}, "title": {"S": title}, "id": {"S": id}},
            )
            
        message = "Successfully inserted data!"
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": message}),
        }
    else:
        with xray_recorder.in_subsegment('process_request_without_payload'):
            logging.info("## Received request without a payload")
            default_id = str(uuid.uuid4())
            
            # Add annotation for default processing
            xray_recorder.put_annotation("has_payload", False)
            xray_recorder.put_annotation("item_id", default_id)
            
            dynamodb_client.put_item(
                TableName=table,
                Item={
                    "year": {"N": "2012"},
                    "title": {"S": "The Amazing Spider-Man 2"},
                    "id": {"S": default_id},
                },
            )
            
        message = "Successfully inserted data!"
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": message}),
        }