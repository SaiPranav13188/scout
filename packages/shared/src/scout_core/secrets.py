"""Encrypted secrets stored in Postgres (spec §12: tokens are never stored in plaintext).

Values are encrypted with Fernet using ENCRYPTION_KEY, which lives only in environment
variables (.env locally, GitHub/Render secrets in the cloud).
"""

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout_core.config import get_settings
from scout_core.db.models import EncryptedSecret


class SecretError(Exception):
    pass


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise SecretError("ENCRYPTION_KEY is not set")
    return Fernet(key.get_secret_value().encode())


async def put_secret(session: AsyncSession, name: str, value: str) -> None:
    token = _fernet().encrypt(value.encode())
    stmt = insert(EncryptedSecret).values(name=name, ciphertext=token)
    await session.execute(stmt.on_conflict_do_update(index_elements=[EncryptedSecret.name],
                                                     set_={"ciphertext": token}))


async def get_secret(session: AsyncSession, name: str) -> str | None:
    row = await session.get(EncryptedSecret, name)
    if row is None:
        return None
    try:
        return _fernet().decrypt(row.ciphertext).decode()
    except InvalidToken as exc:
        raise SecretError(f"secret '{name}' cannot be decrypted with this ENCRYPTION_KEY") from exc
