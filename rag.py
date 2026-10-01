"""Consulta: busca semântica -> re-rank -> resposta do tutor."""
import json

from langchain_community.vectorstores import FAISS

from config import INDEX_DIR, PAGE_OFFSET, TOP_K, TOP_N
from llms import get_embeddings, invoke

# Cada modo muda só a instrução final dada ao tutor.
MODES = {
    "Explicar": (
        "Explique o tema de forma simples e didática, em português, "
        "usando exemplos em inglês que aparecem no contexto."
    ),
    "Praticar": (
        "Crie 3 exercícios curtos (completar lacunas ou traduzir) inspirados no "
        "contexto. Coloque o gabarito no final, depois de uma linha com ---."
    ),
}


def page_label(metadata: dict) -> str:
    """Ex.: 'pág. 9 do livro (PDF 10)'. Assim o aluno acha a página no livro físico."""
    pdf_page = metadata["page"]
    printed = pdf_page + PAGE_OFFSET.get(metadata["book"], 0)
    if printed < 1:
        return f"PDF pág. {pdf_page}"
    return f"pág. {printed} do livro (PDF {pdf_page})"


def index_exists() -> bool:
    return INDEX_DIR.exists()


def load_index() -> FAISS:
    return FAISS.load_local(
        str(INDEX_DIR),
        get_embeddings(),
        allow_dangerous_deserialization=True,  # o índice é gerado por você, localmente
    )


def retrieve(store: FAISS, question: str, book: str | None = None, k: int = TOP_K):
    """Busca semântica, opcionalmente restrita a um livro (filtro por metadado)."""
    if book:
        return store.similarity_search(question, k=k, filter={"book": book})
    return store.similarity_search(question, k=k)


def rerank(question: str, docs: list, top_n: int = TOP_N) -> list:
    """Re-rank em uma única chamada: o LLM escolhe os trechos mais relevantes."""
    if len(docs) <= top_n:
        return docs
    numbered = "\n\n".join(f"[{i}] {d.page_content}" for i, d in enumerate(docs))
    prompt = (
        f"Pergunta de um aluno de inglês: {question}\n\nTrechos:\n{numbered}\n\n"
        f"Retorne SOMENTE uma lista JSON com os índices dos {top_n} trechos mais "
        "úteis para responder, do mais ao menos relevante. Exemplo: [2, 0, 4]"
    )
    raw = invoke(prompt)
    try:
        idx = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
        ranked = [docs[i] for i in idx if 0 <= i < len(docs)]
        return ranked[:top_n] or docs[:top_n]
    except (ValueError, TypeError):
        return docs[:top_n]  # fallback: mantém a ordem da busca semântica


def answer(store: FAISS, question: str, mode: str = "Explicar", book: str | None = None):
    """Pipeline completo. Retorna (resposta, trechos_usados)."""
    best = rerank(question, retrieve(store, question, book))
    context = "\n\n".join(
        f"({d.metadata['book']}, {page_label(d.metadata)})\n{d.page_content}"
        for d in best
    )
    prompt = (
        "Você é um tutor de inglês para brasileiros iniciantes (nível elementary). "
        "Use APENAS o contexto abaixo, que vem dos livros do aluno. "
        "Se o contexto não cobrir a pergunta, diga isso com honestidade. "
        "Cite o livro e a página quando fizer sentido. Só atribua ao livro frases que "
        "aparecem literalmente no contexto; se você completar uma lacuna (___) ou "
        "deduzir algo, avise que é uma dedução. Não misture perguntas e respostas de "
        "diálogos diferentes.\n\n"
        f"Tarefa: {MODES[mode]}\n\n"
        f"Contexto:\n{context}\n\nPergunta do aluno: {question}"
    )
    return invoke(prompt, temperature=0.2), best