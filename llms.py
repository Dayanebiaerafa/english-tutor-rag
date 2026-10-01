"""Fábrica de modelos: troca Gemini <-> OpenAI e lida com erros e limites do plano gratuito."""
import time
 
from config import (
    GEMINI_EMBED_MODELS, GEMINI_LLM_MODELS, OPENAI_EMBED_MODEL, OPENAI_LLM_MODEL, PROVIDER,
)
 
TEMPORARY = ("429", "resource_exhausted", "rate limit", "503", "unavailable", "high demand")
NOT_FOUND = ("404", "not found", "no longer available")
 
_exhausted: set[str] = set()  # modelos cuja cota diária já acabou nesta execução
 
 
class DailyQuotaExceeded(Exception):
    """A cota diária gratuita acabou em todos os modelos disponíveis."""
 
 
def is_temporary(error) -> bool:
    """Limite atingido (429) ou servidor do Google ocupado (503)."""
    return any(w in str(error).lower() for w in TEMPORARY)
 
 
def is_daily_quota(error) -> bool:
    """Cota DIÁRIA esgotada: esperar alguns segundos não adianta, só amanhã."""
    return "perday" in str(error).lower().replace("_", "").replace(" ", "")
 
 
def is_not_found(error) -> bool:
    """Modelo inexistente ou fora do ar para a sua conta (404)."""
    return any(w in str(error).lower() for w in NOT_FOUND)
 
 
def get_llm(temperature: float = 0.0, model: str | None = None):
    if PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI  # usa GOOGLE_API_KEY
        return ChatGoogleGenerativeAI(model=model or GEMINI_LLM_MODELS[0], temperature=temperature)
    from langchain_openai import ChatOpenAI  # usa OPENAI_API_KEY
    return ChatOpenAI(model=model or OPENAI_LLM_MODEL, temperature=temperature)
 
 
def get_embeddings():
    if PROVIDER == "gemini":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        for name in GEMINI_EMBED_MODELS:
            embeddings = GoogleGenerativeAIEmbeddings(model=name)
            try:  # teste rápido: o modelo está disponível para esta conta?
                with_retry(lambda: embeddings.embed_query("teste"))
                print(f"Modelo de embeddings: {name}")
                return embeddings
            except Exception as e:
                if is_not_found(e):
                    print(f"Modelo {name} indisponível, tentando o próximo...")
                    continue
                raise
        raise RuntimeError("Nenhum modelo de embeddings disponível. Veja config.py.")
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model=OPENAI_EMBED_MODEL)
 
 
def text_of(response) -> str:
    """Extrai o texto da resposta (alguns modelos devolvem uma lista de partes)."""
    content = response.content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in content)
    return content
 
 
def with_retry(fn, tries: int = 6, wait: int = 20):
    """Repete a chamada em erros temporários (429 por minuto ou 503).
    Cota diária esgotada NÃO é repetida: não adianta esperar."""
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:
            if is_daily_quota(e) or not is_temporary(e) or attempt == tries - 1:
                raise
            delay = wait * (attempt + 1)
            print(f"  erro temporário do Google; aguardando {delay}s e tentando de novo...")
            time.sleep(delay)
 
 
def invoke(prompt, temperature: float = 0.0) -> str:
    """Chama o LLM e devolve o texto. Se o modelo estiver sobrecarregado, indisponível
    ou sem cota diária, passa automaticamente para o próximo da lista do config.py."""
    models = GEMINI_LLM_MODELS if PROVIDER == "gemini" else [OPENAI_LLM_MODEL]
    last_error = None
    for name in models:
        if name in _exhausted:
            continue
        llm = get_llm(temperature, name)
        try:
            return text_of(with_retry(lambda: llm.invoke(prompt), tries=3, wait=10))
        except Exception as e:
            if not (is_temporary(e) or is_not_found(e)):
                raise  # erro de verdade (chave inválida etc.): não adianta trocar de modelo
            if is_daily_quota(e):
                _exhausted.add(name)
                print(f"  cota diária de {name} esgotada; tentando outro modelo...")
            else:
                print(f"  modelo {name} indisponível no momento; tentando outro...")
            last_error = e
    if all(m in _exhausted for m in models):
        raise DailyQuotaExceeded("Cota diária gratuita esgotada em todos os modelos.") from last_error
    raise last_error
 