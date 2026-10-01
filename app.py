"""Interface Streamlit do tutor de inglês."""
import streamlit as st

from llms import DailyQuotaExceeded
from rag import MODES, answer, index_exists, load_index, page_label

st.set_page_config(page_title="Tutor de Inglês (RAG)", page_icon="📘")
st.title("📘 Tutor de Inglês com RAG")
st.caption("Respostas baseadas nos seus livros: Student Book e Workbook.")

if not index_exists():
    st.error("Índice não encontrado. Rode `python ingest.py` primeiro (veja o README).")
    st.stop()


@st.cache_resource
def get_store():
    return load_index()


store = get_store()

with st.sidebar:
    st.header("Configurações")
    mode = st.radio("Modo", list(MODES))
    book = st.selectbox("Buscar em", ["Todos", "Student Book", "Workbook"])
    st.markdown(
        "**Exemplos de perguntas**\n"
        "- Quando uso *has* e *have*?\n"
        "- Como funciona o verbo *to be*?\n"
        "- Como falar as horas em inglês?"
    )

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    st.chat_message(msg["role"]).write(msg["content"])

question = st.chat_input("Pergunte sobre gramática, vocabulário ou peça exercícios")
if question:
    st.chat_message("user").write(question)
    try:
        with st.spinner("Consultando os livros..."):
            response, sources = answer(store, question, mode, None if book == "Todos" else book)
    except DailyQuotaExceeded:
        st.error("A cota diária gratuita do Gemini acabou. Tente novamente amanhã.")
        st.stop()
    st.chat_message("assistant").write(response)
    with st.expander("Trechos dos livros usados na resposta"):
        for s in sources:
            st.caption(f"{s.metadata['book']} · {page_label(s.metadata)}")
            st.write(s.page_content)
            st.divider()
    st.session_state.messages += [
        {"role": "user", "content": question},
        {"role": "assistant", "content": response},
    ]