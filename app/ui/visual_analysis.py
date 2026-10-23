import streamlit as st


def render_visual_analysis(report: dict[str, object] | None) -> None:
    if not report or report.get("status") == "not_requested":
        return
    st.write("Análise visual do comprovante — Gemini")
    if report.get("status") != "completed":
        st.warning(str(report.get("message", "Análise visual não concluída; conferir manualmente.")))
    else:
        verdict = report.get("verdict")
        if verdict == "indications_found":
            st.warning("Foram apontados sinais visuais que precisam de conferência.")
        elif verdict == "no_obvious_indications":
            st.info("Nenhum indício visual evidente apontado. A autenticidade não foi confirmada.")
        else:
            st.warning("Análise visual inconclusiva.")
        st.text(str(report.get("summary", "")))
        labels = {
            "possible_edit": "Possível edição", "watermark": "Marca d'água (observação)",
            "layout_inconsistency": "Inconsistência de layout", "image_quality": "Qualidade da imagem",
            "other": "Outra observação",
        }
        findings = report.get("findings", [])
        if findings:
            st.table([{
                "Tipo de observação": labels.get(item["category"], "Outra observação"),
                "Região da imagem": item["location"], "O que foi observado": item["observation"],
            } for item in findings])
    for limitation in report.get("limitations", []):
        st.caption(str(limitation))
