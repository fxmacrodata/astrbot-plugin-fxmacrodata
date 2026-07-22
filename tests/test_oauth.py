from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from oauth import (
    CREDENTIAL_STORAGE_KEY,
    PENDING_DEVICE_STORAGE_KEY,
    DeviceAuthorization,
    EncryptedOAuthVault,
    OAuthConfigurationError,
    OAuthPendingError,
)


class FakeKV:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    async def get_kv_data(self, key, default):
        return self.values.get(key, default)

    async def put_kv_data(self, key, value):
        self.values[key] = value


class FakeOAuthClient:
    def __init__(self) -> None:
        self.approved = False
        self.revoked: list[str] = []

    async def start_device_authorization(self):
        return (
            DeviceAuthorization(
                verification_uri_complete="https://example.test/verify?user_code=TEST-CODE",
                user_code="TEST-CODE",
                expires_in=600,
                interval=5,
            ),
            "device-code-for-test-only",
        )

    async def exchange_device_code(self, device_code):
        assert device_code == "device-code-for-test-only"
        if not self.approved:
            raise OAuthPendingError("Finish approval in your FXMacroData browser page.")
        return {
            "access_token": "opaque-access-token-for-test-only",
            "refresh_token": "opaque-refresh-token-for-test-only",
            "expires_in": 3600,
            "refresh_expires_in": 86400,
            "scope": "fxmacrodata.read",
        }

    async def refresh(self, refresh_token):
        assert refresh_token == "opaque-refresh-token-for-test-only"
        return {
            "access_token": "rotated-access-token-for-test-only",
            "refresh_token": "rotated-refresh-token-for-test-only",
            "expires_in": 3600,
            "refresh_expires_in": 86400,
            "scope": "fxmacrodata.read",
        }

    async def revoke(self, refresh_token):
        self.revoked.append(refresh_token)


@pytest.mark.asyncio
async def test_vault_encrypts_user_tokens_and_keeps_users_separate(monkeypatch):
    monkeypatch.setenv(
        "FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode()
    )
    kv = FakeKV()
    oauth_client = FakeOAuthClient()
    vault = EncryptedOAuthVault(kv, oauth_client)

    authorization = await vault.start("chat:telegram:person-a")

    assert authorization.user_code == "TEST-CODE"
    assert "device-code-for-test-only" not in str(kv.values[PENDING_DEVICE_STORAGE_KEY])
    with pytest.raises(OAuthPendingError):
        await vault.complete("chat:telegram:person-a")

    oauth_client.approved = True
    await vault.complete("chat:telegram:person-a")

    assert (
        await vault.access_token("chat:telegram:person-a")
        == "opaque-access-token-for-test-only"
    )
    assert await vault.access_token("chat:telegram:person-b") is None
    assert "opaque-access-token-for-test-only" not in str(
        kv.values[CREDENTIAL_STORAGE_KEY]
    )

    await vault.disconnect("chat:telegram:person-a")

    assert await vault.access_token("chat:telegram:person-a") is None
    assert oauth_client.revoked == ["opaque-refresh-token-for-test-only"]


@pytest.mark.asyncio
async def test_vault_requires_operator_encryption_key(monkeypatch):
    monkeypatch.delenv("FXMACRODATA_ASTRBOT_TOKEN_ENCRYPTION_KEY", raising=False)

    vault = EncryptedOAuthVault(FakeKV(), FakeOAuthClient())

    with pytest.raises(OAuthConfigurationError):
        await vault.start("chat:telegram:person-a")
