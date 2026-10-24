import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

VALID_PATIENT = {
    "first_name": "Jane",
    "last_name": "Davis",
    "date_of_birth": "03/14/1985",
    "sex": "Female",
    "phone_number": "(212) 555-0134",
    "address_line_1": "123 Main Street",
    "city": "Brooklyn",
    "state": "NY",
    "zip_code": "11201",
}


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        vapi_webhook_secret="s3cret",
        seed_demo_data=False,
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def patient_payload():
    return dict(VALID_PATIENT)
