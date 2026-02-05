"""
Matching Engine Service Package

Provides signal scorers for matching creditor answers to inquiries and
explainability builders for debugging match results.
"""

from app.services.matching.signals import score_client_name, score_reference_numbers

__all__ = ["score_client_name", "score_reference_numbers"]
