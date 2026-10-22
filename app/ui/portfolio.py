from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st


INDICATOR_LABELS = {
    "price_ratio": "Preço em relação aos profissionais semelhantes (vezes)",
    "peer_reference_available": "Há dados suficientes para comparar preços",
    "provider_documents_30d": "Documentos do profissional em 30 dias",
    "beneficiary_documents_30d": "Documentos do beneficiário em 30 dias (mesmo procedimento)",
    "beneficiary_available": "Beneficiário identificado no pedido",
    "pair_documents_day": "Pedidos do mesmo beneficiário e profissional na mesma data e procedimento",
    "amount_difference_ratio": "Diferença entre valor solicitado e comprovante (%)",
    "duplicate_documents_30d": "Arquivos iguais apresentados em outros pedidos em 30 dias",
    "ocr_confidence": "Confiança na leitura do comprovante (%)",
}

EVIDENCE_LABELS = {
    "unit_amount": "Valor por consulta ou sessão",
    "peer_median": "Preço de referência dos profissionais semelhantes (mediana)",
    "peer_sample_size": "Documentos usados na comparação de preços",
    "peer_providers": "Outros profissionais usados na comparação de preços",
    "peer_provider_volume_median": "Quantidade de documentos de referência por profissional em 30 dias (mediana)",
    "peer_window_days": "Período da comparação de preços (dias)",
    "frequency_window_days": "Período da contagem de documentos (dias)",
    "pair_amount_day": "Total solicitado pelo mesmo beneficiário e profissional na mesma data e procedimento",
    "annual_requested": "Total solicitado pelo beneficiário no ano",
    "annual_limit": "Limite anual informado",
    "annual_requested_limit_percent": "Total solicitado em relação ao limite anual (%)",
    "declared_document_amount": "Valor lido no comprovante",
    "amount_comparison_available": "Leitura suficiente para comparar o valor do comprovante",
    "document_issue_date": "Data de emissão informada",
}

CLAIM_STATUS = {
    "awaiting_review": "Aguardando revisão",
    "ready_for_human_decision": "Aguardando decisão do analista",
    "approved": "Aprovado",
    "denied": "Negado",
    "needs_correction": "Aguardando correção",
}


def money(value: float) -> str:
    return "R$ " + f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def display_value(field: str, value: object) -> str:
    if value is None:
        return "Não disponível"
    if field in {"peer_reference_available", "beneficiary_available", "amount_comparison_available"}:
        return "Sim" if value else "Não"
    if field in {"unit_amount", "peer_median", "pair_amount_day", "annual_requested", "annual_limit", "declared_document_amount"}:
        return money(float(value))
    if field in {"ocr_confidence", "amount_difference_ratio"}:
        return f"{float(value) * 100:.1f}%".replace(".", ",")
    if field == "annual_requested_limit_percent":
        return f"{float(value):.1f}%".replace(".", ",")
    if isinstance(value, (int, float)):
        return f"{value:g}".replace(".", ",")
    if field == "document_issue_date":
        return date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    return str(value)


def render_dashboard(api_url: str, headers: dict[str, str], timeout: int) -> None:
    st.subheader("Risco sob uma nova perspectiva")
    st.caption("Prioridade de investigação antes da decisão da operadora. Pontuação de 0 a 100 não é probabilidade de fraude.")
    filters = st.columns(3)
    synthetic = filters[0].toggle("Usar dados fictícios de demonstração", value=True)
    start = filters[1].date_input("Recebidos desde", value=date.today() - timedelta(days=90))
    end = filters[2].date_input("Recebidos até", value=date.today())
    if start > end:
        st.error("A data inicial deve ser anterior à final.")
        return
    if st.button("Atualizar painel"):
        st.rerun()
    try:
        response = requests.get(f"{api_url}/v1/risk/dashboard", headers=headers,
                                params={"synthetic": str(synthetic).lower(), "start": start.isoformat(), "end": end.isoformat()},
                                timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        st.error(f"Não foi possível consultar o painel de risco: {exc}")
        return
    summary = data["summary"]
    cols = st.columns(4)
    cols[0].metric("Valor em revisão prioritária", money(summary["amount_in_review"]))
    cols[1].metric("Pedidos para revisar", summary["pending_review"])
    cols[2].metric("Profissionais monitorados", summary["providers"])
    cols[3].metric("Beneficiários na amostra", summary["beneficiaries"])
    st.caption("Valores solicitados em pedidos pendentes que atingiram a pontuação mínima para revisão. Não representam fraude confirmada, bloqueio Pix ou economia auditada.")
    if not data["claims"]:
        st.info("Ainda não há pedidos analisados com contexto da operadora neste período e base. Envie um documento com contexto ou carregue a demonstração descrita no README.")
        return
    st.write(f"{summary['documents']} documentos analisados · {summary['ml_scored']} pedidos novos avaliados pelo modelo de inteligência artificial.")
    if not summary["ml_scored"]:
        st.info("As prioridades disponíveis são baseadas em regras. Treine o modelo com o histórico e envie novos pedidos para obter a análise por inteligência artificial.")
    overview, professionals, beneficiaries = st.tabs(["Visão geral", "Profissionais", "Beneficiários"])
    frame = pd.DataFrame(data["claims"])
    with overview:
        st.write("Onde a revisão atua")
        if data["categories"]:
            categories = pd.DataFrame(data["categories"]).rename(columns={"category": "Motivo principal", "amount": "Valor em revisão", "count": "Pedidos"})
            st.bar_chart(categories.set_index("Motivo principal")[["Valor em revisão"]])
            st.dataframe(categories, hide_index=True)
            st.caption("Cada pedido aparece apenas no motivo de maior prioridade; valores não são somados em duplicidade.")
        st.write("Prioridade de investigação")
        cols = ["claim_id", "provider_ref", "beneficiary_ref", "priority", "amount", "primary_category", "status"]
        displayed_claims = frame[cols].copy()
        displayed_claims["status"] = displayed_claims["status"].map(lambda value: CLAIM_STATUS.get(value, value))
        st.dataframe(displayed_claims.rename(columns={
            "claim_id": "Pedido", "provider_ref": "Profissional", "beneficiary_ref": "Beneficiário",
            "priority": "Prioridade de revisão (0 a 100)", "amount": "Valor solicitado (R$)", "primary_category": "Principal motivo de atenção", "status": "Situação do pedido",
        }), hide_index=True)
        selected = st.selectbox("Ver detalhes do pedido", options=list(frame["claim_id"]))
        row = next(r for r in data["claims"] if r["claim_id"] == selected)
        report = row["report"]
        st.write(f"{row['provider_name']} · {row['specialty']} · {row['procedure_code']} · {row['region']}")
        for signal in report["signals"]:
            st.write(f"• {signal['message']}")
        if not report["signals"]:
            st.write("Sem alertas de regra neste pedido. Isso não confirma autenticidade ou elegibilidade.")
        evidence = report["evidence"]
        st.write(f"Valor por consulta ou sessão: {money(evidence['unit_amount'])}; preço de referência de profissionais semelhantes (mediana): "
                 + (money(evidence["peer_median"]) if evidence["peer_median"] is not None else "referência insuficiente")
                 + f" ({evidence['peer_sample_size']} documentos de {evidence['peer_providers']} outros prestadores).")
        model_status = {
            "available": "Modelo aplicado a pedido novo",
            "not_trained": "Ainda sem modelo treinado",
            "different_dataset": "Modelo de outra base",
            "outside_training_period": "Pedido anterior ao treinamento; não avaliado pelo modelo",
            "model_error": "Modelo indisponível; análise por inteligência artificial inconclusiva",
        }
        st.write(f"Análise por inteligência artificial: {model_status.get(report['ml']['status'], 'Situação não disponível')}")
        with st.expander("Evidências e limites da análise"):
            st.write(f"Pontuação de prioridade: {report['priority']}/100 · Pontuação mínima para revisão: {report['threshold']}")
            st.write("Indicadores considerados")
            st.table([
                {"Indicador": label, "Resultado": display_value(field, report["features"].get(field))}
                for field, label in INDICATOR_LABELS.items()
            ])
            st.write("Dados usados como referência")
            st.table([
                {"Informação": label, "Valor": display_value(field, evidence.get(field))}
                for field, label in EVIDENCE_LABELS.items()
            ])
            for limitation in evidence.get("limitations", []):
                st.caption(limitation)
    with professionals:
        st.write("Comparação entre profissionais semelhantes")
        specialties = sorted(frame["specialty"].unique())
        specialty = st.selectbox("Especialidade", specialties)
        subset = frame[frame["specialty"] == specialty]
        procedure = st.selectbox("Procedimento", sorted(subset["procedure_code"].unique()))
        subset = subset[subset["procedure_code"] == procedure]
        region = st.selectbox("Região / bairro", sorted(subset["region"].unique()))
        subset = subset[subset["region"] == region]
        chart = subset[["unit_amount", "provider_documents_30d", "provider_ref"]].rename(columns={
            "unit_amount": "Valor por consulta ou sessão (R$)",
            "provider_documents_30d": "Documentos do profissional em 30 dias",
            "provider_ref": "Profissional",
        })
        st.scatter_chart(chart, x="Valor por consulta ou sessão (R$)", y="Documentos do profissional em 30 dias", color="Profissional")
        st.caption("Eixo horizontal: preço por unidade. Vertical: documentos do prestador nos 30 dias anteriores ao pedido, incluindo o atual. Cada ponto é um pedido.")
        st.dataframe(pd.DataFrame(data["providers"]).rename(columns={
            "reference": "Profissional", "documents": "Documentos no período", "amount": "Valor solicitado",
            "priority": "Maior prioridade de revisão (0 a 100)", "pending_review": "Pedidos prioritários aguardando revisão",
        }), hide_index=True)
    with beneficiaries:
        st.write("Recorrência e contexto do beneficiário")
        context = frame[frame["beneficiary_ref"].notna()].copy()
        if context.empty:
            st.info("Informe referências internas dos beneficiários nos novos pedidos para acompanhar recorrência.")
        else:
            latest = context.sort_values("received_at").drop_duplicates("beneficiary_ref", keep="last")
            limited = latest[latest["annual_requested_limit_percent"].notna()]
            if not limited.empty:
                chart = limited[["annual_requested_limit_percent", "beneficiary_documents_30d", "beneficiary_ref"]].rename(columns={
                    "annual_requested_limit_percent": "Valor solicitado no ano / limite informado (%)",
                    "beneficiary_documents_30d": "Documentos do beneficiário em 30 dias (mesmo procedimento)",
                    "beneficiary_ref": "Beneficiário",
                })
                st.scatter_chart(chart, x="Valor solicitado no ano / limite informado (%)", y="Documentos do beneficiário em 30 dias (mesmo procedimento)", color="Beneficiário")
                st.caption("Uso solicitado conhecido / limite informado (%), versus documentos do mesmo procedimento em 30 dias. Não é saldo contratual. Recorrência pode ser legítima.")
            st.dataframe(pd.DataFrame(data["beneficiaries"]).rename(columns={
                "reference": "Beneficiário", "documents": "Documentos no período", "amount": "Valor solicitado",
                "priority": "Maior prioridade de revisão (0 a 100)", "pending_review": "Pedidos prioritários aguardando revisão",
            }), hide_index=True)
