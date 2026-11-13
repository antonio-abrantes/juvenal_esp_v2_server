"""
Verificador de intenção de encerramento via LLM.

Quando o usuário diz algo que pode ser uma despedida/encerramento,
este módulo consulta o LLM com um prompt classificador e interpreta
a resposta: "Sim" → encerrar conversa | "Não" → continuar.

Fluxo:
  1. conversation_loop detecta candidato a despedida (_is_dismissal_candidate)
  2. Chama confirm_dismissal_with_llm(user_text, llm_client)
  3. LLM responde "Sim" ou "Não"
  4. Retorna bool → conversation_loop decide encerrar ou continuar
"""

import logging
import re

logger = logging.getLogger("dismissal-checker")

# ─── Palavras-gatilho que ativam a verificação via LLM ───────────────────────
# Mantidas simples e intencionais — sem palavras ambíguas como "para"
DISMISSAL_TRIGGERS = {
    "encerrar", "encerra",
    "pode ir", "pode sair", "pode parar", "pode descansar",
    "tchau", "até mais", "até logo", "boa noite",
    "obrigado", "obrigada", "obrigado por enquanto",
    "é só isso", "só isso", "nada mais",
    "valeu", "finalizar",
    "para", "pare", "parar", "para aí",
    "desliga", "desligar",
    "sai", "saia", "chega",
}

DISMISSAL_CLASSIFIER_PROMPT = (
    "Analise a frase abaixo e responda APENAS com 'Sim' ou 'Não'.\n"
    "Pergunta: O usuário está pedindo para encerrar, parar ou dispensar o assistente?\n"
    "Frase: \"{phrase}\"\n"
    "Resposta:"
)


def _is_dismissal_candidate(text: str) -> bool:
    """
    Triagem rápida por palavras-gatilho antes de consultar o LLM.
    Evita chamar o LLM para toda frase da conversa.

    Frases de 1 palavra-gatilho só ativam se o enunciado tiver ≤ 5 tokens,
    evitando falsos positivos em frases longas que contenham a palavra por acaso.
    """
    normalized = text.lower().strip().rstrip(".!?,")
    tokens = normalized.split()

    # Correspondência exata com qualquer trigger
    if normalized in DISMISSAL_TRIGGERS:
        return True

    for trigger in DISMISSAL_TRIGGERS:
        trigger_tokens = trigger.split()
        # Multi-palavra: verifica se aparece como subsequência contígua
        if len(trigger_tokens) > 1:
            if trigger in normalized:
                return True
        else:
            # Palavra única: só ativa se o enunciado for curto (≤ 5 tokens)
            if len(tokens) <= 5:
                pattern = r'\b' + re.escape(trigger) + r'\b'
                if re.search(pattern, normalized):
                    return True

    return False


async def confirm_dismissal_with_llm(user_text: str, llm_client) -> bool:
    """
    Consulta o LLM para confirmar se o usuário quer encerrar a conversa.

    Envia um prompt classificador simples e interpreta a resposta:
      - Começa com "sim" → True (encerrar)
      - Qualquer outra coisa → False (continuar)

    Args:
        user_text: Frase dita pelo usuário
        llm_client: Instância de LLMClient já inicializada

    Returns:
        True se o LLM confirmar intenção de encerramento, False caso contrário.
    """
    prompt = DISMISSAL_CLASSIFIER_PROMPT.format(phrase=user_text)

    try:
        response = await llm_client.chat_raw(prompt)
        answer = response.strip().lower()
        logger.info("Classificação de encerramento concluída")
        return answer.startswith("sim")
    except Exception:
        logger.warning("Classificador indisponível; conversa não encerrada pela classificação")
        return False
