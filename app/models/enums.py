"""Enum definitions for VC-Manager"""
from enum import Enum

class PaymentStatus(Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"
