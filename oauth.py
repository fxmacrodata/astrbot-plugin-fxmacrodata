"""User-scoped FXMacroData OAuth support for the AstrBot integration.

This module implements a device authorization flow because a chat command has
no private browser callback.  Users enter credentials only on the first-party
FXMacroData verification page.  AstrBot receives revocable OAuth tokens and
keeps them encrypted in its private plugin KV store.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken

# The dedicated API host publishes OAuth at its origin.  The website's
# same-origin proxy exposes a separate `/api/...` alias, which this plugin does
# not use so browser verification and token polling stay on the canonical API
# host.
OAUTH_BASE_URL = "https://api.fxmacrodata.com/oauth"
ASTRBOT_DEVICE_CLIENT_ID = "astrbot-plugin-fxmacrodata"
TOKEN_ENCRYPTION_ENV = "FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY"
CREDENTIAL_STORAGE_KEY = "fxmacrodata_oauth_credentials_v1"
PENDING_DEVICE_STORAGE_KEY = "fxmacrodata_oauth_pending_device_v1"
EXPIRY_SKEW_SECONDS = 60


class OAuthError(RuntimeError):
    """A safe, user-facing OAuth transport or credential-store failure."""


class OAuthPendingError(OAuthError):
    """The user has not yet approved a device authorization."""


class OAuthConfigurationError(OAuthError):
    """The AstrBot operator has not supplied safe token encryption."""


class PluginKV(Protocol):
    async def get_kv_data(self, key: str, default: Any) -> Any: ...

    async def put_kv_data(self, key: str, value: Any) -> None: ...


@dataclass(frozen=True)
class DeviceAuthorization:
    """Non-secret details that AstrBot may show to the person signing in."""

    verification_uri_complete: str
    user_code: str
    expires_in: int
    interval: int


class FXMacroDataOAuthClient:
    """Minimal OAuth client which never logs request bodies or token values."""

    def __init__(self, *, timeout_seconds: int = 45, base_url: str = OAUTH_BASE_URL):
        self._timeout_seconds = timeout_seconds
        self._base_url = base_url.rstrip("/")

    @staticmethod
    def _error_from_response(response: httpx.Response) -> OAuthError:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail", payload) if isinstance(payload, dict) else {}
        error = detail.get("error") if isinstance(detail, dict) else None
        description = (
            detail.get("error_description") if isinstance(detail, dict) else None
        )
        if error == "authorization_pending":
            return OAuthPendingError(
                "Finish approval in your FXMacroData browser page."
            )
        if error == "slow_down":
            return OAuthPendingError("Please wait a few seconds, then check again.")
        if error in {"expired_token", "invalid_user_code"}:
            return OAuthError("This sign-in request expired. Start a new connection.")
        if error == "access_denied":
            return OAuthError("FXMacroData did not approve this connection.")
        if response.status_code == 401:
            return OAuthError(
                "FXMacroData authorization was rejected. Reconnect to continue."
            )
        if response.status_code >= 500:
            return OAuthError("FXMacroData authorization is temporarily unavailable.")
        return OAuthError(
            description or "FXMacroData authorization could not be completed."
        )

    async def _post(self, path: str, data: dict[str, str]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds, follow_redirects=False
            ) as client:
                response = await client.post(f"{self._base_url}{path}", data=data)
        except httpx.HTTPError as exc:
            raise OAuthError(
                "FXMacroData authorization is unavailable. Try again."
            ) from exc
        if response.status_code >= 400:
            raise self._error_from_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthError(
                "FXMacroData returned an invalid authorization response."
            ) from exc
        if not isinstance(payload, dict):
            raise OAuthError("FXMacroData returned an invalid authorization response.")
        return payload

    async def start_device_authorization(self) -> tuple[DeviceAuthorization, str]:
        payload = await self._post(
            "/device/authorize",
            {
                "client_id": ASTRBOT_DEVICE_CLIENT_ID,
                "scope": "fxmacrodata.read",
            },
        )
        device_code = payload.get("device_code")
        verification_uri_complete = payload.get("verification_uri_complete")
        user_code = payload.get("user_code")
        if not all(
            isinstance(value, str) and value
            for value in (device_code, verification_uri_complete, user_code)
        ):
            raise OAuthError("FXMacroData returned an incomplete sign-in request.")
        try:
            expires_in = max(int(payload.get("expires_in") or 0), 1)
            interval = max(int(payload.get("interval") or 5), 1)
        except (TypeError, ValueError) as exc:
            raise OAuthError(
                "FXMacroData returned an invalid sign-in request."
            ) from exc
        return (
            DeviceAuthorization(
                verification_uri_complete=verification_uri_complete,
                user_code=user_code,
                expires_in=expires_in,
                interval=interval,
            ),
            device_code,
        )

    async def exchange_device_code(self, device_code: str) -> dict[str, Any]:
        return await self._post(
            "/token",
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": ASTRBOT_DEVICE_CLIENT_ID,
            },
        )

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        return await self._post(
            "/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": ASTRBOT_DEVICE_CLIENT_ID,
            },
        )

    async def revoke(self, refresh_token: str) -> None:
        try:
            await self._post(
                "/revoke",
                {
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": ASTRBOT_DEVICE_CLIENT_ID,
                },
            )
        except OAuthError:
            # Local encrypted data is still removed, including when the user
            # intentionally disconnects while offline.
            return


class EncryptedOAuthVault:
    """Encrypt and scope OAuth state to one AstrBot identity."""

    def __init__(self, plugin: PluginKV, oauth_client: FXMacroDataOAuthClient) -> None:
        self._plugin = plugin
        self._oauth_client = oauth_client

    @staticmethod
    def _fernet() -> Fernet:
        configured = os.getenv(TOKEN_ENCRYPTION_ENV, "").strip()
        if not configured:
            raise OAuthConfigurationError(
                "The AstrBot operator must set FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY before users can connect protected FXMacroData access."
            )
        try:
            return Fernet(configured.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as exc:
            raise OAuthConfigurationError(
                "The AstrBot token-encryption key is invalid. Ask the operator to replace it."
            ) from exc

    async def _load_map(self, key: str) -> dict[str, dict[str, Any]]:
        raw = await self._plugin.get_kv_data(key, {})
        if not isinstance(raw, dict):
            return {}
        return {
            identity: dict(value)
            for identity, value in raw.items()
            if isinstance(identity, str) and isinstance(value, dict)
        }

    @staticmethod
    def _encrypt(fernet: Fernet, value: str) -> str:
        return fernet.encrypt(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _decrypt(fernet: Fernet, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError):
            return None

    @staticmethod
    def _expiry(payload: dict[str, Any], field: str = "expires_in") -> float:
        try:
            seconds = max(int(payload.get(field) or 0), 1)
        except (TypeError, ValueError) as exc:
            raise OAuthError("FXMacroData returned an invalid token response.") from exc
        return time.time() + seconds

    async def start(self, identity: str) -> DeviceAuthorization:
        fernet = self._fernet()
        (
            authorization,
            device_code,
        ) = await self._oauth_client.start_device_authorization()
        pending = await self._load_map(PENDING_DEVICE_STORAGE_KEY)
        pending[identity] = {
            "device_code": self._encrypt(fernet, device_code),
            "expires_at": time.time() + authorization.expires_in,
        }
        await self._plugin.put_kv_data(PENDING_DEVICE_STORAGE_KEY, pending)
        return authorization

    async def complete(self, identity: str) -> None:
        fernet = self._fernet()
        pending = await self._load_map(PENDING_DEVICE_STORAGE_KEY)
        state = pending.get(identity)
        if not state:
            raise OAuthError("Start FXMacroData connection first.")
        try:
            expired = float(state.get("expires_at") or 0) <= time.time()
        except (TypeError, ValueError):
            expired = True
        device_code = self._decrypt(fernet, state.get("device_code"))
        if expired or not device_code:
            pending.pop(identity, None)
            await self._plugin.put_kv_data(PENDING_DEVICE_STORAGE_KEY, pending)
            raise OAuthError("This sign-in request expired. Start a new connection.")
        token_response = await self._oauth_client.exchange_device_code(device_code)
        await self._save_token_response(identity, token_response, fernet=fernet)
        pending.pop(identity, None)
        await self._plugin.put_kv_data(PENDING_DEVICE_STORAGE_KEY, pending)

    async def _save_token_response(
        self, identity: str, token_response: dict[str, Any], *, fernet: Fernet
    ) -> None:
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise OAuthError("FXMacroData returned an incomplete token response.")
        credentials = await self._load_map(CREDENTIAL_STORAGE_KEY)
        credentials[identity] = {
            "access_token": self._encrypt(fernet, access_token),
            "refresh_token": self._encrypt(fernet, refresh_token),
            "expires_at": self._expiry(token_response),
            "refresh_expires_at": self._expiry(token_response, "refresh_expires_in"),
            "scope": str(token_response.get("scope") or "fxmacrodata.read"),
        }
        await self._plugin.put_kv_data(CREDENTIAL_STORAGE_KEY, credentials)

    async def access_token(self, identity: str) -> str | None:
        fernet = self._fernet()
        credentials = await self._load_map(CREDENTIAL_STORAGE_KEY)
        state = credentials.get(identity)
        if not state:
            return None
        access_token = self._decrypt(fernet, state.get("access_token"))
        try:
            expires_at = float(state.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        if access_token and expires_at > time.time() + EXPIRY_SKEW_SECONDS:
            return access_token

        refresh_token = self._decrypt(fernet, state.get("refresh_token"))
        try:
            refresh_expires_at = float(state.get("refresh_expires_at") or 0)
        except (TypeError, ValueError):
            refresh_expires_at = 0
        if not refresh_token or refresh_expires_at <= time.time():
            credentials.pop(identity, None)
            await self._plugin.put_kv_data(CREDENTIAL_STORAGE_KEY, credentials)
            return None
        try:
            token_response = await self._oauth_client.refresh(refresh_token)
            await self._save_token_response(identity, token_response, fernet=fernet)
        except OAuthError:
            credentials = await self._load_map(CREDENTIAL_STORAGE_KEY)
            credentials.pop(identity, None)
            await self._plugin.put_kv_data(CREDENTIAL_STORAGE_KEY, credentials)
            return None
        return self._decrypt(fernet, token_response.get("access_token"))

    async def is_connected(self, identity: str) -> bool:
        return await self.access_token(identity) is not None

    async def disconnect(self, identity: str) -> None:
        fernet = self._fernet()
        credentials = await self._load_map(CREDENTIAL_STORAGE_KEY)
        state = credentials.pop(identity, None)
        await self._plugin.put_kv_data(CREDENTIAL_STORAGE_KEY, credentials)
        pending = await self._load_map(PENDING_DEVICE_STORAGE_KEY)
        pending.pop(identity, None)
        await self._plugin.put_kv_data(PENDING_DEVICE_STORAGE_KEY, pending)
        if state:
            refresh_token = self._decrypt(fernet, state.get("refresh_token"))
            if refresh_token:
                await self._oauth_client.revoke(refresh_token)
