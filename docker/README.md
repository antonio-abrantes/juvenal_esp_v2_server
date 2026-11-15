# Juvenal Server em Docker

**Limite de implantação:** estes exemplos destinam-se à VPS/outros hosts autorizados. No Orange Pi do projeto, manter Juvenal nativo em venv/systemd; nunca subir containers do Juvenal ou auxiliares. A busca DuckDuckGo usa DDGS dentro do Python, sem container de buscador.

Imagem CPU para **linux/amd64** e **linux/arm64**, com Python3.11, Whisper e Piper. Um mesmo nome/tag contém o manifest das duas arquiteturas; Docker escolhe a plataforma do host. Execução nativa e scripts existentes continuam disponíveis.

O container é headless: microfone/wake e reprodução ficam no ESP. LOCAL_VOICE_ENABLED=false, WHISPER_DEVICE=cpu e TTS_BACKEND=piper são fixados nos exemplos. AUDIO_OUTPUT_MODE aceita esp32 ou endpoint; áudio local do host permanece no modo nativo.

## Publicação no GitHub Container Registry

Imagem deste repositório: ghcr.io/antonio-abrantes/juvenal_esp_v2_server.

| Tag Git enviada | Workflow | Tags da imagem |
|---|---|---|
| v0.1.0-dev | docker-dev.yml | 0.1.0-dev, dev, dev-SHA |
| v0.1.0-beta | docker-dev.yml | 0.1.0-beta, dev, dev-SHA |
| v0.1.0-rc.1 | docker-dev.yml | 0.1.0-rc.1, dev, dev-SHA |
| v0.1.0 | docker-prod.yml | 0.1.0, prod, latest, prod-SHA |

Os workflows só disparam no push de tags compatíveis. Push de branches, PRs, criar Release sem enviar tag, agendamentos e execução manual não disparam. Cada tag constrói o commit apontado: a branch não determina dev/prod. O commit precisa conter Dockerfile, docker/ e workflows, inclusive quando a main ainda estiver atrás da develop.

Os exemplos de versão acima não criam uma versão automaticamente. Depois de revisar, commitar e enviar o código, o usuário escolhe uma tag ainda livre e publica:

~~~bash
git tag v0.1.0-dev
git push origin v0.1.0-dev
~~~

Para produção, usar uma tag estável, por exemplo v0.1.0, no commit escolhido. Não reutilizar/mover tags já publicadas. O GITHUB_TOKEN do job autentica no GHCR com packages:write; nenhuma chave de IA/HA é necessária no build. É preciso permitir Actions e os actions usados nas configurações do repositório.

Há QEMU/Buildx para AMD64+ARM64, sem cache de build conforme o padrão adotado. A primeira publicação ainda precisa comprovar disponibilidade dos wheels nas duas arquiteturas. Os aliases dev/prod/latest refletem a publicação que os atualizou por último; para deploy reproduzível, fixe a versão ou digest. Nenhum workflow faz deploy, SSH, teste da aplicação ou ação no HA.

Pacotes GHCR podem nascer privados. Torne o pacote público ou cadastre uma credencial de leitura do GHCR no Portainer e selecione o registry ao subir a stack.

## Portainer Swarm com Traefik

1. Crie uma stack no endpoint Swarm e cole docker/stack.swarm.yml no editor.
2. Em Environment variables, use **Load variables from .env file** e importe seu .env real ou uma cópia preenchida de docker/.env.example.
3. Defina JUVENAL_DOMAIN com o domínio da VPS. Ajuste JUVENAL_IMAGE_TAG para dev, latest ou uma versão publicada.
4. Confira LLM/provider/modelo/chaves, PANEL_USERNAME/PANEL_PASSWORD e ESP_DEVICE_ID/ESP_DEVICE_TOKEN. O token deve coincidir com o firmware.
5. Faça o deploy manual e acompanhe logs e health no Portainer.

As variáveis do aplicativo são interpoladas explicitamente em environment. Não há env_file/stack.env, build local, container_name ou plataforma fixada na stack. O .env importado pela interface fornece os valores; não precisa existir um arquivo .env no host da VPS.

Os exemplos fixam caminhos internos /config, /data e /cache, bind0.0.0.0/porta8000 e capacidades headless. Valores antigos de caminhos Windows, porta do host e dispositivo de áudio no .env não sobrepõem essas escolhas. HASS_* e os modos de saída são preservados; catálogo permanece opcional. Para HA, TOOLS_ENABLED/LLM_TOOL_CALLING_CONFIRMED e provedor/modelo correspondentes precisam estar configurados como na instalação que já funciona.

A rede network_public deve existir e ser compartilhada com o Traefik. Ajuste TRAEFIK_NETWORK, TRAEFIK_ENTRYPOINT e TRAEFIK_CERTRESOLVER à infraestrutura. O exemplo padrão usa websecure, TLS e letsencryptresolver. Labels ficam em deploy.labels e apontam para a porta interna8000; não há publicação direta de porta no Swarm.

As stacks Swarm definem FORWARDED_ALLOW_IPS=* para o Uvicorn reconhecer HTTPS encaminhado pelo Traefik, inclusive na verificação de origem do painel. Essa escolha pressupõe backend acessível somente pela rede Docker interna e proxies confiáveis; não publique8000 diretamente nessas stacks. Pode restringir FORWARDED_ALLOW_IPS aos IPs estáveis dos proxies quando disponíveis. O Compose standalone mantém o padrão restrito a127.0.0.1.

Uma réplica e node.role==manager preservam o estado local da conversa e o padrão de agendamento. Não há node.labels.*. Os volumes são locais ao nó: com vários managers, escolha um manager estável usando uma constraint padrão de hostname ou providencie armazenamento compartilhado. Mudar de nó não migra volumes automaticamente.

Swarm reinicia o serviço em falha. A atualização é stop-first para evitar dois processos simultâneos da mesma instância. O processo recebe SIGTERM e tem90s para encerrar. Não configurar múltiplas réplicas para esta arquitetura de contexto em memória.

## Volumes, modelos e persona

| Volume | Destino | Conteúdo |
|---|---|---|
| juvenal_config | /config | system_prompt.md personalizável; opcionalmente dotenv adicional |
| juvenal_data | /data | Catálogo opcional e seus backups |
| juvenal_cache | /cache | Downloads Hugging Face/Whisper e Piper |

Nomes reais recebem o prefixo da stack/projeto. Docker inicializa os volumes novos a partir dos diretórios da imagem, preparados para UID/GID10001. Volumes/bind mounts já existentes precisam ser graváveis por esse UID; a aplicação não assume root para corrigir permissões. /app é somente leitura nos exemplos.

O primeiro início baixa os modelos necessários, sem incluí-los na imagem. O healthcheck tem600s de tolerância inicial. Ele verifica HTTP/status e capacidades STT/TTS necessárias, mas não aciona ferramentas nem confirma conectividade com HA/LLM ou som real no ESP.

A imagem inclui apenas a persona genérica de configs/system_prompt.example.md. **A persona pessoal não é enviada ao GitHub nem embutida na imagem.** Para preservar sua configuração atual, use o navegador de volumes do Portainer, quando disponível, para enviar o arquivo pessoal como system_prompt.md ao volume juvenal_config; mantenha acesso pelo UID10001 e reinicie/redeploy a stack. Alternativamente, o administrador pode copiar o arquivo para esse volume no nó responsável. Isso é feito uma vez, não a cada atualização. O entrypoint não sobrescreve persona existente.

Atualizar imagem preserva volumes. Não remover volumes para atualizar. Faça backup de config/data; cache pode ser baixado novamente. Dados pessoais e logs não devem ser publicados.

## Rede, Home Assistant e ESP

Painel: https://SEU_DOMINIO/ com Basic Auth já implementado. ESP: wss://SEU_DOMINIO/ws/esp com Bearer; não colocar middleware Basic global sobre essa rota. Traefik encaminha WebSocket sem trocar o protocolo PCM/WS existente.

HASS_API_ENDPOINT e o endpoint alternativo precisam ser alcançáveis **de dentro da VPS/container**. localhost aponta ao próprio container. Um IP privado da sua casa requer conectividade privada ou um endpoint que já seja acessível à VPS; este pacote não configura túnel, firewall, DNS ou HA. Ollama externo pode ser configurado com OLLAMA_BASE_URL, mantendo /v1; o valor deve ser um endereço acessível ao container.

No arquivo privado do ESP main/juvenal_secrets.h, manter o transporte WS e alterar JUVENAL_WS_URL para wss://SEU_DOMINIO/ws/esp. Preservar ID/token iguais aos do servidor. WSS já usa bundle de certificados e exige horário sincronizado; aguardar SNTP e certificado público válido no domínio. Não desabilitar verificação TLS.

Terminal ESP-IDF5.4.4 no Windows, somente quando decidir migrar o dispositivo:

~~~powershell
idf.py -C "C:\Projects\Juvenal_v2\projects\juvenal_esp" build
idf.py -C "C:\Projects\Juvenal_v2\projects\juvenal_esp" -p COM5 flash monitor
~~~

Conferir COM5 se reconectar; Ctrl+] sai do monitor. Sem erase-flash. A aplicação nativa do Orange Pi não é parada/desinstalada por estes arquivos; escolher deliberadamente qual servidor receberá o ESP.

## Docker Compose sem Swarm

docker/compose.yml publica apenas127.0.0.1:8000 por padrão. JUVENAL_BIND_IP/JUVENAL_HTTP_PORT alteram isso quando necessário, em ambiente controlado. No Portainer Standalone, cole esse arquivo e importe as variáveis da mesma forma.

Exemplo manual na raiz do repositório, usando um arquivo privado preparado:

~~~bash
docker compose --env-file docker/.env -f docker/compose.yml up -d
~~~

## Atualização e verificação manual

No Portainer, altere JUVENAL_IMAGE_TAG para versão publicada e use Update/Re-pull. dev/prod/latest são aliases mutáveis; versões/digests permitem escolher o rollback. As sessões/contextos em memória não sobrevivem ao restart; ações incertas não são reenviadas automaticamente.

Conferir manualmente: Actions por tag e não por branch, manifest AMD64/ARM64, ausência de segredos/arquivos pessoais na imagem, volumes após update, health e logs, Basic Auth, ESP WSS/voz/HA, alternância de saída e reinício. Nesta entrega houve apenas revisão estática; não foram construídas/publicadas imagens, iniciados containers ou feitos deploys/testes pelo agente.

Referências: [multiarch](https://docs.docker.com/build/ci/github-actions/multi-platform/), [tags GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onpushbranchestagsbranches-ignoretags-ignore), [GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [variáveis Portainer](https://docs.portainer.io/user/docker/stacks/add), [labels Traefik/Swarm](https://doc.traefik.io/traefik/v2.11/routing/providers/docker/).
