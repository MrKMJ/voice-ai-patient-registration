import uuid

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def create(client, payload):
    return client.post("/patients", json=payload)


def test_create_and_get(client, patient_payload):
    res = create(client, patient_payload)
    assert res.status_code == 201
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    uuid.UUID(data["patient_id"])
    assert data["phone_number"] == "2125550134"
    assert data["date_of_birth"] == "03/14/1985"
    assert data["preferred_language"] == "English"
    assert data["created_at"].endswith("Z") and data["deleted_at"] is None

    fetched = client.get(f"/patients/{data['patient_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["data"]["last_name"] == "Davis"


def test_list_filters(client, patient_payload):
    create(client, patient_payload)
    other = {**patient_payload, "first_name": "Sam", "last_name": "Lee",
             "phone_number": "4155550199", "date_of_birth": "01/02/1990"}
    create(client, other)

    assert len(client.get("/patients").json()["data"]) == 2
    assert client.get("/patients?last_name=davis").json()["data"][0]["first_name"] == "Jane"
    assert client.get("/patients?date_of_birth=01/02/1990").json()["data"][0]["last_name"] == "Lee"
    assert client.get("/patients?phone_number=(415)555-0199").json()["data"][0]["last_name"] == "Lee"
    assert client.get("/patients?last_name=nobody").json()["data"] == []
    assert client.get("/patients").json()["meta"]["total"] == 2


def test_list_rejects_bad_filter(client):
    res = client.get("/patients?phone_number=123")
    assert res.status_code == 400
    assert res.json()["data"] is None and res.json()["error"]["code"] == "bad_request"


def test_validation_errors_are_422_with_field_details(client, patient_payload):
    patient_payload.update(phone_number="555", date_of_birth="01/01/2999", state="ZZ")
    del patient_payload["city"]
    res = create(client, patient_payload)
    assert res.status_code == 422
    fields = {d["field"]: d["message"] for d in res.json()["error"]["details"]}
    assert set(fields) == {"phone_number", "date_of_birth", "state", "city"}
    assert "future" in fields["date_of_birth"]


def test_unknown_fields_rejected(client, patient_payload):
    assert create(client, {**patient_payload, "ssn": "123"}).status_code == 422


def test_malformed_json_is_400(client):
    res = client.post("/patients", content="{not json", headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "bad_request"


def test_bad_uuid_400_and_missing_404(client):
    assert client.get("/patients/not-a-uuid").status_code == 400
    res = client.get(f"/patients/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.json() == {"data": None, "error": {"code": "not_found", "message": "Patient not found.", "details": []}}


def test_unknown_route_uses_envelope(client):
    res = client.get("/nope")
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"


def test_partial_update(client, patient_payload):
    pid = create(client, patient_payload).json()["data"]["patient_id"]
    before = client.get(f"/patients/{pid}").json()["data"]
    res = client.put(f"/patients/{pid}", json={"city": "Queens", "email": "Jane@Example.com"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["city"] == "Queens" and data["email"] == "jane@example.com"
    assert data["first_name"] == "Jane"
    assert data["updated_at"] >= before["updated_at"]
    assert data["created_at"] == before["created_at"]


def test_update_validation_and_empty_body(client, patient_payload):
    pid = create(client, patient_payload).json()["data"]["patient_id"]
    assert client.put(f"/patients/{pid}", json={"phone_number": "1"}).status_code == 422
    assert client.put(f"/patients/{pid}", json={"first_name": None}).status_code == 422
    assert client.put(f"/patients/{pid}", json={}).status_code == 400


def test_optional_field_can_be_cleared(client, patient_payload):
    pid = create(client, {**patient_payload, "email": "a@b.co"}).json()["data"]["patient_id"]
    res = client.put(f"/patients/{pid}", json={"email": None})
    assert res.json()["data"]["email"] is None


def test_soft_delete_hides_but_keeps_row(client, patient_payload, settings):
    pid = create(client, patient_payload).json()["data"]["patient_id"]
    res = client.delete(f"/patients/{pid}")
    assert res.status_code == 200 and res.json()["data"]["deleted_at"]
    assert client.get(f"/patients/{pid}").status_code == 404
    assert client.get("/patients").json()["data"] == []
    assert client.put(f"/patients/{pid}", json={"city": "X"}).status_code == 404

    from sqlalchemy import text
    with client.app.state.engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM patients")).scalar() == 1


def test_database_constraints_backstop_the_api(client):
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError
    import pytest
    with client.app.state.engine.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO patients (patient_id, first_name, last_name, date_of_birth, sex, phone_number,"
            " address_line_1, city, state, zip_code, preferred_language, created_at, updated_at)"
            " VALUES ('x','A','B','1990-01-01','Robot','123','s','c','NY','11201','English',"
            "'2024-01-01','2024-01-01')"
        ))


def test_data_survives_restart(settings, patient_payload):
    with TestClient(create_app(settings)) as first:
        pid = create(first, patient_payload).json()["data"]["patient_id"]
    with TestClient(create_app(settings)) as second:
        assert second.get(f"/patients/{pid}").json()["data"]["first_name"] == "Jane"


def test_api_key_enforced_when_configured(tmp_path, patient_payload):
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'k.db'}", api_key="k1", _env_file=None)
    with TestClient(create_app(cfg)) as c:
        assert c.get("/patients").status_code == 401
        assert c.get("/patients", headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.get("/patients", headers={"X-API-Key": "k1"}).status_code == 200
        assert c.get("/health").status_code == 200  # health stays open for platform probes


def test_seed_data(tmp_path):
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 's.db'}", seed_demo_data=True, _env_file=None)
    with TestClient(create_app(cfg)) as c:
        assert len(c.get("/patients").json()["data"]) == 2
    with TestClient(create_app(cfg)) as c:  # idempotent
        assert len(c.get("/patients").json()["data"]) == 2


def test_health_and_dashboard(client):
    assert client.get("/health").json() == {"data": {"status": "ok"}, "error": None, "meta": None}
    page = client.get("/dashboard")
    assert page.status_code == 200 and "Patient Registrations" in page.text
