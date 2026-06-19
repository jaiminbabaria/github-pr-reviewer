"""Pydantic models used for API responses.

These describe the shape of data returned to clients. DynamoDB stores numbers
as Decimal, so the read routes coerce them to int/float before returning.
"""
from typing import Optional

from pydantic import BaseModel


class Review(BaseModel):
    reviewId: str
    repoName: str
    prNumber: int
    prTitle: Optional[str] = None
    author: Optional[str] = None
    status: str
    diffSize: Optional[int] = 0
    commentsPosted: Optional[int] = 0
    reviewSummary: Optional[str] = None
    processingTimeMs: Optional[int] = None
    createdAt: Optional[str] = None
    completedAt: Optional[str] = None


class ReviewList(BaseModel):
    count: int
    reviews: list[Review]


class Repository(BaseModel):
    repoId: str
    repoName: str
    installationId: Optional[str] = None
    totalReviews: int = 0
    registeredAt: Optional[str] = None


class Stats(BaseModel):
    totalReviews: int
    completed: int
    failed: int
    pending: int
    processing: int
    averageProcessingTimeMs: Optional[float] = None
    totalCommentsPosted: int


class WebhookAccepted(BaseModel):
    status: str
    detail: str
    delivery_id: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    service: str
