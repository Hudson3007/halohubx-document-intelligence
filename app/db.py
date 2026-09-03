"""
SQLAlchemy engine + session. Synchronous (not async) on purpose — the
main I/O-bound work here (calling the AI provider, delivering webhooks)
already happens as separate HTTP calls, so sync SQLAlchemy keeps this
MVP simpler without a meaningful performance cost at this scale.

Security: deliberately refuses to run as a database superuser (which is
what a bare Supabase `postgres` connection string is) unless explicitly
opted out — production should connect with a least-privilege app role.
Remote connections are forced over TLS unless explicitly disabled.
"""

import os
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL, DB_SSL_DISABLE, ALLOW_SUPERUSER_DB

_SUPERUSER_KEYWORDS = ("postgres", "admin", "root", "master", "superuser")


def _is_private_host(host: str) -> bool:
    """True if the hostname is loopback/localhost, or a literal
    private/loopback/link-local IP.

    Compose/VPC internal names that aren't IP literals (e.g. 'db') are treated
    as public (subject to TLS), which is the safe default — operators pin the
    service name or disable SSL explicitly for trusted bare hosts.
    """
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _guard_database_url(url: str) -> str:
    """Apply security guardrails to the connection string.

    Returns the effective URL (possibly with sslmode added). Raises
    RuntimeError on a superuser connection unless explicitly allowed.
    """
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to your .env file locally, or to "
                           "your host's environment variables in production. Example (Supabase): "
                           "postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres")

    parsed = urlsplit(url)
    username = (parsed.username or "").lower()

    if username in _SUPERUSER_KEYWORDS and not ALLOW_SUPERUSER_DB:
        raise RuntimeError(
            "DATABASE_URL connects as a database superuser "
            f"('{username}'). Production must use a least-privilege app role "
            "that is NOT postgres/admin/root. Create a dedicated role with "
            "only the DML privileges this app needs, and pass its connection "
            "string as DATABASE_URL. If you are absolutely certain you want "
            "to run as superuser (e.g. a throwaway local DB), set "
            "ALLOW_SUPERUSER_DB=1."
        )

    # Force TLS for any public (internet) host unless explicitly disabled.
    # Loopback/localhost + RFC1918/link-local hosts (local dev, private
    # compose/VPC Postgres) are trusted and skip SSL — TLS is handled at the
    # edge there. Non-IP service names like 'db' count as public, so operators
    # either use an IP to opt into "trusted" or set DB_SSL_DISABLE explicitly.
    host = (parsed.hostname or "").lower()
    is_trusted_host = _is_private_host(host)
    if not is_trusted_host and not DB_SSL_DISABLE and parsed.scheme.startswith("postgresql"):
        # Merge a query param set; keep existing params and add sslmode if absent.
        sep = "&" if parsed.query else ""
        if "sslmode=" not in parsed.query:
            url = urlunsplit(parsed._replace(query=parsed.query + sep + "sslmode=require"))

    return url


_DATABASE_URL = _guard_database_url(DATABASE_URL)
engine = create_engine(_DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI dependency — yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
