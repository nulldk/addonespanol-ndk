import os


def load_secrets():
    """Kept for older callers. Secrets are read directly from the environment."""
    return None


def get_secret(key, default=None):
    """Return a secret from the environment."""
    return os.getenv(key, default)
