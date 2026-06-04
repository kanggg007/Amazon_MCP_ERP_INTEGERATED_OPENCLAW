"""
Amazon Operations Intelligence Platform
========================================
Enterprise-grade multi-store Amazon FBA management system.

SECURITY NOTICE:
- No credentials are hardcoded in this codebase.
- All secrets loaded via environment variables or AWS Secrets Manager.
- The system NEVER exposes tokens to other modules.
- Only get_access_token(store_id) is the public API for the auth layer.
"""

__version__ = "1.0.0"
