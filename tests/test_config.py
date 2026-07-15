import pytest
from django.core.exceptions import ImproperlyConfigured

from proofgraph.config import database_config, env_bool


def test_database_config_requires_postgresql() -> None:
    with pytest.raises(ImproperlyConfigured, match="postgres or postgresql"):
        database_config({"DATABASE_URL": "sqlite:///tmp/proofgraph.db"})


def test_database_config_parses_environment_url() -> None:
    config = database_config(
        {
            "DATABASE_URL": (
                "postgresql://proof%40user:secret%20value@db.internal:5433/proofgraph"
                "?sslmode=require"
            ),
            "DATABASE_CONN_MAX_AGE": "30",
        }
    )

    assert config == {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "proofgraph",
        "USER": "proof@user",
        "PASSWORD": "secret value",
        "HOST": "db.internal",
        "PORT": 5433,
        "CONN_MAX_AGE": 30,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"sslmode": "require"},
    }


@pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
def test_env_bool_accepts_true_values(value: str) -> None:
    assert env_bool(value) is True


def test_env_bool_rejects_unknown_value() -> None:
    with pytest.raises(ImproperlyConfigured, match="Invalid boolean"):
        env_bool("sometimes")
