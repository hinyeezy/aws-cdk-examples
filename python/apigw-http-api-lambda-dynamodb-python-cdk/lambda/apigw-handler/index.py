# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import boto3
import os
import json
import logging
import uuid
import sys
from datetime import datetime
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS services for X-Ray tracing
patch_all()

# Configure structured logging for security events
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Create formatter for structured JSON logs
formatter = logging.Formatter(
    '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s", "function": "%(funcName)s"}'
)

# Configure handler
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
logger.handlers = [handler]

dynamodb_client = boto3.client("dynamodb")


@xray_recorder.capture('lambda_handler')
def handler(event, context):
    table = os.environ.get("TABLE_NAME")
    
    # Extract security-relevant information from event
    request_context = event.get("requestContext", {})
    identity = request_context.get("identity", {})
    source_ip = identity.get("sourceIp", "unknown")
    user_agent = identity.get("userAgent", "unknown")
    
    # Log security event - API request received
    logger.info(json.dumps({
        "event_type": "api_request_received",
        "source_ip": source_ip,
        "user_agent": user_agent,
        "request_id": context.aws_request_id,
        "function_name": context.function_name,
        "table_name": table,
        "timestamp": datetime.utcnow().isoformat()
    }))
    
    # Add custom annotation for X-Ray filtering
    xray_recorder.put_annotation("table_name", table)
    xray_recorder.put_annotation("source_ip", source_ip)
    xray_recorder.put_metadata("security_context", {
        "function_name": context.function_name,
        "request_id": context.aws_request_id,
        "source_ip": source_ip,
        "user_agent": user_agent
    })
    
    try:
        if event["body"]:
            with xray_recorder.in_subsegment('process_request_with_payload'):
                item = json.loads(event["body"])
                
                # Log data access event
                logger.info(json.dumps({
                    "event_type": "data_processing_start",
                    "has_payload": True,
                    "request_id": context.aws_request_id,
                    "source_ip": source_ip,
                    "timestamp": datetime.utcnow().isoformat()
                }))
                
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
                
                # Log successful data write
                logger.info(json.dumps({
                    "event_type": "dynamodb_write_success",
                    "table_name": table,
                    "item_id": id,
                    "request_id": context.aws_request_id,
                    "source_ip": source_ip,
                    "timestamp": datetime.utcnow().isoformat()
                }))
                
        else:
            with xray_recorder.in_subsegment('process_request_without_payload'):
                default_id = str(uuid.uuid4())
                
                # Log default processing event
                logger.info(json.dumps({
                    "event_type": "data_processing_start",
                    "has_payload": False,
                    "default_processing": True,
                    "request_id": context.aws_request_id,
                    "source_ip": source_ip,
                    "timestamp": datetime.utcnow().isoformat()
                }))
                
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
                
                # Log successful default data write
                logger.info(json.dumps({
                    "event_type": "dynamodb_write_success",
                    "table_name": table,
                    "item_id": default_id,
                    "default_processing": True,
                    "request_id": context.aws_request_id,
                    "source_ip": source_ip,
                    "timestamp": datetime.utcnow().isoformat()
                }))
        
        # Log successful request completion
        logger.info(json.dumps({
            "event_type": "api_request_completed",
            "status": "success",
            "request_id": context.aws_request_id,
            "source_ip": source_ip,
            "timestamp": datetime.utcnow().isoformat()
        }))
        
        message = "Successfully inserted data!"
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"message": message}),
        }
        
    except Exception as e:
        # Log security-relevant error information
        logger.error(json.dumps({
            "event_type": "api_request_error",
            "error_type": type(e).__name__,
            "error_message": str(e),
            "request_id": context.aws_request_id,
            "source_ip": source_ip,
            "table_name": table,
            "timestamp": datetime.utcnow().isoformat()
        }))
        
        # Re-raise the exception for proper error handling
        raise