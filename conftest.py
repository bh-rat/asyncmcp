"""
Top-level pytest configuration for AsyncMCP tests.
"""

import pytest

# Configure anyio backend for tests
pytest_plugins = ("anyio",)


@pytest.fixture(scope="session")
def anyio_backend():
    """Configure anyio backend for tests."""
    return "asyncio"
