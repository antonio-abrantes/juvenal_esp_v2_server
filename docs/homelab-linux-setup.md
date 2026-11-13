# Juvenal Server — preparação Linux nativa (JS-03.8)

Esta entrega prepara scripts, caminhos, dependências por capacidade e configuração para Python nativo, sem Docker. O agente não acessou o host Linux, instalou dependências, iniciou processos nem ativou serviço. Systemd/autostart, reboot e finalização operacional JS-03.9 permanecem futuros e podem ser concluídos em local/endpoint sem ESP32.

## Antes de instalar no host real

Conferir distribuição/arquitetura, versão Python, usuário não root e diretórios de código/configuração/cache/dados. Python 3.11 é a referência. As versões diretas dos manifests foram extraídas dos metadados do venv Windows existente, não de uma instalação Linux nova. Há pinos diretos; dependências transitivas continuam resolvidas pelo pip. Não apresentar esses arquivos como lock integral ou confirmação de wheels em toda arquitetura.

Whisper e Piper usam bibliotecas nativas e modelos; áudio local exige PortAudio/ALSA conforme a distribuição. **Entrada textual ou ESP com saída endpoint não exige placa de som, player, PulseAudio ou grupo audio.** Wake local exige runtime TFLite/pymicro-features compatíveis com o host; confirmar disponibilidade no SO/arquitetura antes de escolher o perfil local.

## Perfis e requisitos

| Perfil do instalador | Pacotes |
|---|---|
| auto | Base e capacidades selecionadas pelo .env/ambiente; padrão compatível local |
| text | Somente base, para entrada textual com entrega endpoint |
| endpoint | Base + Whisper/numpy, para áudio recebido sem captura/player local |
| local | Base + Whisper + captura/wake + Piper/player |
| esp32 | Base + Whisper; playback ESP permanece indisponível nesta fase |

Perfis explícitos selecionam dependências e não alteram o .env. O perfil auto usa STT_ENABLED, LOCAL_VOICE_ENABLED e AUDIO_OUTPUT_MODE. O legado sem modo explícito permanece misto e requer saída local para as origens locais.

O requirements.txt conserva o perfil local completo para instalação manual. requirements-core.txt não instala OpenAI SDK, Piper ou áudio. Os outros manifests são requisitos de capacidades reais. O modelo MicroWakeWord, modelos Whisper/Piper e prompt devem ser preservados; instalar dependências não configura os provedores ou copia segredos.

## Preparação manual

Copiar o projeto para o diretório escolhido, sem transportar um venv Windows para Linux. Não alterar o original externo. O instalador usa JUVENAL_PYTHON (padrão python3) para criar o venv; usar o executável 3.11 instalado no host, quando necessário.

A partir da raiz do projeto no Linux:

~~~bash
bash scripts/install.sh auto
~~~

Se .env já existir, seu conteúdo e permissões não são reescritos. Em instalação nova, uma cópia do exemplo é criada com umask 077. Configurar fornecedor e capacidades antes de iniciar. Não executar source .env: dotenv não é um script de shell.

Exemplo de configuração a acrescentar para uma instalação que entrega texto e não captura áudio no host:

~~~dotenv
AUDIO_OUTPUT_MODE=endpoint
LOCAL_VOICE_ENABLED=false
STT_ENABLED=true
~~~

Preencher separadamente AUDIO_TEXT_ENDPOINT_URL e contrato real; se a entrada for só texto, STT_ENABLED=false e perfil text dispensam também o Whisper. Não colocar chaves reais na documentação. Consultar [configuration.md](configuration.md) para autenticação/formato/status e migração da variável legada.

## Inicialização pelo usuário

~~~bash
bash scripts/start.sh
~~~

O script encontra a raiz a partir do seu próprio diretório, chama o Python absoluto do venv e usa exec para transmitir sinais ao processo Python. Não exige shell ativado, VS Code ou login gráfico. Também é possível iniciar com .venv/bin/python server/main.py.

JUVENAL_HOST e JUVENAL_PORT controlam endereço/porta. O painel é servido em /, /ws transmite eventos, /health mostra capacidades. Os scripts não iniciam Ollama, n8n, Home Assistant nem outro serviço externo. Cada dependência externa precisa de operação própria.

Whisper/Piper ausentes podem baixar modelos quando a aplicação for iniciada com a capacidade correspondente; preparar/cachear previamente no provisionamento escolhido pelo usuário. Em endpoint, Piper não é importado nem baixado. Não pressupor que uma rede pronta significa provedor pronto.

## Persistência e recuperação

.env e configs/system_prompt.md são os arquivos ativos de configuração/prompt. Código/venv devem ser separados de configuração protegida e de diretórios graváveis no desenho do serviço. Cache de modelos usa o usuário do processo. JUVENAL_DATA_DIR reserva os dados futuros, sem criar catálogo nesta fase. Histórico em memória pode ser descartado em restart.

O núcleo controla admissão, tarefas, cancelamento, limites e descarte de resultados atrasados; um worker síncrono residual continua contabilizado como ocupado. Shutdown não significa rollback de um POST externo. Não repetir respostas/ações após reboot nem trocar destino após resultado desconhecido.

## O que ainda falta para operação definitiva

Identificar host/usuário/caminhos; verificar dependências nativas/wheels; preparar unit systemd apropriada, EnvironmentFile/permissões, WorkingDirectory, Python do venv e limites de restart/shutdown. A unit não foi criada nem instalada nesta fase. Validar boot, restart, encerramento e falhas fica a cargo do usuário em JS-03.9.

Para rollback, recuperar código/scripts/manifests e configuração compatível entre interações; recriar dependências no venv somente quando solicitado. Não reenviar um POST de resultado desconhecido. A baseline anterior desta entrega está registrada na documentação de trabalho do workspace, não em uma tag nova.
