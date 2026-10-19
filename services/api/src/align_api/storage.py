from __future__ import annotations

from typing import Any

import boto3
from botocore.config import Config as BotoConfig

from align_api.config import Settings


def storage_client(settings: Settings) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        region_name=settings.s3_region,
        config=BotoConfig(
            s3={"addressing_style": "path" if settings.s3_use_path_style else "virtual"},
            connect_timeout=2,
            read_timeout=10,
            retries={"max_attempts": 1},
        ),
    )
