import os
from datetime import date

import requests
import streamlit as st

API_URL = os.getenv("STREAMLIT_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("STREAMLIT_API_KEY", "dev-only-change-me")
HEADERS = {"X-API-Key": API_KEY}
TIMEOUT_SECONDS = 20

VERIFICATION_STATUS = {
    "not_verified": "Não verificado em fonte externa",
    "reading_only": "Confiança de leitura",
    "format_valid": "Dígitos válidos; titularidade não confirmada",
    "needs_review": "Conferir manualmente",
    "not_found": "Não identificado no OCR",
    "not_provided": "Referência não informada",
    "matches_input": "Coincide com o informado; identidade não confirmada",
}

st.set_page_config(page_title="Erix | Reembolsos", page_icon="🧾", layout="wide")
st.title("Erix — análise de reembolsos")
st.warning(
    "Protótipo acadêmico. Os alertas apenas encaminham pedidos para revisão humana; "
    "não use dados reais de saúde nem tome decisões de pagamento com esta versão."
)

try:
    response = requests.get(f"{API_URL}/v1/claims", headers=HEADERS, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    claims = response.json()
except requests.RequestException as exc:
    st.error(f"Não foi possível conectar à API ({exc}). Verifique se o serviço está em execução.")
    st.stop()

upload_tab, review_tab = st.tabs(["Enviar solicitação", "Fila de revisão"])

with upload_tab:
    with st.form("new_claim"):
        provider = st.text_input("Nome do prestador")
        consultation_date = st.date_input("Data da consulta", value=date.today())
        amount = st.number_input("Valor solicitado (R$)", min_value=0.01, step=10.0)
        document = st.file_uploader("Comprovante (JPEG ou PNG)", type=["jpg", "jpeg", "png"])
        submitted = st.form_submit_button("Enviar para análise")

    if submitted:
        if not provider.strip() or document is None:
            st.error("Informe o prestador e selecione um comprovante.")
        else:
            try:
                created = requests.post(
                    f"{API_URL}/v1/claims",
                    headers=HEADERS,
                    json={
                        "provider_name": provider.strip(),
                        "consultation_date": consultation_date.isoformat(),
                        "requested_amount": amount,
                    },
                    timeout=TIMEOUT_SECONDS,
                )
                created.raise_for_status()
                claim = created.json()
                uploaded = requests.post(
                    f"{API_URL}/v1/claims/{claim['id']}/document",
                    headers=HEADERS,
                    files={"file": (document.name, document.getvalue(), document.type)},
                    timeout=TIMEOUT_SECONDS,
                )
                uploaded.raise_for_status()
                st.success(f"Solicitação {claim['id']} enviada. Atualize a página para acompanhar o processamento.")
            except requests.RequestException as exc:
                st.error(f"Falha ao enviar solicitação: {exc}")

with review_tab:
    reviewable = [claim for claim in claims if claim["status"] in {"awaiting_review", "ready_for_human_decision"}]
    if not reviewable:
        st.info("Não há solicitações aguardando decisão humana.")
    for claim in reviewable:
        with st.expander(
            f"{claim['provider_name']} — R$ {claim['requested_amount']} — {claim['status']}"
        ):
            st.write(f"Consulta: {claim['consultation_date']}")
            st.write(f"Sinais para análise: {', '.join(claim['risk_signals']) or 'nenhum sinal automático'}")
            st.write(f"Pontuação de triagem: {claim['risk_score']}")
            fields = claim.get("extracted_fields") or {}
            st.write("Dados lidos do comprovante")
            st.json({key: value for key, value in fields.items() if key != "verification"})
            report = fields.get("verification")
            st.caption(
                "Verificação gratuita de consistência. Não confirma autenticidade da imagem, "
                "existência do CPF ou situação profissional. Nenhum dado é enviado a serviços externos."
            )
            with st.form(f"verification-{claim['id']}"):
                st.write("Comparar com dados de referência do paciente (opcional)")
                patient_name = st.text_input("Nome do paciente", max_chars=200)
                patient_cpf = st.text_input("CPF do paciente", max_chars=14)
                patient_birth = st.date_input(
                    "Nascimento do paciente", value=None,
                    min_value=date(1900, 1, 1), max_value=date.today(),
                )
                st.caption(
                    "A comparação exige rótulos como 'Paciente:', 'CPF do paciente:' e "
                    "'Nascimento do paciente:' no comprovante. Campos ausentes ficam para conferência."
                )
                if st.form_submit_button("Verificar consistência sem custo"):
                    try:
                        verified = requests.post(
                            f"{API_URL}/v1/claims/{claim['id']}/verification",
                            headers=HEADERS,
                            json={
                                "patient_name": patient_name.strip() or None,
                                "patient_cpf": patient_cpf.strip() or None,
                                "patient_birth_date": patient_birth.isoformat() if patient_birth else None,
                            },
                            timeout=TIMEOUT_SECONDS,
                        )
                        verified.raise_for_status()
                        report = verified.json()
                        st.success("Comparação concluída. Veja os resultados abaixo.")
                    except requests.RequestException as exc:
                        st.error(f"Falha na comparação: {exc}")
            if report:
                st.table([
                    {
                        "Campo": check["field"],
                        "Resultado": VERIFICATION_STATUS.get(check["status"], check["status"]),
                        "Detalhe": check["message"],
                        "Registro / CPF mascarado": check.get("extracted_value", check.get("masked_value", "")),
                    }
                    for check in report["checks"]
                ])
                st.write("Consultas oficiais para conferência manual")
                for label, url in report["official_sources"].items():
                    st.link_button(f"Consultar {label}", url)
                st.caption(
                    "Nos conselhos, confira nome, número, região e situação do registro. "
                    "Registre a fonte, a data e o resultado da consulta na justificativa da decisão abaixo. "
                    "Abrir um link não registra uma validação automática."
                )
            with st.form(f"review-{claim['id']}"):
                reviewer = st.text_input("Nome do analista", key=f"reviewer-{claim['id']}")
                rationale = st.text_area("Justificativa da decisão", key=f"rationale-{claim['id']}")
                decision = st.selectbox(
                    "Decisão humana",
                    ["approved", "needs_correction", "denied"],
                    format_func={
                        "approved": "Aprovado",
                        "needs_correction": "Solicitar correção",
                        "denied": "Negado conforme regras do benefício",
                    }.get,
                    key=f"decision-{claim['id']}",
                )
                if st.form_submit_button("Registrar decisão"):
                    try:
                        result = requests.post(
                            f"{API_URL}/v1/claims/{claim['id']}/reviews",
                            headers=HEADERS,
                            json={"reviewer": reviewer, "rationale": rationale, "decision": decision},
                            timeout=TIMEOUT_SECONDS,
                        )
                        result.raise_for_status()
                        st.success("Decisão registrada. Atualize a página.")
                    except requests.RequestException as exc:
                        st.error(f"Falha ao registrar decisão: {exc}")

with st.expander("Solicitações recentes"):
    st.dataframe(
        [
            {
                "ID": claim["id"],
                "Prestador": claim["provider_name"],
                "Data": claim["consultation_date"],
                "Valor": str(claim["requested_amount"]),
                "Status": claim["status"],
            }
            for claim in claims
        ],
        use_container_width=True,
        hide_index=True,
    )
