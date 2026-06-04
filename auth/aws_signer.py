"""
AWS IAM Signer
===============
Signs SP-API requests using AWS Signature V4 with IAM Role credentials.
Required for Amazon SP-API authentication alongside LWA tokens.
"""

import hashlib
import hmac
import logging
import time
from typing import Dict, Optional
from urllib.parse import urlparse, quote

from config.secrets import SecretsLoader

logger = logging.getLogger("amazon.auth.aws_signer")


class AWSSigner:
    """
    AWS Signature V4 signing for SP-API requests.

    Signs requests using the store's IAM role credentials.
    Required for SP-API operations that need IAM auth.
    """

    def __init__(self, secrets_loader: SecretsLoader):
        self._secrets = secrets_loader

    def sign_request(
        self,
        store_id: str,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: str = "",
        service: str = "execute-api",
        region: str = "us-east-1",
    ) -> Dict[str, str]:
        """
        Sign an SP-API request with AWS Signature V4.

        Args:
            store_id: Store to sign for
            method: HTTP method (GET, POST, PUT)
            url: Full request URL
            headers: Existing request headers
            body: Request body string
            service: AWS service name (default: execute-api)
            region: AWS region

        Returns:
            Headers dict with Authorization added
        """
        secrets = self._secrets.get_all(store_id)
        access_key = secrets.get("aws_access_key_id")
        secret_key = secrets.get("aws_secret_access_key")

        if not access_key or not secret_key:
            logger.warning(
                "AWS credentials not found for store '%s'. Skipping signing.",
                store_id,
            )
            return headers

        signed_headers = self._sign_v4(
            method=method,
            url=url,
            headers=headers,
            body=body,
            service=service,
            region=region,
            access_key=access_key,
            secret_key=secret_key,
        )

        headers.update(signed_headers)
        return headers

    def _sign_v4(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        body: str,
        service: str,
        region: str,
        access_key: str,
        secret_key: str,
    ) -> Dict[str, str]:
        """AWS Signature V4 implementation."""
        parsed = urlparse(url)
        amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        date_stamp = time.strftime("%Y%m%d", time.gmtime())

        headers["x-amz-date"] = amz_date
        headers["host"] = parsed.hostname

        # Canonical request
        canonical_uri = quote(parsed.path, safe="/") or "/"
        canonical_querystring = parsed.query
        canonical_headers = "".join(
            f"{k.lower()}:{v.strip()}\n" for k, v in sorted(headers.items())
        )
        signed_headers_list = ";".join(sorted(k.lower() for k in headers))
        payload_hash = hashlib.sha256(body.encode()).hexdigest()

        canonical_request = "\n".join([
            method.upper(),
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            signed_headers_list,
            payload_hash,
        ])

        # String to sign
        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
        string_to_sign = "\n".join([
            algorithm,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ])

        # Signing key
        k_date = self._hmac_sha256(f"AWS4{secret_key}", date_stamp)
        k_region = self._hmac_sha256(k_date, region)
        k_service = self._hmac_sha256(k_region, service)
        k_signing = self._hmac_sha256(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        authorization = (
            f"{algorithm} Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers_list}, Signature={signature}"
        )
        headers["Authorization"] = authorization

        return headers

    @staticmethod
    def _hmac_sha256(key: bytes, msg: str) -> bytes:
        if isinstance(key, str):
            key = key.encode()
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
