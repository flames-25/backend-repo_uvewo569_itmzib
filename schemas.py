"""
Database Schemas for the application

Each Pydantic model represents a MongoDB collection. Collection name is the
lowercased class name (e.g., AppUser -> "appuser").
"""
from typing import List, Optional
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime


class AppUser(BaseModel):
    email: EmailStr
    password_hash: str
    name: Optional[str] = None
    stripe_customer_id: Optional[str] = None
    subscription_status: Optional[str] = Field(default="inactive")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Plan(BaseModel):
    name: str
    description: Optional[str] = None
    price_cents: int = Field(ge=0)
    interval: str = Field(description="billing interval: month or year")
    stripe_price_id: str = Field(description="Stripe Price ID for this plan")
    features: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class Subscription(BaseModel):
    user_id: str
    stripe_subscription_id: str
    stripe_customer_id: str
    status: str
    plan_price_id: str
    current_period_end: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class GoogleConnection(BaseModel):
    user_id: str
    refresh_token: Optional[str] = None
    access_token: Optional[str] = None
    token_expiry: Optional[int] = None
    scopes: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
