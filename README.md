# Juvenal Server

Servidor Python do assistente de voz **Juvenal**, desenvolvido por Antônio Abrantes. Centraliza transcrição, conversa com IA, contexto e entrega da resposta, integrando um dispositivo **ESP32-S3** independente do Home Assistant.

Este componente usa repositório próprio: **`juvenal_esp_v2_server`**, associado ao firmware `juvenal_esp_v2`. A pasta local do servidor permanece `juvenal_server`; firmware e servidor têm históricos separados.

## Estado do projeto

**Etapa Home Assistant e persona concluída em 14/11/2026**, por aceite do usuário após informar que a integração passou a funcionar. A entrega inclui dois modos HA, comandos sem cadastro obrigatório, logs de POST e persona breve com interpretação de transcrição imperfeita. O aceite não comprova individualmente todos os modos e cenários de falha; as verificações complementares ficam no [guia HA](docs/home-assistant.md).

O núcleo de conversa e a entrada ESP por WebSocket já têm evidências de STT/IA, playback e continuidade. LEDs de inicialização e boca por amplitude foram aprovados no hardware. A operação Linux/systemd e outras verificações específicas continuam pendentes, separadas desta etapa.

O firmware experimental **Juvenal Visual v0.4**, em juvenal_http_teste, permanece WAV/HTTP POST. O cliente integrado está no principal juvenal_esp, com ESP-IDF **5.4.4**, face, painel e diagnóstico WAV preservados. A integração HA é executada no servidor e não exige nova alteração de firmware.

## Como a conversa funciona

A entrada pode vir do ESP por WS ou do diagnóstico local. No ambiente atual, LOCAL_VOICE_ENABLED=false: wake e microfone ficam no ESP; o Python recebe PCM, executa Whisper/IA e entrega no destino configurado. A entrada local do computador continua opcional.

As integrações têm responsabilidades distintas:

| Configuração | Finalidade |
|---|---|
| `LLM_PROVIDER` e variáveis do provedor | Gerar a resposta da IA; suporta OpenAI, Ollama, Groq, servidor compatível e webhook conversacional |
| `WEBHOOK_URL` / `WEBHOOK_API_KEY` | Provedor alternativo, ativo somente com `LLM_PROVIDER=webhook`; contrato `message` → `response` |
| `AUDIO_TEXT_ENDPOINT_URL` | Receber a resposta final já produzida; ativo com `AUDIO_OUTPUT_MODE=endpoint` |
| Ferramenta Home Assistant | Encaminhar comandos residenciais à IA do HA com configuração `HASS_*`; catálogo opcional, API com fala no ESP ou endpoint com fala remota |

Com OpenAI como provedor e saída `endpoint`, a resposta é gerada pela OpenAI e depois enviada ao webhook. O corpo devolvido por esse webhook pode ser registrado para diagnóstico; não é interpretado como nova resposta da IA, ferramenta ou áudio.

## Modos de saída

| `AUDIO_OUTPUT_MODE` | Comportamento |
|---|---|
| `local` | Sintetiza com Piper e reproduz no dispositivo de áudio do servidor |
| `endpoint` | Entrega a resposta comum por POST; ações HA seguem a política de áudio específica abaixo |
| `esp32` | Piper → PCM16 mono 16 kHz → WS v1; conclui após confirmação do TX pelo ESP. Exige cliente com playback habilitado e montagem apta |

No modo `endpoint`, o formato padrão é JSON `{"texto": "resposta final da IA"}`; há opção de texto puro. Status aceito confirma entrega HTTP conforme a configuração, sem confirmar reprodução de fala. Não há reenvio ou mudança automática de destino após falha.

`LOCAL_VOICE_ENABLED` controla wake/microfone do host, `STT_ENABLED` habilita Whisper e `AUDIO_OUTPUT_DEVICE` escolhe somente a reprodução local. No modo endpoint, o aceite HTTP conclui a entrega do turno, sem presumir que um receptor externo terminou de falar. Na extensão WS v1, o ESP pode retomar a escuta na mesma conexão após essa entrega.

## Instalação e execução

Use **Python 3.11** como referência. Windows já teve execução manual comprovada; scripts e caminhos Linux estão preparados, mas dependências do host e operação com systemd ainda precisam de validação.

Execute os comandos a partir da raiz deste repositório. Em instalação nova, prepare a configuração e um prompt local sem substituir arquivos existentes.

Windows / PowerShell:

~~~powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path configs/system_prompt.md)) {
    Copy-Item configs/system_prompt.example.md configs/system_prompt.md
}
~~~

Linux:

~~~bash
[ -f .env ] || (umask 077; cp .env.example .env)
[ -f configs/system_prompt.md ] || (umask 077; cp configs/system_prompt.example.md configs/system_prompt.md)
~~~

Edite o `.env` local com seu provedor, credenciais e capacidades. Para um diagnóstico com microfone local e entrega de texto:

~~~dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=
AUDIO_OUTPUT_MODE=endpoint
LOCAL_VOICE_ENABLED=true
STT_ENABLED=true
AUDIO_TEXT_ENDPOINT_URL=
AUDIO_TEXT_ENDPOINT_FORMAT=json
AUDIO_TEXT_ENDPOINT_SUCCESS_STATUSES=200,201,202
AUDIO_TEXT_ENDPOINT_TIMEOUT_SECONDS=10
~~~

Preencha os campos vazios com sua chave, modelo disponível e URL do receptor; escolha os dispositivos locais conforme o host. A URL de saída não é a URL do provedor de IA. Autenticação do webhook é configurada separadamente, apenas quando exigida pelo receptor.

Para entrada por wake word, copie o modelo personalizado validado `juvehnal.tflite` para `models/`, conforme [modelos](models/README.md). Pesos não são distribuídos neste repositório. Entrada exclusivamente textual dispensa esse modelo e exige `LOCAL_VOICE_ENABLED=false`, `STT_ENABLED=false`.

Depois da configuração, instale e inicie manualmente:

~~~powershell
.\scripts\install.ps1
.\scripts\start.ps1
~~~

~~~bash
bash scripts/install.sh
bash scripts/start.sh
~~~

O instalador preserva o `.env` existente e não inicia a aplicação ou outros serviços. O perfil `auto` escolhe dependências conforme a configuração; perfis explícitos `text`, `endpoint`, `local` e `esp32` escolhem pacotes e não alteram o `.env`. O perfil `endpoint` é para áudio recebido sem captura local; para wake/microfone no host, use `auto` com as opções acima.

Whisper e Piper podem baixar modelos ao iniciar a capacidade correspondente. A saída endpoint comum dispensa Piper; a API HA com resposta no ESP precisa dele, mesmo quando a saída geral é endpoint. Detalhes: [configuração](docs/configuration.md) e [preparação Linux](docs/homelab-linux-setup.md).

## Alteração do destino

Para enviar a resposta ao webhook, use `AUDIO_OUTPUT_MODE=endpoint` e preencha `AUDIO_TEXT_ENDPOINT_*`. Para ouvir pelo computador, use `AUDIO_OUTPUT_MODE=local`, configure Piper/dispositivo e instale as dependências locais correspondentes. Reinicie o processo após mudar o `.env`.

Para retornar áudio ao ESP, com a montagem já validada (GPIO15/16/17, USB5V/GND comum), use o speaker habilitado no header privado do firmware, defina `AUDIO_OUTPUT_MODE=esp32`, `TTS_BACKEND=piper`, configure `PIPER_MODEL` e instale o perfil `esp32` (Whisper + Piper, sem player do host). Reinicie o servidor e grave o firmware atualizado manualmente. Origem textual/microfone do host sem vínculo WS não consegue receber essa saída e retorna indisponibilidade; não existe fallback. Para voltar ao webhook, restaure `AUDIO_OUTPUT_MODE=endpoint` e reinicie; não precisa habilitar speaker no ESP.

## Painel, configuração e privacidade

- `/`: painel de monitoramento; `/ws`: eventos; `/health`: capacidades.
- O backend aceita entrada textual `send_text`; o painel atual é um monitor, sem formulário de chat.
- Para controles pela LAN, configure `PANEL_USERNAME` e `PANEL_PASSWORD`. Sem essas credenciais, escrita fica restrita a conexões loopback. Use rede protegida ou HTTPS para credenciais Basic.
- O prompt ativo padrão é `configs/system_prompt.md`, criado do [exemplo](configs/system_prompt.example.md). Aceita `{{DATA_HORA}}`, `{{DATA}}`, `{{HORA}}` e `{{DIA_SEMANA}}`.
- Para personalizar Juvenal, edite a Ficha da persona (nome, personalidade, tom, humor e respostas) e os administradores/preferências no arquivo pessoal; salve em UTF-8 e reinicie o Python. Nenhuma atualização do ESP é necessária. Dados familiares ficam apenas no prompt ignorado; o exemplo público usa campos genéricos. Veja [personalização da persona](docs/configuration.md#personalizar-a-persona).
- `LOG_TRANSCRIPTS=true` exibe texto reconhecido; `AUDIO_TEXT_ENDPOINT_LOG_RESPONSE=true` exibe uma prévia de até 4096 bytes do retorno HTTP. Ambas ficam desabilitadas no exemplo e devem ser habilitadas deliberadamente: os logs podem conter conversa privada.
- `.env`, prompts pessoais, ambientes virtuais, modelos, gravações, logs, dados e arquivos históricos ficam excluídos pelo `.gitignore`. Somente exemplos revisados devem ser versionados. Não force a inclusão de arquivos ignorados.

Consulte a [estrutura dos módulos](PROJECT_STRUCTURE.md), o [contrato WS v1 e migração](docs/esp32-websocket-mic.md) e a [operação Linux/systemd](docs/homelab-linux-setup.md). Os guias de ASR/wake word preservam referências históricas.

## Modelo personalizado e agradecimentos

O modelo que reconhece a palavra **“Juvenal”**, identificado nos arquivos como `juvehnal`, foi treinado com os recursos do projeto **[TaterTotterson/Tater-Wake-Words](https://github.com/TaterTotterson/Tater-Wake-Words)**. Agradecemos ao autor pela disponibilização do projeto e dos recursos para criação de wake words personalizadas.

Esse modelo reconhece a palavra de ativação. A transcrição da fala seguinte é responsabilidade do Whisper; a resposta conversacional vem do provedor configurado. O treinamento não é executado pelo Juvenal Server. Preserve os avisos e licenças aplicáveis aos modelos e componentes utilizados.

## Home Assistant

A integração é opcional e roda no Python: Juvenal encaminha o comando residencial à IA do Home Assistant, que interpreta e executa. **Não é necessário cadastrar ações** no padrão HASS_REQUIRE_CATALOG=false. Pedidos claros seguem diretamente; dúvidas relevantes da transcrição são esclarecidas antes do envio.

| HASS_MODE | Execução e resposta |
|---|---|
| api — padrão | POST em /api/conversation/process com Bearer e agente configurável. O speech retornado é sintetizado e reproduzido no ESP de origem, sem reescrita por outro LLM |
| text_endpoint | Envia comando ao receptor configurado, que executa e fala remotamente. Essa ação não produz TTS no ESP, mesmo com AUDIO_OUTPUT_MODE=esp32 |

HASS_ENABLED liga/desliga o módulo; HASS_API_ENDPOINT, HASS_API_TOKEN e HASS_AGENT_ID configuram a API. No modo alternativo, use HASS_TEXT_ENDPOINT_URL e o campo JSON HASS_TEXT_ENDPOINT_FIELD. Os dados ficam no .env privado do servidor; alterar modo exige apenas reiniciar Python, sem regravar o ESP. HASS_* é independente de AUDIO_TEXT_ENDPOINT_URL, que entrega respostas comuns.

TOOLS_ENABLED e a configuração estruturada do provedor/modelo precisam estar habilitados. Logs mostram início, HTTP, duração e resultado do POST; HASS_LOG_CONTENT acrescenta conteúdo limitado e sanitizado. Não há reenvio automático de ações com resultado incerto.

O catálogo do botão Automações permanece disponível com HASS_REQUIRE_CATALOG=true. Administrá-lo exige PANEL_USERNAME/PANEL_PASSWORD; data/automacoes.md é privado. Veja [configuração completa, alternância e verificações](docs/home-assistant.md).

A persona personalizável prioriza respostas curtas, recusa textos deliberadamente longos e interpreta erros claros de transcrição pelo contexto. Quando faltar clareza, faz uma pergunta breve. Consulte [personalização](docs/configuration.md#personalizar-a-persona).

## Próximas entregas e validação

O ESP principal já forneceu áudio e recebeu respostas em sessões reais; a continuidade também foi informada como funcionando. No ambiente atual, entrada vem do ESP e AUDIO_OUTPUT_MODE=esp32; LOCAL_VOICE_ENABLED=false desliga somente o microfone do computador. ESP_DEVICE_ID/ESP_DEVICE_TOKEN devem corresponder nos dois projetos. Não exponha token em URL. O experimental WAV permanece separado. A etapa Home Assistant/persona foi encerrada por aceite do usuário; os cenários complementares estão separados no [guia dos dois modos](docs/home-assistant.md).

Não foram individualmente comprovados o modo HA alternativo, alternância/desativação e todos os erros; esses cenários continuam como verificações complementares. Volume/persistência nas condições não relatadas, limites/cancelamento e operação Linux/systemd permanecem pendências próprias. Os recebimentos HTTP 200 anteriores e os playback confirmados no ESP continuam evidências das integrações correspondentes.

A validação funcional é manual, pelo usuário. Não há suíte automatizada de testes ou implantação automática nesta entrega.

## Continuidade da conversa com o ESP

Na extensão v1 de14/11/2026, uma wake inicia conversa de vários turnos no mesmo WS. Configure no .env do Python:

~~~dotenv
CONVERSATION_IDLE_TIMEOUT_SECONDS=10
~~~

O ESP informa quando está pronto para escutar e quando a fala começa. O prazo conta somente na escuta ociosa, é cancelado na fala e reiniciado depois da resposta completa e de nova prontidão. Captura de fala, STT, ferramentas, TTS e espera do ACK não consomem esse prazo. Expiração encerra o WS ordenadamente e devolve o ESP à wake.

É necessária uma atualização inicial coordenada dos dois projetos para os novos eventos. Depois dela, alterar10 para20 nessa variável e reiniciar o Python é suficiente, mantendo o firmware instalado. As antigas ESP_CONTINUATION_SECONDS/ESP_FIRST_SPEECH_SECONDS não controlam mais o v1; não há timeout ocioso duplicado no ESP. Veja o [contrato e a configuração](docs/esp32-websocket-mic.md).

O usuário informou que a continuidade passou a funcionar após a atualização dos dois projetos. A alteração 10 para 20 s também teve conferência específica isolada do servidor. LEDs e boca foram aprovados no hardware, e o usuário posteriormente informou funcionamento da integração Home Assistant. O encerramento da etapa preserva as verificações específicas ainda não relatadas.
