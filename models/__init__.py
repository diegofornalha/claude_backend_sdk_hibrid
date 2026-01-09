"""
Models Package - Pydantic models para Structured Output (DT-SDK-003)

Define schemas que o Claude Agent SDK pode usar para garantir
respostas estruturadas e validadas.
"""

from .analysis_models import (
    DataAnalysisResult,
    SessionCostReport,
    UserActivityReport,
)

__all__ = [
    # Analysis
    "DataAnalysisResult",
    "SessionCostReport",
    "UserActivityReport",
]
