"""Lazily-constructed AWS clients shared across the FastAPI app.

boto3 clients are thread-safe and relatively expensive to build, so we create
them once and reuse. Region comes from settings; credentials come from the EC2
instance role (preferred) or the standard AWS credential chain.
"""
from functools import lru_cache

import boto3

from .config import get_settings


@lru_cache
def sns_client():
    return boto3.client("sns", region_name=get_settings().aws_region)


@lru_cache
def dynamodb_resource():
    return boto3.resource("dynamodb", region_name=get_settings().aws_region)


@lru_cache
def reviews_table():
    return dynamodb_resource().Table(get_settings().dynamodb_reviews_table)


@lru_cache
def repositories_table():
    return dynamodb_resource().Table(get_settings().dynamodb_repositories_table)
