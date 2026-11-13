# Estrutura atual do Juvenal Server — fase 01

| Local | Responsabilidade |
|---|---|
| server/main.py | FastAPI/lifespan, entradas, tarefas e eventos do painel |
| server/config.py | .env único, raiz/caminhos, capacidades, limites e migração de saída |
| server/conversation.py | Admissão serializada, contexto por identidade, TTL, turno comum e entrega |
| server/response_output.py | Adaptadores local/endpoint e indisponibilidade explícita de playback esp32 |
| server/runtime.py | Controle de worker síncrono e capacidade residual após cancelamento |
| server/llm_client.py / providers.py | Chamadas HTTP/streaming aos provedores; sem histórico global |
| server/dismissal_checker.py | Classificação de despedida isolada do histórico |
| server/transcriber.py | Whisper PCM; captura local opcional, cleanup e sinalização de fim |
| server/tts.py | Síntese Piper nativa e player local separados; sem HTTP/texto HA |
| server/wake_word.py | Detector local preservado, com transferência controlada do microfone |
| server/legacy_esp.py | Adaptador /ws/esp antigo, com limites e tarefas; não é WS v1 |
| frontend/ | Painel de monitoramento; eventos compatíveis, ws/wss e diagnósticos de erro |
| configs/system_prompt.md | Prompt ativo padrão, editável, com placeholders por solicitação |
| system_prompt.md | Cópia histórica preservada; não carregada por padrão |
| .env / .env.example | Configuração protegida real / template sem segredos |
| requirements*.txt | Base e capacidades; requirements.txt mantém o perfil local completo |
| scripts/install.* | Venv/dependências por perfil; preservam configuração, sem startup |
| scripts/install_profile.py | Seleção de manifests sem importar a aplicação |
| scripts/start.* | Raiz correta e Python do venv, sem iniciar serviços externos |
| models/ | Modelo MicroWakeWord e modelos locais escolhidos; usados conforme capacidade |
| docs/configuration.md | Contratos/configuração e migração da fase 01 |
| docs/homelab-linux-setup.md | Preparação Linux nativa; systemd final permanece futuro |
| archive/ | Referências históricas preservadas, fora dos módulos ativos |

Os modelos e drivers de captura/player não são obrigatórios para entrada textual + endpoint. O servidor não requer um broker, banco ou framework de agentes novo. Ferramentas/catálogo HA são da fase 02; WS v1, retorno PCM, MAX98357A e firmware são da fase 03. O arquivo docs/esp32-websocket-mic.md continua referência do cliente/protocolo legado, não instrução de integração v1.

Python 3.11 é a referência. Nenhuma dependência foi instalada ou aplicação iniciada pelo agente nesta entrega; execução e avaliação manual ficam a cargo do usuário. A versão experimental ESP v0.4 continua separada e não foi modificada.
