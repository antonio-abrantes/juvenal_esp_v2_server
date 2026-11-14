# Juvenal Server — Linux nativo e systemd

Fase 03 entrega scripts, perfis, unit parametrizável e roteiro operacional. Host Linux, arquitetura, usuário e caminhos reais ainda não foram informados; serviço não foi gerado para um host específico, instalado ou iniciado pelo agente. Não há Docker obrigatório.

## Preparação do host

Use Python 3.11 como referência. Confirme SO/arquitetura e disponibilidade das dependências nativas/wheels antes de escolher pacotes do SO. Os pinos diretos dos requirements não constituem lock integral de transitivas nem comprovação de instalação Linux.

Escolha usuário existente sem root, diretório de código/venv, arquivo dotenv protegido, diretório de dados persistentes e cache gravável. Não copie venv Windows para Linux. Preserve o prompt ativo e o catálogo existente; ao mudar JUVENAL_DATA_DIR, copie o catálogo e seu backup de forma deliberada antes de iniciar, sem sobrescrever o original.

| Perfil | Dependências além da base |
|---|---|
| auto | Conforme .env/ambiente |
| text | Nenhuma; texto + endpoint |
| endpoint | Whisper/numpy, sem captura ou player local |
| local | Whisper, wake/captura, Piper e player |
| esp32 | Whisper + Piper; sem wake/player do host |

Entrada ESP + endpoint dispensa placa de som, grupo audio e PulseAudio. Saída esp32 exige Piper, mas não player local. PortAudio/ALSA e runtime de wake só são necessários quando capacidades locais estão habilitadas.

## Instalação e primeiro início manuais

Na raiz do repositório no Linux, ajuste o dotenv antes de instalar auto. Se desejar arquivo externo, defina JUVENAL_ENV_FILE no ambiente do instalador e do processo, com caminho absoluto. Não execute source .env: dotenv não é shell. Os instaladores preservam arquivos existentes; o perfil seleciona pacotes, não reconfigura capacidades.

~~~bash
bash scripts/install.sh auto
bash scripts/start.sh
~~~

Para entrada ESP com entrega textual, o dotenv deve conter AUDIO_OUTPUT_MODE=endpoint, LOCAL_VOICE_ENABLED=false, STT_ENABLED=true, ESP_DEVICE_ID/TOKEN e AUDIO_TEXT_ENDPOINT_URL, além do provedor de IA. Copie configs/system_prompt.example.md para o prompt ativo somente se não existir; configure SYSTEM_PROMPT_PATH deliberadamente quando externo.

Whisper/Piper podem obter modelos no primeiro início da capacidade. Prepare-os/cacheie sob o usuário real do serviço. Endpoint não importa Piper. Os scripts não iniciam n8n, Ollama, HA ou outros serviços externos.

## Gerar a unit com caminhos reais

O template deploy/juvenal-server.service.in não é uma unit pronta para instalar. scripts/render_service.py valida usuário não root existente e caminhos absolutos, inclusive o Python do venv, dotenv, dados e cache. O gerador só escreve o arquivo solicitado; não executa systemctl. Execute no host Linux real após criar diretórios com proprietário/permissões corretos.

Substitua TODOS os exemplos abaixo pelos caminhos/usuário reais. Use o mesmo diretório de dados que contém o catálogo escolhido.

~~~bash
.venv/bin/python scripts/render_service.py \
  --user USUARIO_EXISTENTE \
  --project-dir /CAMINHO/DO/PROJETO \
  --env-file /CAMINHO/DA/CONFIGURACAO/juvenal.env \
  --data-dir /CAMINHO/DOS/DADOS \
  --cache-dir /CAMINHO/DO/CACHE \
  --output /CAMINHO/DO/PROJETO/deploy/juvenal-server.service
~~~

O gerador recusa sobrescrita sem --replace. Confira o arquivo antes de instalar. Não substitua o caminho lexical .venv/bin/python pelo destino resolvido do symlink: isso pode perder o venv.

A unit usa JUVENAL_ENV_FILE para que Python leia o dotenv, preservando a sintaxe já usada; não usa EnvironmentFile do systemd. JUVENAL_DATA_DIR e XDG_CACHE_HOME são explícitos e prevalecem sobre o dotenv. Variáveis já presentes no processo prevalecem sobre o arquivo; confira o ambiente efetivo sem registrar segredos.

O usuário precisa ler código/venv/configuração/prompt/modelos e escrever apenas dados/cache. Use permissões restritas no dotenv (ex.: 0600, proprietário correto). Dados/cache não devem ficar dentro do diretório de código protegido. A unit usa ProtectSystem=strict, ProtectHome=read-only, NoNewPrivileges, PrivateTmp, UMask=0077 e ReadWritePaths para dados/cache. Diretórios precisam existir; valide permissões no host. Modelos que exigem download não devem apontar para diretório de código somente leitura.

## Instalar, operar e consultar logs

Somente após revisar a unit gerada, os comandos manuais no host real são:

~~~bash
sudo install -m 0644 /CAMINHO/DO/PROJETO/deploy/juvenal-server.service /etc/systemd/system/juvenal-server.service
sudo systemctl daemon-reload
sudo systemctl enable --now juvenal-server.service
sudo systemctl status juvenal-server.service
sudo journalctl -u juvenal-server.service -f
~~~

Após editar o dotenv: sudo systemctl restart juvenal-server.service. Após editar a unit: daemon-reload e restart. Para parar: sudo systemctl stop juvenal-server.service. Para impedir início no boot: sudo systemctl disable juvenal-server.service. Não executar essas ações como consequência automática de gerar a unit.

Restart=on-failure tem intervalo de 5 s e limite de três partidas por 120 s. Após corrigir uma falha persistente, examine o journal antes de usar reset-failed/start. SIGTERM encerra admissões/sessões e sinaliza workers; TimeoutStopSec=90 dá prazo ao processo, com controle do grupo. Disponibilidade de rede não garante disponibilidade de STT/LLM/n8n.

JUVENAL_HOST/JUVENAL_PORT controlam bind; /health informa capacidades, não garante sucesso de uma conversa externa. Painel em / e observador em /ws; dispositivo usa /ws/esp. Firewall/proxy/TLS devem permitir Upgrade WS e prazos compatíveis, sem publicar Basic ou Bearer em rede desprotegida.

## Atualização, rollback e aceite pendente

Atualize código e dependências entre interações, preservando dotenv, prompt, modelos, dados e cópia anterior do catálogo. Reinicie manualmente. Para rollback, pare o serviço, recupere código/dependências/configuração compatíveis e inicie novamente. Não apague NVS do ESP ou catálogo como rotina. Histórico é volátil; catálogo é persistente. Nenhuma ação/entrega desconhecida é repetida após crash/reboot.

Ainda pendentes no host: resolução/instalação de dependências, geração da unit final, permissões/cache, boot sem login, restart após falha, shutdown em conversa e preservação do catálogo. Podem ser avaliados com endpoint/local, sem MAX98357A. O código e roteiro entregues não equivalem a serviço operacional aceito.
