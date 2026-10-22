"""Free local consistency checks. No registry or image-authenticity claim is made."""

import re
import unicodedata
from datetime import date, datetime, timezone

from app.core.config import get_settings


OFFICIAL_SOURCES = {
    "CRM": "https://portal.cfm.org.br/busca-medicos",
    "CRP": "https://cadastro.cfp.org.br/",
    "CPF": "https://servicos.receita.fazenda.gov.br/Servicos/CPF/ConsultaSituacao/ConsultaPublica.asp",
    "NFS-e": "https://www.gov.br/pt-br/servicos/consultar-nota-fiscal-de-servico-eletronica",
}


def normalize_name(value: str) -> str:
    plain = unicodedata.normalize("NFKD", value)
    return " ".join("".join(c for c in plain if not unicodedata.combining(c)).upper().split())


def cpf_digits(value: str) -> str:
    return re.sub(r"[.\-\s]", "", value)


def valid_cpf(value: str) -> bool:
    digits = cpf_digits(value)
    if not re.fullmatch(r"[0-9]{11}", digits) or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11
        if int(digits[size]) != (0 if check == 10 else check):
            return False
    return True


def _field(text: str, label: str, value_pattern: str) -> tuple[str | None, bool]:
    # Require an explicit label at the start of a line to avoid assigning the
    # professional's CPF/name to the patient. Multiple values remain ambiguous.
    values = list(dict.fromkeys(re.findall(
        rf"^[ \t]*(?:{label})[ \t]*:[ \t]*({value_pattern})[ \t]*$",
        text, flags=re.IGNORECASE | re.MULTILINE,
    )))
    return (values[0] if len(values) == 1 else None), len(values) > 1


def verify_document(
    text: str,
    ocr_confidence: float,
    patient_name: str | None = None,
    patient_cpf: str | None = None,
    patient_birth_date: date | None = None,
) -> dict[str, object]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    checks: list[dict[str, object]] = []
    low_confidence = ocr_confidence < get_settings().low_ocr_confidence_threshold

    def add(field: str, status: str, message: str, **extra: object) -> None:
        checks.append({"field": field, "status": status, "message": message, **extra})

    add("image_authenticity", "not_verified",
        "A autenticidade da imagem não foi verificada. OCR e consistência não detectam toda adulteração.")
    add("official_identity", "not_verified",
        "CPF, nome e nascimento não foram consultados em uma base oficial.",
        source_url=OFFICIAL_SOURCES["CPF"])
    add("ocr", "needs_review" if low_confidence else "reading_only",
        "Confiança de leitura; não é probabilidade de autenticidade.", confidence=ocr_confidence)

    # Check every explicitly labelled CPF, without inferring its owner.
    cpfs = list(dict.fromkeys(re.findall(
        r"\bCPF(?:[ \t]+(?:do|da)[ \t]+[^:\n]{1,40})?[ \t]*:[ \t]*([0-9.\-]{11,14})(?![0-9.\-])",
        text, flags=re.IGNORECASE,
    )))
    for index, cpf in enumerate(cpfs, start=1):
        ok = valid_cpf(cpf)
        add(f"cpf_{index}", "format_valid" if ok else "needs_review",
            "Dígitos verificadores válidos; existência e titularidade não confirmadas."
            if ok else "CPF lido com formato ou dígitos inválidos; confira a imagem antes de concluir.",
            masked_value="***.***.***-" + cpf_digits(cpf)[-2:])

    # CRM and CRP have distinct region conventions. Recognition is not a
    # validation of registration number, professional name or active status.
    registrations = list(dict.fromkeys(re.findall(
        r"\b(?:CRM[ \t]*(?:[-/:][ \t]*)?(?:[A-Z]{2}[ \t]*[-/:]?[ \t]*)?[0-9]{1,8}(?:[ \t]*[-/][ \t]*[A-Z]{2})?"
        r"|CRP[ \t]*[-:]?[ \t]*[0-9]{1,2}[ \t]*/[ \t]*[0-9]{1,8})(?![0-9])",
        text, flags=re.IGNORECASE,
    )))
    for index, registration in enumerate(registrations, start=1):
        council = registration[:3].upper()
        add(f"professional_registration_{index}", "not_verified",
            "Registro identificado no OCR. Confira nome, região e situação na fonte oficial.",
            extracted_value=registration, council=council, source_url=OFFICIAL_SOURCES[council])
    if not registrations:
        add("professional_registration", "not_found",
            "CRM/CRP não identificado nos formatos suportados. Outros conselhos exigem consulta específica.")

    name, name_ambiguous = _field(text, r"(?:Nome[ \t]+do[ \t]+)?(?:paciente|cliente|benefici[aá]rio)", r"[^\n\r]{2,200}")
    cpf, cpf_ambiguous = _field(text, r"CPF[ \t]+do[ \t]+(?:paciente|cliente|benefici[aá]rio)", r"[0-9.\-]{11,14}")
    # A generic birth date could belong to the professional. Only the explicitly
    # patient-labelled form is used for a comparison below.
    patient_birth, patient_birth_ambiguous = _field(
        text, r"(?:Data[ \t]+de[ \t]+nascimento|Nascimento)[ \t]+do[ \t]+(?:paciente|cliente|benefici[aá]rio)",
        r"[0-9]{2}[/.-][0-9]{2}[/.-][0-9]{4}",
    )
    parsed_birth = None
    if patient_birth:
        try:
            parsed_birth = datetime.strptime(re.sub(r"[.-]", "/", patient_birth), "%d/%m/%Y").date()
        except ValueError:
            add("patient_birth_date_format", "needs_review", "Data de nascimento lida é inválida.")
        if parsed_birth and parsed_birth > date.today():
            add("patient_birth_date_format", "needs_review", "Data de nascimento lida está no futuro.")

    comparisons = [
        ("patient_name", patient_name, name, name_ambiguous, normalize_name),
        ("patient_cpf", patient_cpf, cpf, cpf_ambiguous, cpf_digits),
        ("patient_birth_date", patient_birth_date, parsed_birth, patient_birth_ambiguous, lambda x: x),
    ]
    for field, expected, extracted, ambiguous, normalize in comparisons:
        if expected is None:
            add(field, "not_provided", "Dado de referência não informado para comparação.")
        elif ambiguous or extracted is None:
            add(field, "needs_review", "Campo ausente, ambíguo ou sem rótulo do paciente no OCR.")
        elif low_confidence:
            add(field, "needs_review", "OCR com baixa confiança; confira o campo na imagem.")
        else:
            matches = normalize(expected) == normalize(extracted)
            add(field, "matches_input" if matches else "needs_review",
                "Coincide com o dado informado; identidade não confirmada."
                if matches else "Não coincide com o dado informado; confira o OCR e o cadastro.")
    if patient_cpf is not None and not valid_cpf(patient_cpf):
        add("input_patient_cpf", "needs_review", "CPF informado tem formato ou dígitos verificadores inválidos.")
    if patient_birth_date and patient_birth_date > date.today():
        add("input_patient_birth_date", "needs_review", "Nascimento informado está no futuro.")

    return {
        "version": "local-checks-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "external_checks_performed": False,
        "needs_review": any(check["status"] == "needs_review" for check in checks),
        "checks": checks,
        "official_sources": OFFICIAL_SOURCES,
    }
