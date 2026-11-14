# Integração com Home Assistant

**Etapa concluída em 14/11/2026**, por aceite do usuário após relatar funcionamento da integração. O encerramento inclui os dois modos, comandos sem cadastro obrigatório, logs e ajustes de persona; não equivale à comprovação individual de todos os cenários abaixo.

O Python recebe o comando reconhecido no ESP e o encaminha pela ferramenta estruturada à IA do Home Assistant. Por padrão não é necessário cadastrar ações no Juvenal. O catálogo anterior permanece opcional. O token do Home Assistant permanece no servidor. O firmware atual não precisa ser alterado ou regravado para alternar os modos.

## Configuração

Edite o arquivo privado `.env` na raiz deste servidor e reinicie o Python. Não publique esse arquivo.

| Variável | Padrão / finalidade |
|---|---|
| `HASS_ENABLED` | `true`. `false` desativa todo o módulo; a instrução do agente informa a desativação para pedidos de automação |
| `HASS_MODE` | `api` (padrão) ou `text_endpoint` |
| `HASS_API_ENDPOINT` | URL raiz do HA, como `http://IP_DO_HOME_ASSISTANT:8123`. Também aceita a rota completa terminada em `/api/conversation/process`, sem duplicar o caminho |
| `HASS_API_TOKEN` | Token Bearer do Home Assistant; usado exclusivamente no modo API |
| `HASS_AGENT_ID` | `conversation.extended_openai_conversation_2`; ajustar à entidade real do agente |
| `HASS_REQUEST_TIMEOUT_SECONDS` | `30`; prazo por solicitação, entre 0,1 e 120 s, limitado também pelo turno ativo |
| `HASS_REQUIRE_CATALOG` | `false`: IA do HA interpreta o comando diretamente; `true` exige cadastro/permissões no catálogo do Juvenal |
| `HASS_LOG_CONTENT` | `false` no exemplo; `true` registra payload e corpo da API com limite/ocultação de segredos |
| `HASS_TEXT_ENDPOINT_URL` | Receptor de comandos no modo alternativo |
| `HASS_TEXT_ENDPOINT_FIELD` | `mensagem`; nome do campo JSON esperado pelo receptor |
| `HASS_TEXT_ENDPOINT_API_KEY` | Credencial opcional própria do receptor; nunca reutiliza automaticamente o token HA |
| `HASS_TEXT_ENDPOINT_AUTH_HEADER` | Nome do header exigido pelo receptor, quando há chave |
| `HASS_TEXT_ENDPOINT_AUTH_PREFIX` | Prefixo exato; por exemplo, `"Bearer "` com espaço, somente se o receptor exigir |

## Modo padrão: API e fala no ESP

~~~dotenv
HASS_ENABLED=true
HASS_MODE=api
HASS_API_ENDPOINT=http://IP_DO_HOME_ASSISTANT:8123
HASS_API_TOKEN=PREENCHA_APENAS_NO_ENV_PRIVADO
HASS_AGENT_ID=conversation.extended_openai_conversation_2
HASS_REQUEST_TIMEOUT_SECONDS=30
~~~

O servidor faz POST em `/api/conversation/process` com `Authorization: Bearer …`, `agent_id`, `language: pt-BR` e `text`. O texto usa as instruções solicitadas de execução direta, estado atual, resposta curta e relato de falha; o comando interpolado preserva o pedido residencial do usuário. A IA do HA resolve entidades e ações. A IA do Juvenal seleciona a ferramenta estruturada para esse pedido, sem transformar conversa geral em ação. Com HASS_REQUIRE_CATALOG=true, o comando vem da ação/entidade/parâmetros cadastrados.

A fala vem de `response.speech.plain.speech`. O texto é preservado, sem uma nova chamada ao LLM para reescrevê-lo. Em uma sessão ESP v1, Piper gera PCM16 mono 16 kHz e o ESP reproduz no MAX98357A, com a boca/volume e ACK já existentes. Essa resposta de ação vai ao ESP mesmo se a saída geral estiver em `endpoint`; as demais respostas mantêm `AUDIO_OUTPUT_MODE`. Exige playback habilitado, Piper e modelo de voz disponíveis. Sem essas capacidades, registra falha de entrega e não muda de destino.

Para entradas do painel/host sem conexão ESP, permanece o destino geral configurado; não há um ESP remoto escolhido implicitamente.

`response_type=error` e falhas em `data.failed` são resultados de erro, mas a fala devolvida pelo HA continua sendo a resposta ouvida. `action_done` com sucessos e sem falhas confirma o resultado informado pelo HA; HTTP 200 isolado não confirma execução. JSON inválido, fala ausente, formato somente SSML ou corpo acima de 64 KiB geram mensagem de resultado não confirmado, sem repetição. A fala tem o limite `RESPONSE_MAX_BYTES`.

O módulo não solicita TTS ao HA nem aciona seus alto-falantes nesse modo. Automações independentes existentes no próprio HA continuam sob responsabilidade da sua configuração.

## Alternativa: endpoint executa e fala remotamente

~~~dotenv
HASS_ENABLED=true
HASS_MODE=text_endpoint
HASS_TEXT_ENDPOINT_URL=https://SEU_RECEPTOR/webhook/COMANDOS
HASS_TEXT_ENDPOINT_FIELD=mensagem
~~~

O corpo padrão é `{"mensagem": "comando residencial do usuário"}`. Se o workflow espera `texto` ou `comando`, altere apenas `HASS_TEXT_ENDPOINT_FIELD`. A autenticação desse receptor é configurada nos campos `HASS_TEXT_ENDPOINT_AUTH_*` e `HASS_TEXT_ENDPOINT_API_KEY`.

Após iniciar o POST, a resposta dessa ação não gera áudio no ESP nem no servidor Juvenal, mesmo com `AUDIO_OUTPUT_MODE=esp32`. Também não é reenviada a `AUDIO_TEXT_ENDPOINT_URL`. O receptor recebe o comando e assume execução/áudio remoto. O corpo HTTP não é interpretado como fala nem entregue a outro LLM.

HTTP 2xx significa pedido aceito, sem comprovar execução ou que o áudio remoto terminou. Falha/timeout é registrada como resultado incerto; não há fala duplicada, fallback para API ou nova tentativa. O painel recebe o resultado do turno. Antes de qualquer envio, recusas por identidade não autorizada ainda podem ser informadas pelo Juvenal. Pedidos de confirmação por catálogo só existem se HASS_REQUIRE_CATALOG=true; não são uma resposta de execução delegada.

A continuidade WS e o timeout de inatividade continuam controlados pelo Python. Como este receptor não fornece ACK de término do áudio remoto, a escuta pode ser retomada depois do aceite HTTP; não há espera fixa inventada para estimar o fim da fala. Conferir possível captação do alto-falante externo pelo microfone do ESP na montagem real.

## Habilitação sem cadastro de ações

HASS_ENABLED=true habilita o módulo e HASS_REQUIRE_CATALOG=false dispensa o cadastro de ações. A IA do Home Assistant recebe o comando e faz a interpretação; não há lista de entidades obrigatória no Juvenal nem confirmação adicional de catálogo nesse modo. Identidade autenticada do ESP, módulo habilitado, limites de chamadas, tentativa única e deduplicação permanecem.

Isso não impede esclarecimento de uma transcrição ambígua: se houver dúvida sobre ação, negação, dispositivo ou valor, o Juvenal pergunta de forma breve antes de chamar a ferramenta. Quando o pedido está claro, executa sem confirmação adicional. A persona usa contexto para corrigir erros óbvios de fala, sem inventar comandos.

O transporte estruturado usa TOOLS_ENABLED=true, LLM_TOOL_CALLING_CONFIRMED=true e LLM_TOOL_CALLING_PROVIDER/LLM_TOOL_CALLING_MODEL correspondentes ao provedor/modelo ativo. Esses campos foram habilitados no .env local por solicitação do usuário; isso permite a avaliação manual e não comprova compatibilidade do provedor. O adaptador é openai-chat-completions. Webhook conversacional não oferece esta ferramenta.

Para voltar a exigir catálogo no futuro, configure HASS_REQUIRE_CATALOG=true e reinicie. Somente nesse caso, cadastre ações habilitadas no painel e conceda permissão à identidade esp:ID_DO_DISPOSITIVO; confirmation=never executa direto e always exige confirmação. O catálogo e o painel existentes não foram apagados.

HASS_ENABLED=false impede a oferta da ferramenta e inclui instrução explícita para o agente informar a desativação. /health, em capabilities.tools, mostra disponível/motivo, hass_enabled, hass_mode e catalog_required, sem URL/token. Sem exigir catálogo, catalog_valid é null porque o arquivo não é consultado.

## Migração e separação dos endpoints

- `HASS_*`: comandos residenciais e destino específico da resposta de ação.
- `AUDIO_TEXT_ENDPOINT_URL`: entrega da resposta comum da IA no modo geral `endpoint`.
- `WEBHOOK_URL`: provedor conversacional alternativo, quando selecionado.
- `HA_AUTOMATION_*` e o antigo `text-command-v1`: configuração anterior substituída pelos dois modos acima. Não são mais lidos para executar ações. Não é necessário confirmar um contrato fictício de resposta com request_id para o novo endpoint textual.

O adaptador faz uma tentativa por chamada residencial, sem redirects, retries ou troca automática de modo. Resultado incerto não autoriza repetir. Comandos textualmente iguais, ou ações iguais do catálogo opcional, são deduplicados no mesmo turno; não é uma garantia de idempotência entre pedidos separados.

## Logs dos POSTs

Os metadados são registrados sempre: turno, request_id, modo, início do POST, status HTTP, duração e resultado (sucesso, erro, timeout ou incerteza). Falhas de transporte mostram categoria, sem imprimir a exceção que pode conter URL/credenciais. Se configuração ou catálogo opcional bloquear a ferramenta, aparece o motivo e a indicação de que nenhum POST foi enviado.

HASS_LOG_CONTENT=true acrescenta o payload enviado e uma prévia do corpo recebido da API, inclusive em erro HTTP. O limite por conteúdo é 4096 caracteres; segredos conhecidos são ocultados e controles são escapados. Headers de autenticação e URL privada não são registrados. No modo textual, o corpo 2xx continua ignorado: registrar aceite não comprova execução ou áudio remoto. Textos/entidades podem ser privados; os logs locais não devem ser publicados sem revisão.

A habilitação de ferramentas requer TOOLS_ENABLED=true e configuração estruturada correspondente ao provedor/modelo em uso. HASS_ENABLED=true sozinho não substitui essas chaves. Depois de editar o .env, reinicie o Python. Com HASS_REQUIRE_CATALOG=false não é necessário salvar o catálogo.

## Verificações complementares

O usuário informou que a integração funcionou e solicitou o encerramento da etapa. Sem detalhamento por cenário, permanecem para conferência específica, sem reabrir automaticamente a entrega:

- API: uma ação autorizada, entidade já no estado solicitado, entidade não reconhecida e erro retornado pelo HA; comparar fala com o texto original devolvido.
- API em sessão ESP: resposta audível com boca/volume/ACK e continuação sem nova wake. Conferir também quando a saída geral for endpoint.
- Endpoint: confirmar formato do POST, execução/fala remota e ausência de áudio no ESP; corpo de retorno não deve provocar outro POST.
- Alternância: mudar somente HASS_MODE no servidor e reiniciar. Desativação: HASS_ENABLED=false deve impedir POST e informar o motivo.
- Sem catálogo salvo: comando deve ser enviado com HASS_REQUIRE_CATALOG=false. Catálogo/permissões/confirmacão somente no modo opcional true. Conferir identidade inválida, HTTP 401/500, timeout e queda de conexão: sem sucesso inventado ou reenvio.

Implementação e documentação encerradas por aceite do usuário, com revisão estática pelo agente. Nenhum servidor foi iniciado, chamada HA/dispositivo efetuada ou teste funcional executado pelo agente.

Contrato oficial consultado: [Conversation API](https://contributors.home-assistant.io/core/internals/conversation-api/) e [Process conversation](https://www.home-assistant.io/actions/conversation.process/).
