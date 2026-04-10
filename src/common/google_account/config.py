"""
Google Account Configuration Module

Configuration for Google API authentication scopes and settings.
Supports both login-only authentication and full Google services integration.
"""

import os
from dataclasses import dataclass, field
from typing import List


# ============================================================================
# SCOPE DEFINITIONS
# ============================================================================

# Basic scopes for user identification (login only)
OPENID_SCOPE = 'openid'
EMAIL_SCOPE = 'https://www.googleapis.com/auth/userinfo.email'
PROFILE_SCOPE = 'https://www.googleapis.com/auth/userinfo.profile'

# Google Services scopes
GMAIL_SCOPE = 'https://www.googleapis.com/auth/gmail.modify'
GMAIL_LABELS_SCOPE = 'https://www.googleapis.com/auth/gmail.labels'
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar'

# Scope sets for different use cases
LOGIN_SCOPES = [OPENID_SCOPE, EMAIL_SCOPE, PROFILE_SCOPE]
"""Minimal scopes for Google Sign-In (user identification only)"""

SERVICE_SCOPES = [GMAIL_SCOPE, GMAIL_LABELS_SCOPE, DRIVE_SCOPE, CALENDAR_SCOPE]
"""Scopes for Google services (Gmail, Drive, Calendar)"""

FULL_SCOPES = LOGIN_SCOPES + SERVICE_SCOPES
"""All scopes for users who login with Google (login + all services)"""


# ============================================================================
# OAUTH CONFIGURATION
# ============================================================================

# Google OAuth endpoints
GOOGLE_AUTH_URI = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URI = 'https://www.googleapis.com/oauth2/v3/userinfo'
GOOGLE_REVOKE_URI = 'https://oauth2.googleapis.com/revoke'


@dataclass
class GoogleAccountConfig:
    """
    Configuration for Google API authentication.
    
    Attributes:
        login_scopes: Scopes for basic login (user identification)
        service_scopes: Scopes for Google services (Gmail, Drive, Calendar)
        full_scopes: All scopes combined (used when logging in with Google)
    """
    login_scopes: List[str] = field(default_factory=lambda: LOGIN_SCOPES.copy())
    service_scopes: List[str] = field(default_factory=lambda: SERVICE_SCOPES.copy())
    full_scopes: List[str] = field(default_factory=lambda: FULL_SCOPES.copy())
    
    # OAuth endpoints
    auth_uri: str = GOOGLE_AUTH_URI
    token_uri: str = GOOGLE_TOKEN_URI
    userinfo_uri: str = GOOGLE_USERINFO_URI
    revoke_uri: str = GOOGLE_REVOKE_URI
    
    @classmethod
    def load_config(cls) -> 'GoogleAccountConfig':
        """
        Load configuration with default Google API scopes.
        
        Returns:
            GoogleAccountConfig: Configured instance
        """
        return cls()
    
    @property
    def scopes(self) -> List[str]:
        """
        Get all scopes (backward compatibility).
        
        Returns:
            List[str]: All configured scopes
        """
        return self.full_scopes


# Global config instance
config = GoogleAccountConfig.load_config()

