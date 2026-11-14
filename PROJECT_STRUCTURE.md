# Estrutura atual do Juvenal Server — fases 01 a 03

| Local | Responsabilidade |
|---|---|
| server/main.py | FastAPI/lifespan, entradas, tarefas e eventos do painel |
| server/config.py | .env único, raiz/caminhos, capacidades, limites e migração de saída |
| server/conversation.py | Admissão serializada, contexto por identidade, TTL, turno comum e entrega |
| server/automation_catalog.py | Catálogo Markdown validado, revisão, backup e escrita atômica |
| server/catalog_api.py | Administração autenticada e controle de origem/revisão do catálogo |
| server/tools/registry.py | Contratos, validação e registro explícito de ferramentas |
| server/tools/home_assistant.py | Permissões, propostas de confirmação e adaptador n8n sem reenvio |
| server/esp_transport.py | WS v1 /ws/esp, PCM/VAD, limites, sessões e créditos/ACK de playback |
| server/pcm_stream.py | Conversão contínua da taxa/canais nativos Piper para PCM16 mono 16 kHz |
| server/response_output.py | Um destino local/endpoint/esp32; retorno WS exige cliente apto e ACK |
| server/runtime.py | Controle de worker síncrono e capacidade residual após cancelamento |
| server/llm_client.py / providers.py | Chamadas HTTP/streaming aos provedores; sem histórico global |
| server/dismissal_checker.py | Classificação de despedida isolada do histórico |
| server/transcriber.py | Whisper PCM; captura local opcional, cleanup e sinalização de fim |
| server/tts.py | Síntese Piper nativa e player local separados; sem HTTP/texto HA |
| server/wake_word.py | Detector local preservado, com transferência controlada do microfone |
| server/legacy_esp.py | Adaptador /ws/esp/legacy, opt-in e autenticado; sem playback |
| frontend/ | Painel de monitoramento; eventos compatíveis, ws/wss e diagnósticos de erro |
| frontend/catalog.js | Cadastro estruturado de automações, sem executor genérico no navegador |
| configs/system_prompt.md | Prompt ativo padrão, editável, com placeholders por solicitação |
| system_prompt.md | Cópia histórica preservada; não carregada por padrão |
| .env / .env.example | Configuração protegida real / template sem segredos |
| requirements*.txt | Base e capacidades; requirements.txt mantém o perfil local completo |
| scripts/install.* | Venv/dependências por perfil; preservam configuração, sem startup |
| scripts/install_profile.py | Seleção de manifests sem importar a aplicação |
| scripts/render_service.py | Gera unit com usuário/caminhos reais no Linux, sem instalar/iniciar |
| deploy/juvenal-server.service.in | Template systemd, dados/cache separados, restart limitado e shutdown |
| scripts/start.* | Raiz correta e Python do venv, sem iniciar serviços externos |
| models/ | Modelo MicroWakeWord e modelos locais escolhidos; usados conforme capacidade |
| docs/configuration.md | Contratos/configuração e migração da fase 01 |
| docs/homelab-linux-setup.md | Instalação nativa, gerador de unit, operação e rollback; host real pendente |
| archive/ | Referências históricas preservadas, fora dos módulos ativos |

Os modelos e drivers de captura/player não são obrigatórios para entrada textual + endpoint. O servidor não requer broker, banco ou framework de agentes novo. Ferramentas/catálogo HA foram implementados na fase 02, com execução opt-in após confirmação do provedor e contrato; integração real pendente. A fase 03 implementa WS v1 e retorno PCM; firmware correspondente está no repositório separado juvenal_esp_v2. docs/esp32-websocket-mic.md documenta o contrato atual e a migração explícita do legado. Hardware e operação Linux continuam pendentes de aceite manual.

Python 3.11 é a referência. Nenhuma dependência foi instalada ou aplicação iniciada pelo agente nesta entrega; execução e avaliação manual ficam a cargo do usuário. A versão experimental ESP v0.4 continua separada e não foi modificada.
