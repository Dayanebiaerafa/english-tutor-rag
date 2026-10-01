"""Configurações centrais do projeto (caminhos, modelos e parâmetros de RAG)."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # lê as chaves do arquivo .env

# Provedor de IA: "gemini" (tem plano gratuito) ou "openai".
# Para trocar, defina PROVIDER=openai no .env.
PROVIDER = os.getenv("PROVIDER", "gemini").lower()

# Modelos (se algum nome for descontinuado, troque aqui pelo atual do AI Studio)
# Modelos de texto/visão em ordem de preferência: se o primeiro estiver
# sobrecarregado (erro 503) ou indisponível, o programa usa o próximo.
GEMINI_LLM_MODELS = [
    "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
    "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
]
# Embeddings: usa o primeiro que estiver disponível para a sua conta
GEMINI_EMBED_MODELS = ["models/gemini-embedding-2", "models/gemini-embedding-001"]
OPENAI_LLM_MODEL = "gpt-4o-mini"
OPENAI_EMBED_MODEL = "text-embedding-3-small"

# Pastas
BOOKS_DIR = Path("books")          # PDFs de entrada (não vão para o GitHub)
CACHE_DIR = Path("cache")          # texto transcrito por página
INDEX_DIR = Path("faiss_index")    # banco vetorial salvo em disco

# Ingestão
MIN_TEXT_CHARS = 100               # abaixo disso, a página é tratada como imagem
RENDER_DPI = 130                   # resolução usada para enviar a página ao modelo
PAGES_PER_REQUEST = 4              # páginas escaneadas por chamada (poupa a cota diária)
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

# Ritmo das chamadas (evita erro 429 no plano gratuito do Gemini)
REQUEST_DELAY = 6 if PROVIDER == "gemini" else 0   # segundos entre transcrições
EMBED_BATCH = 50                                   # chunks por lote de embeddings
EMBED_DELAY = 5 if PROVIDER == "gemini" else 0     # segundos entre lotes

# Numeração: página impressa no livro = página do PDF + deslocamento
# (medido nos seus PDFs: Student Book = -1, Workbook = -2)
PAGE_OFFSET = {"Student Book": -1, "Workbook": -2}

# Recuperação
TOP_K = 8                          # candidatos da busca semântica
TOP_N = 4                          # trechos que sobram após o re-rank