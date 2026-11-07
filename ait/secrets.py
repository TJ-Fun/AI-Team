"""Encrypted credential storage and provider key resolution."""

from __future__ import annotations

from ait.common import *


# --- Encrypted Credentials & Connections ---
def secret_cipher() -> Any | None:
    if Fernet is None:
        return None
    env_key = os.environ.get("TEAM_SECRET_KEY", "").strip()
    if env_key:
        return Fernet(env_key.encode("utf-8"))
    ensure_data()
    key_path = DATA / "settings" / "secret.key"
    if not key_path.is_file():
        generated = Fernet.generate_key()
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(generated)
    return Fernet(key_path.read_bytes().strip())


def read_saved_secrets() -> dict[str, str]:
    cipher = secret_cipher()
    enc_path = DATA / "settings" / "secrets.enc"
    if cipher is None or not enc_path.is_file():
        return {}
    try:
        raw = cipher.decrypt(enc_path.read_bytes())
        parsed = json.loads(raw.decode("utf-8"))
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str) and v.strip()}
    except (InvalidToken, ValueError, OSError):
        return {}
    return {}


def write_saved_secrets(secrets: dict[str, str]) -> None:
    cipher = secret_cipher()
    if cipher is None:
        raise ValueError("Python 'cryptography' package is required to store encrypted credentials.")
    ensure_data()
    enc_path = DATA / "settings" / "secrets.enc"
    token = cipher.encrypt(json.dumps(secrets).encode("utf-8"))
    tmp_path = enc_path.with_suffix(".enc.tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(token)
    tmp_path.replace(enc_path)


def get_provider_key_and_source(conn_id: str) -> tuple[str, str]:
    spec = CONNECTION_SPECS.get(conn_id)
    if not spec:
        return "", ""
    saved = read_saved_secrets().get(conn_id, "").strip()
    if saved:
        return saved, "Encrypted on VM"
    env_val = os.environ.get(spec[1], "").strip()
    if env_val:
        return env_val, f"Environment ({spec[1]})"
    return "", ""


def get_provider_key(conn_id: str) -> str:
    return get_provider_key_and_source(conn_id)[0]


def configured_providers() -> list[str]:
    result: list[str] = []
    if get_provider_key("gemini"):
        result.append("Gemini")
    if get_provider_key("groq"):
        result.append("GroqCloud")
    if get_provider_key("openrouter"):
        result.append("OpenRouter")
    return result
