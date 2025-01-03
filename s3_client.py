# s3_client.py

import boto3
import config  # Your configuration file with AWS credentials

# Initialize S3 client
s3_client = boto3.client(
    "s3",
    aws_access_key_id=config.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
    region_name=config.AWS_REGION  # e.g., "us-east-1"
)