"""
Google Account Authentication Package

This package provides user-aware Google API authentication for Django applications.
"""

from .auth import get_authenticated_service

__all__ = ['get_authenticated_service']

