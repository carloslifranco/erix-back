from datetime import date

import pytest

from app.services.verification import valid_cpf, verify_document


def statuses(report):
    return {check["field"]: check["status"] for check in report["checks"]}


@pytest.mark.parametrize("value, expected", [
    ("529.982.247-25", True),  # checksum fixture, not an identity assertion
    ("52998224725", True),
    ("529.982.247-24", False),
    ("111.111.111-11", False),
    ("00000000000", False),
    ("5299822472", False),
    ("52998224725abc", False),
])
def test_cpf_checksum(value, expected):
    assert valid_cpf(value) is expected


def test_matching_data_never_becomes_verified_identity():
    report = verify_document(
        "Paciente: José Exemplo\nCPF do paciente: 529.982.247-25\n"
        "Nascimento do paciente: 01/02/1990\nCRM-SP 12345\nCRP 06/12345",
        0.99, patient_name="JOSE EXEMPLO", patient_cpf="52998224725",
        patient_birth_date=date(1990, 2, 1),
    )
    result = statuses(report)
    assert result["patient_name"] == result["patient_cpf"] == result["patient_birth_date"] == "matches_input"
    assert result["image_authenticity"] == result["official_identity"] == "not_verified"
    assert result["professional_registration_1"] == result["professional_registration_2"] == "not_verified"
    assert report["external_checks_performed"] is False
    assert "52998224725" not in str(report) and "529.982.247-25" not in str(report)


def test_professionals_identity_is_not_assigned_to_patient():
    report = verify_document(
        "Nome: José Exemplo\nCPF: 529.982.247-25\nNascimento: 01/02/1990",
        0.99, patient_name="José Exemplo", patient_cpf="52998224725",
        patient_birth_date=date(1990, 2, 1),
    )
    result = statuses(report)
    assert all(result[key] == "needs_review" for key in ("patient_name", "patient_cpf", "patient_birth_date"))


def test_invalid_cpf_and_mismatches_are_review_signals():
    report = verify_document(
        "Paciente: Outra Pessoa\nCPF do paciente: 111.111.111-11\nNascimento do paciente: 31/02/1990",
        0.99, patient_name="José Exemplo", patient_birth_date=date(1990, 2, 1),
    )
    result = statuses(report)
    assert result["cpf_1"] == result["patient_name"] == result["patient_birth_date_format"] == "needs_review"
    assert report["needs_review"] is True


def test_low_confidence_and_multiple_patient_names_remain_uncertain():
    assert statuses(verify_document("Paciente: José Exemplo", 0.1, patient_name="José Exemplo"))["patient_name"] == "needs_review"
    assert statuses(verify_document("Paciente: José Exemplo\nPaciente: Outra Pessoa", 0.99, patient_name="José Exemplo"))["patient_name"] == "needs_review"


def test_missing_data_is_not_an_automatic_identity_match():
    result = statuses(verify_document("", 0.0))
    assert result["patient_cpf"] == "not_provided"
    assert result["professional_registration"] == "not_found"
