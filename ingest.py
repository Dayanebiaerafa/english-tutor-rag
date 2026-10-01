"""Ingestão: PDFs -> texto por página -> chunks -> embeddings -> FAISS.
 
Páginas com texto selecionável são lidas direto do PDF.
Páginas escaneadas (imagem) são transcritas por um modelo com visão, várias
páginas por chamada (para economizar a cota diária do plano gratuito).
Tudo é guardado em cache/, então cada página só é transcrita UMA vez.
 
Se a cota diária acabar, o programa para de transcrever, monta o índice com o
que já tem e avisa. Rode de novo no dia seguinte: ele continua de onde parou.
 
Uso:
    python ingest.py --limit 10   # teste: só as 10 primeiras páginas de cada livro
    python ingest.py              # todas as páginas
"""
import argparse
import base64
import json
import re
import time
from pathlib import Path
 
import pymupdf
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from langchain_text_splitters import RecursiveCharacterTextSplitter
 
from config import (
    BOOKS_DIR, CACHE_DIR, CHUNK_OVERLAP, CHUNK_SIZE, EMBED_BATCH, EMBED_DELAY,
    INDEX_DIR, MIN_TEXT_CHARS, PAGES_PER_REQUEST, REQUEST_DELAY, RENDER_DPI,
)
from llms import DailyQuotaExceeded, get_embeddings, invoke, is_not_found, is_temporary, with_retry
 
TRANSCRIBE_PROMPT = (
    "Você receberá páginas escaneadas de um livro de inglês. Antes de cada imagem "
    "há o rótulo 'PÁGINA N:'. Para CADA página, escreva uma linha exatamente assim: "
    "=== PÁGINA N === (com o N informado) e, logo abaixo, a transcrição fiel de todo "
    "o texto da página, mantendo a ordem de leitura, títulos, enunciados e exemplos. "
    "Represente lacunas com ___. Não resolva exercícios, não traduza e não comente."
)
 
 
def book_label(pdf_path: Path) -> str:
    """Nome amigável do livro, usado como metadado e como filtro na interface."""
    name = pdf_path.stem.upper()
    if name.endswith("WB"):
        return "Workbook"
    if name.endswith("SB"):
        return "Student Book"
    return pdf_path.stem
 
 
def parse_pages(raw: str, expected: list[int]) -> dict[int, str]:
    """Separa a resposta do modelo em {número_da_página: texto}."""
    parts = re.split(r"===\s*P[ÁA]GINA\s+(\d+)\s*===", raw)
    result = {}
    for k in range(1, len(parts) - 1, 2):
        number = int(parts[k])
        if number - 1 in expected:
            result[number] = parts[k + 1].strip()
    return result
 
 
def transcribe_pages(doc, indexes: list[int]) -> dict[int, str]:
    """Envia várias páginas de uma vez e devolve {número_da_página: texto}."""
    content = [{"type": "text", "text": TRANSCRIBE_PROMPT}]
    for i in indexes:
        image = base64.b64encode(doc[i].get_pixmap(dpi=RENDER_DPI).tobytes("jpeg")).decode()
        content.append({"type": "text", "text": f"PÁGINA {i + 1}:"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}})
    return parse_pages(invoke([HumanMessage(content=content)]), indexes)
 
 
def extract_pages(pdf_path: Path, limit: int | None = None, vision: bool = True):
    """Retorna ({página: texto}, [páginas puladas], cota_esgotada)."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{pdf_path.stem}.json"
    cache = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file.exists() else {}
 
    def save():
        cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
 
    doc = pymupdf.open(pdf_path)
    total = len(doc) if limit is None else min(limit, len(doc))
 
    pending = []  # páginas escaneadas que ainda precisam de visão
    for i in range(total):
        if str(i + 1) in cache:
            continue
        text = doc[i].get_text().strip()
        if len(text) >= MIN_TEXT_CHARS:
            cache[str(i + 1)] = text  # tinha texto selecionável: sem custo
        else:
            pending.append(i)
    save()
 
    skipped, quota_hit = [], False
    if not vision:
        skipped = [i + 1 for i in pending]
    else:
        for start in range(0, len(pending), PAGES_PER_REQUEST):
            group = pending[start : start + PAGES_PER_REQUEST]
            print(f"  [{pdf_path.name}] transcrevendo páginas {[i + 1 for i in group]} "
                  f"({start + len(group)}/{len(pending)} escaneadas)...")
            try:
                result = transcribe_pages(doc, group)
            except DailyQuotaExceeded:
                quota_hit = True
                skipped += [i + 1 for i in pending[start:]]
                break
            except Exception as e:
                if not (is_temporary(e) or is_not_found(e)):
                    raise  # erro real (ex.: chave inválida): pare e mostre
                print("  ⚠ grupo pulado (Google instável). Rode de novo depois.")
                skipped += [i + 1 for i in group]
                continue
            for i in group:
                if i + 1 in result:
                    cache[str(i + 1)] = result[i + 1]
                else:
                    skipped.append(i + 1)
            save()
            time.sleep(REQUEST_DELAY)
 
    pages = {int(k): v for k, v in cache.items() if int(k) <= total}
    return pages, skipped, quota_hit
 
 
def build_index(chunks: list) -> None:
    """Gera embeddings em lotes (respeitando o limite gratuito) e salva o FAISS."""
    embeddings = get_embeddings()
    store = None
    for i in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[i : i + EMBED_BATCH]
        print(f"  embeddings {min(i + EMBED_BATCH, len(chunks))}/{len(chunks)}")
        if store is None:
            store = with_retry(lambda: FAISS.from_documents(batch, embeddings))
        else:
            with_retry(lambda: store.add_documents(batch))
        time.sleep(EMBED_DELAY)
    store.save_local(str(INDEX_DIR))
 
 
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None,
                        help="processa só as N primeiras páginas de cada PDF (para testar)")
    args = parser.parse_args()
 
    pdfs = sorted(BOOKS_DIR.glob("*.pdf"))
    if not pdfs:
        raise SystemExit("Coloque seus PDFs na pasta books/ e rode novamente.")
 
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
 
    chunks, all_skipped, quota_hit = [], {}, False
    for pdf in pdfs:
        print(f"Lendo {pdf.name}...")
        label = book_label(pdf)
        pages, skipped, hit = extract_pages(pdf, args.limit, vision=not quota_hit)
        quota_hit = quota_hit or hit
        if skipped:
            all_skipped[pdf.name] = skipped
        for page_number, text in pages.items():
            if not text.strip():
                continue
            page_doc = Document(
                page_content=text,
                metadata={"book": label, "page": page_number, "source": pdf.name},
            )
            chunks.extend(splitter.split_documents([page_doc]))
 
    if not chunks:
        raise SystemExit("Nenhuma página foi transcrita ainda. Tente novamente mais tarde.")
 
    print(f"Gerando embeddings de {len(chunks)} chunks...")
    build_index(chunks)
    print("Índice salvo. Para conversar, rode: streamlit run app.py")
 
    if quota_hit:
        print("\n⏸ A cota diária gratuita do Gemini acabou.")
    for name, pages in all_skipped.items():
        print(f"⚠ {name}: {len(pages)} página(s) ainda sem transcrição.")
    if all_skipped:
        print("Rode `python ingest.py` de novo (amanhã, se a cota acabou) para completar.")
 
 
if __name__ == "__main__":
    main()
 