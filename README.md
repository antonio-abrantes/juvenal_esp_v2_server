# Juvenal Server

Servidor Python do assistente de voz **Juvenal**, desenvolvido por Antônio Abrantes. Centraliza transcrição, conversa com IA, contexto e entrega da resposta, preparando a integração com um dispositivo **ESP32-S3** independente do Home Assistant.

Este componente usa repositório próprio: **`juvenal_esp_v2_server`**, associado ao firmware `juvenal_esp_v2`. A pasta local do servidor permanece `juvenal_server`; firmware e servidor têm históricos separados.

## Estado do projeto

A fase 01 está encerrada no escopo de núcleo e saídas configuráveis, com validação manual parcial no Windows. O usuário comprovou detecção da wake word, captura/transcrição, resposta do LLM, recebimento de texto no webhook com HTTP 200 e retorno à escuta, inclusive após falha HTTP. A transcrição voltou a aparecer no terminal quando habilitada.

A fase 02 entrega ferramentas estruturadas e catálogo de automações Home Assistant/n8n com edição autenticada pelo painel. A fase foi encerrada após o aceite manual informado pelo usuário para o painel, catálogo e conversa. A execução real de ações ainda depende da confirmação do provedor/modelo e do contrato do workflow. WebSocket por interação, retorno PCM/TTS ao ESP32 e reprodução pelo MAX98357A permanecem futuros. O adaptador `/ws/esp` existente é legado; sua presença não comprova integração com o firmware atual.

O firmware experimental **Juvenal Visual v0.4**, no projeto separado `juvenal_http_teste`, continua usando MicroWakeWord, gravação WAV PCM16 e HTTP POST. Ele não utiliza o transporte futuro deste servidor. A integração será implementada no firmware principal `juvenal_esp`, com ESP-IDF **5.4.4**.

## Como a conversa funciona

O fluxo atual de diagnóstico usa o microfone do computador: wake word → captura → Whisper local → texto → provedor de IA → resposta final → destino configurado.

As integrações têm responsabilidades distintas:

| Configuração | Finalidade |
|---|---|
| `LLM_PROVIDER` e variáveis do provedor | Gerar a resposta da IA; suporta OpenAI, Ollama, Groq, servidor compatível e webhook conversacional |
| `WEBHOOK_URL` / `WEBHOOK_API_KEY` | Provedor alternativo, ativo somente com `LLM_PROVIDER=webhook`; contrato `message` → `response` |
| `AUDIO_TEXT_ENDPOINT_URL` | Receber a resposta final já produzida; ativo com `AUDIO_OUTPUT_MODE=endpoint` |
| Ferramenta HA/n8n | Executar ações do catálogo, com permissões, confirmação e configuração `HA_AUTOMATION_*` própria |

Com OpenAI como provedor e saída `endpoint`, a resposta é gerada pela OpenAI e depois enviada ao webhook. O corpo devolvido por esse webhook pode ser registrado para diagnóstico; não é interpretado como nova resposta da IA, ferramenta ou áudio.

## Modos de saída

| `AUDIO_OUTPUT_MODE` | Comportamento |
|---|---|
| `local` | Sintetiza com Piper e reproduz no dispositivo de áudio do servidor |
| `endpoint` | Envia uma tentativa de POST por resposta final; não carrega nem executa TTS |
| `esp32` | Reserva a saída futura; atualmente informa `esp32_playback_unavailable` |

No modo `endpoint`, o formato padrão é JSON `{"texto": "resposta final da IA"}`; há opção de texto puro. Status aceito confirma entrega HTTP conforme a configuração, sem confirmar reprodução de fala. Não há reenvio ou mudança automática de destino após falha.

`LOCAL_VOICE_ENABLED` controla wake/microfone do host, `STT_ENABLED` habilita Whisper e `AUDIO_OUTPUT_DEVICE` escolhe somente a reprodução local. No modo `endpoint` explícito, cada ativação de áudio termina após uma resposta; o servidor não presume o momento em que um receptor externo terminou de falar.

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

Edite o `.env` local com seu provedor, credenciais e capacidades. Para reproduzir o fluxo atual de microfone local e entrega de texto:

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

Whisper e Piper podem baixar modelos ao iniciar a capacidade correspondente. No modo `endpoint`, Piper não é carregado. Detalhes: [configuração](docs/configuration.md) e [preparação Linux](docs/homelab-linux-setup.md).

## Alteração do destino

Para enviar a resposta ao webhook, use `AUDIO_OUTPUT_MODE=endpoint` e preencha `AUDIO_TEXT_ENDPOINT_*`. Para ouvir pelo computador, use `AUDIO_OUTPUT_MODE=local`, configure Piper/dispositivo e instale as dependências locais correspondentes. Reinicie o processo após mudar o `.env`.

O modo `esp32` só poderá ser usado para reprodução depois da implementação da integração e validação no dispositivo. Selecioná-lo agora não habilita retorno de áudio.

## Painel, configuração e privacidade

- `/`: painel de monitoramento; `/ws`: eventos; `/health`: capacidades.
- O backend aceita entrada textual `send_text`; o painel atual é um monitor, sem formulário de chat.
- Para controles pela LAN, configure `PANEL_USERNAME` e `PANEL_PASSWORD`. Sem essas credenciais, escrita fica restrita a conexões loopback. Use rede protegida ou HTTPS para credenciais Basic.
- O prompt ativo padrão é `configs/system_prompt.md`, criado do [exemplo](configs/system_prompt.example.md). Aceita `{{DATA_HORA}}`, `{{DATA}}`, `{{HORA}}` e `{{DIA_SEMANA}}`.
- `LOG_TRANSCRIPTS=true` exibe texto reconhecido; `AUDIO_TEXT_ENDPOINT_LOG_RESPONSE=true` exibe uma prévia de até 4096 bytes do retorno HTTP. Ambas ficam desabilitadas no exemplo e devem ser habilitadas deliberadamente: os logs podem conter conversa privada.
- `.env`, prompts pessoais, ambientes virtuais, modelos, gravações, logs, dados e arquivos históricos ficam excluídos pelo `.gitignore`. Somente exemplos revisados devem ser versionados. Não force a inclusão de arquivos ignorados.

Consulte também a [estrutura dos módulos](PROJECT_STRUCTURE.md). Os guias de ASR, wake word e cliente ESP legado preservam referências históricas; este README e a configuração descrevem o estado atual.

## Modelo personalizado e agradecimentos

O modelo que reconhece a palavra **“Juvenal”**, identificado nos arquivos como `juvehnal`, foi treinado com os recursos do projeto **[TaterTotterson/Tater-Wake-Words](https://github.com/TaterTotterson/Tater-Wake-Words)**. Agradecemos ao autor pela disponibilização do projeto e dos recursos para criação de wake words personalizadas.

Esse modelo reconhece a palavra de ativação. A transcrição da fala seguinte é responsabilidade do Whisper; a resposta conversacional vem do provedor configurado. O treinamento não é executado pelo Juvenal Server. Preserve os avisos e licenças aplicáveis aos modelos e componentes utilizados.

## Automações e ferramentas

O botão **Automações** abre o cadastro no painel. Configure `PANEL_USERNAME` e `PANEL_PASSWORD` no `.env` e reinicie: administrar o catálogo exige autenticação mesmo no computador local. É possível cadastrar, editar, habilitar, desabilitar e remover entradas; salvar não executa ações.

O arquivo `data/automacoes.md` é criado no primeiro salvamento, sob `JUVENAL_DATA_DIR`, e fica fora do Git. O backend valida ações, entidades, parâmetros e identidades permitidas. Edições concorrentes geram conflito; uma edição inválida bloqueia ações. A revisão anterior é preservada em `automacoes.md.previous`.

As ferramentas têm padrão `TOOLS_ENABLED=false`. Para ativar, confirme explicitamente o suporte estruturado da combinação provedor/modelo/adaptador e o contrato textual separado do n8n. Não reutilize automaticamente o webhook de saída ou de WAV. Veja [configuração das ferramentas](docs/configuration.md#ferramentas-e-catálogo--fase-02).

Confirmações expiram e valem somente para os parâmetros, identidade e revisão apresentados. Em saída `endpoint`, confirme numa nova ativação da wake word. Cancelamento não desfaz um envio externo; resultado incerto não provoca repetição.

## Próximas entregas e validação

A próxima etapa integra WebSocket por sessão, áudio PCM16 e reprodução no ESP32-S3, mantendo os destinos de saída independentes. A validação real das ferramentas e do workflow n8n permanece pendente.

Permanecem sem comprovação nesta entrega: conectividade após o último ajuste da URL local, log do corpo devolvido pelo webhook, reprodução local nesta versão, todos os cenários de limites/cancelamento, execução Linux/systemd e áudio no ESP32. O recebimento HTTP 200 anterior continua sendo uma evidência válida de entrega textual.

A validação funcional é manual, pelo usuário. Não há suíte automatizada de testes ou implantação automática nesta entrega.
