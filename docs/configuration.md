# Configuração do Juvenal Server — fases 01 e 02

A configuração é carregada uma vez por server/config.py, a partir do .env da raiz do projeto. Variáveis do processo prevalecem. Caminhos relativos partem da raiz, sem depender do diretório de chamada; o antigo ../models/juvehnal.tflite é compatibilizado para models/juvehnal.tflite.

A fase 01 preservou a configuração existente; ajustes posteriores autorizados definiram a saída textual. A fase 02 acrescentou somente defaults novos desativados, preservando provedor, credenciais, áudio e destino de saída. O .env real permanece local e ignorado.

## Saída global e migração

| Configuração | Resultado |
|---|---|
| AUDIO_OUTPUT_MODE=local | TTS/player local, sem POST textual ou retorno PCM ESP |
| AUDIO_OUTPUT_MODE=endpoint | Apenas texto HTTP; não importa/carrega Piper, pyttsx3 ou player |
| AUDIO_OUTPUT_MODE=esp32 | Retorna indisponibilidade de playback, sem síntese/fallback; fase 03 pendente |
| Modo ausente e TTS_HA_WEBHOOK_URL configurada | Compatibilidade temporária: entrada ESP entrega texto ao webhook legado, outras origens continuam locais. Aviso sem URL/segredos |
| Modo ausente e sem variável legada | local |

Um modo explícito prevalece sobre o alias antigo. Para migrar o caminho ESP legado, confirmar contrato do receptor, definir AUDIO_OUTPUT_MODE=endpoint e AUDIO_TEXT_ENDPOINT_URL, sem alterar o webhook WAV do firmware experimental. O modo global passa a valer para todas as origens, despedida e mensagem de espera. Não há seletor de saída no painel.

LOCAL_VOICE_ENABLED é true por compatibilidade; desligar quando não houver microfone/wake no host. STT_ENABLED é true; desligar se a entrada for somente textual. AUDIO_INPUT_DEVICE e AUDIO_OUTPUT_DEVICE mantêm índice numérico ou dispositivo padrão quando vazios/não numéricos, sem significado de IP/identidade ESP. O output local preserva a tentativa de dispositivo padrão caso o selecionado falhe; isso nunca muda o destino para endpoint/ESP.

## Contrato da entrega HTTP

O receptor **ainda não foi conferido nesta implementação**. O formato conhecido é o do emissor antigo, não prova de contrato do serviço remoto. Os controles abaixo permitem configurá-lo explicitamente; confirme antes de usar uma URL real.

| Variável | Regra |
|---|---|
| AUDIO_TEXT_ENDPOINT_URL | HTTP(S), obrigatória no modo endpoint, sem usuário/senha embutidos |
| AUDIO_TEXT_ENDPOINT_FORMAT | json (padrão): corpo {"texto": "resposta"}; text: bytes UTF-8 com Content-Type text/plain; charset=utf-8 |
| AUDIO_TEXT_ENDPOINT_TIMEOUT_SECONDS | 10 s inicial; positivo, máximo 60 s; também respeita o prazo restante da interação |
| AUDIO_TEXT_ENDPOINT_SUCCESS_STATUSES | 200,201,202 como proposta inicial configurável; somente 2xx |
| AUDIO_TEXT_ENDPOINT_API_KEY | Opcional; não enviada sem header explicitamente configurado |
| AUDIO_TEXT_ENDPOINT_AUTH_HEADER | Nome exato requerido pelo receptor; obrigatório se API Key for fornecida |
| AUDIO_TEXT_ENDPOINT_AUTH_PREFIX | Prefixo literal, vazio por padrão. Se o contrato exigir Bearer, configurar "Bearer " preservando o espaço |

Não copiar configuração da ferramenta HA ou do provedor conversacional. Não há retry, redirect, áudio, anexos, fallback ou segundo POST após falha. Aceite HTTP não prova fala. Timeout/conexão interrompida podem significar resultado desconhecido: não reenviar automaticamente.

### Logs solicitados para diagnóstico

`LOG_TRANSCRIPTS=true` exibe o texto reconhecido no terminal antes da chamada à IA. `AUDIO_TEXT_ENDPOINT_LOG_RESPONSE=true` exibe uma prévia de até **4096 bytes** do corpo retornado pelo endpoint, junto ao status HTTP e ID do turno. Ambas as opções são false por padrão; foram habilitadas no `.env` local por pedido expresso do usuário em 13/11/2026. Alterar exige reinício.

O corpo do receptor é apenas texto de diagnóstico: não é interpretado como resposta do agente, não entra no histórico e não gera outro TTS, tool ou POST. Leitura/log incompletos após receber o status não invalidam o resultado HTTP já observado. Mensagens de log escapam quebras/controles e ocultam segredos conhecidos. Para desligar conteúdo no terminal, definir essas duas opções como false, sem alterar o funcionamento da entrega.

## Voz e modelos

Whisper mantém WHISPER_MODEL_SIZE, LANGUAGE, DEVICE, COMPUTE_TYPE, BEAM_SIZE, TEMPERATURE, NO_SPEECH_THRESHOLD e CONDITION_ON_PREVIOUS_TEXT com o prefixo WHISPER_ em cada variável.

Wake local mantém WAKE_WORD_MODEL e WAKE_WORD_THRESHOLD. O microfone é liberado antes da captura Whisper e retomado após liberar capacidade. O modelo validado não foi alterado. Modelos ausentes não são instalados pelo instalador; a biblioteca pode baixá-los na primeira inicialização da capacidade correspondente.

Piper mantém PIPER_MODEL, PIPER_LENGTH_SCALE, PIPER_VOLUME e PIPER_USE_CUDA. Procura em models/ e no cache Piper do usuário; caminho ONNX explícito é aceito. Chunks de síntese têm taxa/canais/formato nativos, separados do player. A conversão/framing PCM WS é da fase 03.

TTS_BACKEND=pyttsx3 continua opcional e explícito; exige instalar pyttsx3 separadamente. Não seleciona AUDIO_OUTPUT_DEVICE e falha de forma controlada se um índice local for pedido. Não existe fallback automático de Piper para outro backend/destino.

## Provedores e prompt

Preservados ollama, openai, groq, custom e webhook. Nomes de configuração permanecem no exemplo. O webhook conversacional usa {"message": "..."} e espera {"response": "..."}, com WEBHOOK_API_KEY em X-API-Key; não é saída textual nem ferramenta de automação.

SYSTEM_PROMPT_PATH aponta por padrão para configs/system_prompt.md. O classificador de despedida faz requisição isolada sem alterar histórico. O agente recebe o histórico do contexto correspondente; o singleton do provedor não armazena mais conversa global.

## Limites e contexto

| Variável | Inicial |
|---|---|
| CONTEXT_MAX_MESSAGES / CONTEXT_MAX_BYTES | 20 / 65536 bytes serializados por contexto; remoção de turnos completos, preservando chamadas e resultados de ferramentas |
| CONTEXT_TTL_SECONDS / CONTEXT_MAX_COUNT | 1800 s / 8 contextos |
| RESPONSE_MAX_BYTES / TOKEN_QUEUE_MAX_BYTES | 32768 / 65536 bytes; fila também limitada a 128 tokens |
| LLM_TIMEOUT_SECONDS | 60 s |
| INTERACTION_TIMEOUT_SECONDS | 180 s absolutos, incluindo captura/processamento/entrega |
| DELIVERY_TIMEOUT_SECONDS | 60 s para reprodução local |
| CONVERSATION_TIMEOUT / ESP_SILENCE_TIMEOUT | 15 s para início de fala / 1,8 s de intervalo no protocolo ESP legado |

Contextos são voláteis e não sobrevivem a restart. A identidade local é separada da identidade do operador web. O operador autenticado/loopback mantém contexto web entre reconexões; ESP legado usa identidade autenticada ou contexto único da conexão sem credencial. Nunca confiar em device_id arbitrário do cliente.

Limpar histórico exige permissão de controle e ausência de interação/worker ocupado. O painel usa o contexto da última ação naquela conexão; por padrão local, ou web quando voz local está desligada. O backend aceita context=local|web para seleção explícita. Não permite apagar história de outro dispositivo pelo nome.

Erro/timeout não entrega texto parcial como sucesso. A espera pode ser cancelada sem interromper imediatamente o trabalho síncrono: a capacidade continua ocupada até ele terminar e saídas tardias são descartadas. Uma ação externa não é desfeita por cancelamento; as ferramentas da fase 02 também respeitam essa ocupação residual e não repetem envios.

## Painel, controles e capacidades

PANEL_USERNAME e PANEL_PASSWORD, definidos juntos, habilitam Basic Auth em / e /ws. Sem eles, o monitor permanece acessível na rede, mas controles de escrita só são permitidos por conexão loopback. Navegador de outra origem não pode operar controles. Controles antigos continuam disponíveis em loopback sem credenciais; a administração do catálogo exige credenciais em qualquer origem; não exponha o painel aberto ou Basic HTTP na Internet. Proteção HTTPS/rede efetiva deve ser alinhada à implantação.

O monitor mantém eventos existentes e mostra busy/erros/resultado de entrega. Não foi acrescentado formulário de chat. Para solicitar texto manualmente, o operador desenvolvedor pode usar o console da página do painel:

~~~javascript
ws.send(JSON.stringify({action: "send_text", text: "Olá, Juvenal"}))
~~~

Isso é uma ação manual opcional do usuário, não procedimento executado pelo agente. Não funciona como bypass de autenticação, origem ou admissão.

ESP_DEVICE_TOKEN, se definido, exige Authorization: Bearer com a credencial no handshake legado; ESP_DEVICE_ID é a identidade vinculada. Sem token, não há retomada de contexto entre conexões de ESP. /ws/esp continua legado e não atende protocol_version v1. Frames pares de até 4096 bytes, fila até 64 KiB e áudio até 30 s/960000 bytes; atingir limite descarta a interação, sem executar fala truncada. O intervalo de pacotes do legado não é VAD; VAD/WS por sessão/ack de playback são da fase 03.

JUVENAL_HOST/JUVENAL_PORT preservam 0.0.0.0:8000 como padrão. /health informa modo, compatibilidade, busy e capacidades sem URLs, chaves ou áudio. JUVENAL_DATA_DIR define a localização do catálogo Markdown persistente da fase 02, criado no primeiro salvamento pelo administrador; não há banco de dados adicional. Não copiar configs/segredos para esse diretório por rotina.

## Avaliação manual pendente

Quando decidir iniciar, o usuário pode conferir conversa local/dispositivos, despedida e idle; endpoint textual único sem TTS; falha sem retry/fallback; contexto por origem/TTL/limites; concorrência e retorno após erro; modo esp32 indisponível; caminhos de scripts e configuração preservada.

O agente não executou cenários funcionais nem criou testes automatizados. O usuário comprovou o fluxo local/IA/entrega textual JSON com HTTP 200 na fase 01; a configuração final do receptor, ferramentas e compatibilidade Linux ainda precisam de avaliação específica.

## Ferramentas e catálogo — fase 02

O painel tem o botão **Automações** para cadastrar, editar, habilitar/desabilitar e remover entradas. Configure `PANEL_USERNAME` e `PANEL_PASSWORD` juntos no `.env`, reinicie e autentique-se ao abrir o painel. A API de catálogo exige essas credenciais mesmo em loopback; o token ESP não dá acesso administrativo. O navegador usa a sessão Basic existente, sem guardar chaves em URL ou armazenamento local. Escritas exigem mesma origem e revisão do catálogo.

A conversa e a entrega textual continuam disponíveis com ferramentas desativadas. O modo de saída permanece no `.env`, sem seletor no catálogo.

| Variável | Uso / padrão |
|---|---|
| `TOOLS_ENABLED` | `false`; habilitação geral de ferramentas |
| `LLM_TOOL_CALLING_CONFIRMED` | `false`; confirmação deliberada de suporte estruturado |
| `LLM_TOOL_CALLING_PROVIDER` / `LLM_TOOL_CALLING_MODEL` | Devem corresponder exatamente ao provedor/modelo em uso |
| `LLM_TOOL_CALLING_ADAPTER` | `openai-chat-completions`; nenhum suporte presumido apenas pela URL |
| `HA_AUTOMATION_URL` | Receptor de comandos textuais; vazio, independente de LLM e saída |
| `HA_AUTOMATION_API_KEY` / `AUTH_HEADER` / `AUTH_PREFIX` | Usar o prefixo completo `HA_AUTOMATION_` nas três variáveis; só enviar a credencial quando configurada e exigida pelo contrato |
| `HA_AUTOMATION_CONTRACT` / `HA_AUTOMATION_CONTRACT_CONFIRMED` | Vazio / `false`; adaptador proposto `text-command-v1`, ainda sem comprovação do workflow real |
| `TOOL_TIMEOUT_SECONDS` | 15 s, teto de 15 s por chamada |
| `TOOL_CONFIRMATION_TTL_SECONDS` | 60 s; máximo 300 s |
| `TOOL_MAX_ROUNDS` / `TOOL_MAX_CALLS` | Até 3 rodadas com ferramentas e 4 chamadas por turno; envio serial |
| `AUTOMATION_CATALOG_MAX_BYTES` / `MAX_ENTRIES` | Usar `AUTOMATION_CATALOG_` nas duas variáveis; 262144 bytes / 200 entradas |

Não habilite as confirmações de capacidade/contrato por tentativa. Webhook conversacional não fornece chamadas Python estruturadas; sua conversa permanece disponível, com a limitação informada na instrução enviada ao provedor. Provedores OpenAI-compatible só recebem ferramentas se a combinação confirmada corresponder à configuração ativa. Não há extração de comando a partir de texto livre ou JSON aparente de uma resposta normal.

O adaptador `text-command-v1` é uma implementação opt-in da proposta textual do projeto, não uma confirmação de compatibilidade com o workflow atual. Antes de ativar, alinhe os campos de comando, autenticação e resposta com esse workflow. Não envie esse formato ao webhook WAV sem adaptar e confirmar uma entrada textual separada.

### Persistência e campos do catálogo

O painel gera `JUVENAL_DATA_DIR/automacoes.md` como Markdown com título fixo e um único bloco JSON contendo `schema_version: 1` e `automations`. Edição manual precisa preservar o formato gerado. IDs duplicados, campos desconhecidos, políticas inválidas ou ações não cadastradas bloqueiam a atualização.

Cada entrada contém ID, título, descrição, habilitação, exemplos/sinônimos, ação, parâmetros, identidades permitidas, confirmação e orientação de resultado. Ações disponíveis: `light.turn_on/off`, `switch.turn_on/off`, `scene.turn_on`, `script.turn_on` e `automation.trigger`. O catálogo não cria novos tipos de ação nem contém URL de execução, código ou credenciais.

`entity_id` exige uma lista explícita de entidades do domínio da ação. Brilho e transição só existem nas ações apropriadas, com limites numéricos restritos. As identidades aceitas são `local`, `web:operator` e `esp:ID`; ESP só pode executar com sua identidade autenticada configurada. Cadastro não executa ação e nenhuma automação real vem habilitada.

Cada salvamento valida o documento, verifica a revisão, grava um temporário e troca o arquivo atomicamente. A versão anterior fica em `automacoes.md.previous`. O catálogo é relido para oferecer ferramentas e antes de cada execução. Alteração de revisão invalida a proposta anterior; arquivo inválido bloqueia ações, mantendo conversa e administração disponíveis. Sem revisão recuperável, corrija manualmente o arquivo antes de salvar.

Histórico e confirmações permanecem em memória; o catálogo é persistente. Não há fila durável de comandos nem replay após restart.

### Confirmação e resultados

`confirmation=always` solicita confirmação explícita da proposta vinculada à mesma identidade, parâmetros, revisão e prazo. Diga `confirmo`/`pode executar` para executar ou `cancelar` para recusar. Em saída endpoint, faça isso na próxima ativação da wake word. Uma afirmação do LLM nunca confirma a própria ação. Falha/cancelamento na entrega da pergunta remove a proposta.

`never` dispensa somente essa confirmação adicional; habilitação, parâmetros, identidade e revisão continuam obrigatórios.

Estados de resultado: `success`, `denied`, `needs_confirmation`, `failed`, `unknown`. HTTP 2xx sem confirmação estruturada e correlacionada de execução resulta em `unknown`. O corpo bruto do workflow não é entregue ao LLM nem registrado como instrução. Confirmações, falhas e resultados incertos usam mensagem controlada pelo backend; a IA recebe somente resultados estruturados e limitados.

Não há retry, redirecionamento ou fallback de ferramenta. O mesmo pedido no mesmo turno usa o resultado já registrado. Cancelar espera não desfaz um comando enviado; resultados completos ou incertos são preservados no contexto para não transformar interrupção em execução novamente.

### Pontos para avaliação manual

Sem habilitar execução: conferir autenticação do catálogo, cadastro desabilitado, edição, remoção e conflito entre duas páginas. Conferir conversa/entrega textual normais e estado de ferramentas em `/health`.

Depois de confirmar o provedor e o contrato do workflow, o usuário pode avaliar ação permitida, negação de parâmetros/identidade, confirmação e expiração, mudança de catálogo e tratamento de aceite/erro/timeout. Esses cenários não foram executados pelo agente e não incluem autorização para acionar dispositivos automaticamente.
