# ESP32-S3 — integração WebSocket v1

Implementação da fase 03 em ESP-IDF 5.4.4 e no Juvenal Server. Cinco sessões anteriores com playback confirmado foram fornecidas pelo usuário; LEDs de boot e boca também foram aprovados. A continuidade descrita nesta revisão é posterior e ainda requer atualização inicial/aceite na placa. O cliente é o firmware principal juvenal_esp; o experimental juvenal_http_teste Visual v0.4 permanece WAV/POST.

## Configuração inicial: ESP recebe voz e servidor entrega texto

No .env do servidor: AUDIO_OUTPUT_MODE=endpoint, STT_ENABLED=true, ESP_DEVICE_ID e ESP_DEVICE_TOKEN preenchidos. Preserve o provedor de IA e AUDIO_TEXT_ENDPOINT_URL existentes. LOCAL_VOICE_ENABLED=false dispensa microfone/wake do computador; mantenha true enquanto ainda precisar do diagnóstico local. Reinicie manualmente após editar.

No header privado main/juvenal_secrets.h do firmware, use JUVENAL_AUTO_TRANSPORT_WS=true, JUVENAL_WS_URL=ws://IP_DO_SERVIDOR:8000/ws/esp e a mesma identidade/credencial em JUVENAL_WS_DEVICE_ID/TOKEN. Defina Wi-Fi. Token não vai na URL nem no painel. Um exemplo público sem segredos acompanha o firmware.

Não usar 127.0.0.1/localhost no ESP: esses endereços apontam para o próprio dispositivo. IP, firewall e porta precisam permitir acesso pela LAN. WS sem TLS requer rede privada protegida; para WSS, o firmware valida certificado com o bundle e exige relógio sincronizado por SNTP.

O modelo MicroWakeWord continua no ESP; o servidor não precisa desse modelo nem de placa de som quando LOCAL_VOICE_ENABLED=false. Whisper continua necessário para a fala recebida.

## Sessão e envelope

A rota do dispositivo é /ws/esp. O /ws do painel é observador e usa outro contrato. Bearer obrigatório no handshake; session.start precisa chegar no prazo configurado.

~~~json
{"protocol_version":1,"event":"session.start","payload":{"device_id":"juvenal","playback":false,"playback_buffer_bytes":16384,"conversation_events":true}}
~~~

O servidor responde session.ready com session_id e turn_id, ambos novos, e payload com sample_rate=16000, channels=1, sample_format=s16le, output_mode, vad_start_rms, conversation_events=true e continuation. session_id permanece na conversa; turn_id muda a cada nova escuta. Mensagens seguintes incluem ambos os IDs. input.start informa exatamente os três campos de formato. Entrada binária é PCM bruto, sem WAV/base64, em quadros pares até 4096 bytes; o cliente envia nominalmente 640 bytes.

| Evento | Direção | Regra |
|---|---|---|
| input.start / PCM / input.end | ESP → servidor | Stream de entrada; fim inclui reason e total_samples efetivamente enviados |
| input.ready | ESP → servidor | Payload vazio; captura compartilhada pronta para ouvir. Arma a inatividade no Python |
| input.speech_started | ESP → servidor | Payload vazio; primeiro bloco elegível no RMS enviado pelo servidor. Cancela a inatividade imediatamente; VAD confirma fala no PCM |
| input.stop | Servidor → ESP | Parar entrada por vad_silence, conversation_idle_timeout ou no_speech; responder input.end com o motivo correspondente |
| assistant.state | Servidor → ESP | Estado de processamento |
| transcript / assistant.response | Servidor → ESP | Prévia textual limitada; resposta é display_only, sem disparar outro áudio/POST |
| output.start / PCM / output.end | Servidor → ESP | Apenas saída esp32; formato fixo, blocos e totais |
| playback.credit | ESP → servidor | consumed_bytes cumulativo, par, nunca maior que os bytes enviados |
| playback.finished | ESP → servidor | total_bytes e total_samples iguais aos enviados, depois de output.end e do TX drenado |
| session.cancel | ESP → servidor | Interrompe sessão; não desfaz ação externa já enviada |
| session.error / session.end | Servidor → ESP | Falha/resultado terminal e fechamento; sem replay |

input.end aceita complete, client_vad, server_vad, no_speech ou conversation_idle_timeout. capture_limit e outros motivos forçados abortam; não processar comando cortado. O ESP transmite continuamente por um ring reutilizável na área compartilhada com o diagnóstico WAV. Seu tamanho não é duração máxima de fala; seis segundos limitam somente a gravação WAV manual/legada. O servidor determina o fim normal pelo VAD e mantém ESP_MAX_INPUT_SECONDS (default30s a partir do início da fala) como proteção configurável; silencio aguardando pergunta usa apenas pré-buffer limitado a500ms, sem consumir esse prazo; nunca executar comando cortado ao atingir limite.

VAD usa amostras PCM, não intervalo entre pacotes: três quadros de 20 ms acima do RMS inicial, seguidos de silêncio configurado (1,8 s padrão). Ganho 1,6× e HPF 80 Hz são aplicados uma vez no worker do ESP; MicroWakeWord recebe áudio original. Overflow ou demora em respeitar input.stop terminam com erro.

## Entrega e contexto

Cada turno completa a entrega antes de reabrir a escuta: endpoint aguarda aceite HTTP (sem afirmar fala externa terminada), local aguarda player e esp32 aguarda ACK após DMA. Falha encerra com erro e não provoca replay/fallback. Uma entrega bem-sucedida permite nova pergunta sem outra wake, na mesma conexão.

A identidade autenticada esp:ID mantém contexto dentro do TTL entre sockets. session_id não é identidade permanente. Nova wake abre nova conexão; em espera WS fechado. Queda não dispara reconexão, ação ou resposta automática. Trabalho síncrono residual permanece ocupado até terminar.

### Inatividade conversacional exclusivamente no servidor

CONVERSATION_IDLE_TIMEOUT_SECONDS=10 controla a espera ociosa, inclusive a primeira pergunta após wake. O ESP envia input.start e input.ready quando estiver pronto para escutar; só input.ready inicia a contagem. Nova fala envia input.speech_started e cancela o prazo. O VAD de PCM também confirma a transição. Durante captura da fala, STT, ferramentas, entrega/TTS e espera do ACK não existe contagem ociosa.

Depois de concluir a resposta, o Python envia outro session.ready com novo turn_id; o ESP limpa o ring anterior, retoma escuta e envia novo input.ready. Começa uma contagem inteira nova. A captura I2S permanece compartilhada e ativa; transmitir silêncio em escuta não equivale a capturar um enunciado em andamento.

Ao expirar, input.stop interrompe a entrada e aguarda input.end; em seguida session.end(result=inactivity) e fechamento ordenado. Não sintetiza IDLE_MESSAGE antes de fechar esse WS. Despedida confirmada, cancelamento e falha também encerram. CONVERSATION_TIMEOUT/IDLE_MESSAGE continuam disponíveis nos fluxos locais/legados.

ESP_CONTINUATION_SECONDS e ESP_FIRST_SPEECH_SECONDS foram removidos do caminho v1. INTERACTION_TIMEOUT_SECONDS protege cada turno ativo a partir da fala; não limita a idade da conexão ou a espera ociosa. O ESP não tem cronômetro duplicado, teto total de180s ou configuração local de10/20s. Quedas/erros de transporte, abertura e ping/pong conservam recuperação sem replay.

Atualização inicial coordenada: instalar este servidor e compilar/gravar o firmware com conversation_events/input.ready/input.speech_started. Cliente anterior não satisfaz o contrato ampliado; não usar legado como bypass. Depois dessa única atualização, altere somente no .env do Python:

~~~dotenv
CONVERSATION_IDLE_TIMEOUT_SECONDS=20
~~~

Reinicie o serviço/processo Python. Nenhuma mudança ou novo build/flash do ESP é necessária para trocar10 por20. Valor finito positivo entre0,1 e3600s; padrão10.

## Retorno de áudio ao MAX98357A

Montagem confirmada pelo usuário: MAX98357A BCLK15/WS16/DIN17, USB5V e GND comum, SD/GAIN desconectados, som real no teste isolado. O firmware privado já habilita JUVENAL_SPEAKER_ENABLED e esses GPIOs; não remapear microfone/tela/LED. O exemplo público continua opt-in. No servidor, escolha AUDIO_OUTPUT_MODE=esp32 e TTS_BACKEND=piper; configure modelo e instale o perfil esp32. Nenhuma dessas escolhas valida eletricamente a montagem.

O servidor converte a taxa nativa do Piper para PCM16 mono 16 kHz com continuidade entre chunks e saturação. Blocos de 640 bytes/320 amostras/20 ms; último pode ser menor e par. output.end traz totais depois da conversão. O servidor não usa player local nem envia ao webhook nesse modo. Entrada local/textual sem vínculo com um cliente apto recebe indisponibilidade.

O ESP anuncia playback somente com TX configurado/inicializado. I2S_NUM_0 RX continua exclusivo do MicroWakeWord; I2S_NUM_1 TX duplica mono nos slots estéreo. Buffer interno de 16 KiB, início após seis blocos ou EOF, créditos de consumo e ACK depois da saída real dos descritores DMA. Underrun detectado, erro ou timeout não gera confirmação fictícia. Áudio isolado da montagem já comprovado pelo usuário; resposta PCM/WS do assistente e estabilidade integrada ainda não comprovadas.

## Migração do legado

O antigo contrato action=wake/start_audio/stop_audio foi movido para /ws/esp/legacy. Exige ESP_LEGACY_ENABLED=true e Bearer; desligado por padrão. Não aceita envelope v1 por heurística, não oferece playback e não é usado pelo novo firmware. Exemplos Arduino históricos precisam de URL/header adaptados e não são o firmware principal. Não importar suas configurações QIO/PSRAM 80 MHz: a montagem atual usa Flash DIO 80 MHz e PSRAM octal 40 MHz.

## Avaliação manual

Primeiro reinicie o servidor e confira /health; depois compile/grave o principal pelo ESP-IDF 5.4.4. Observe wake → conexão → fala/transcrição → resposta → nova escuta na mesma conexão → inatividade configurada → WS fechado/wake. Confira nova wake, contexto dentro do TTL, painel observador, erro de conexão sem travamento e diagnóstico WAV separado. A chamada ao webhook já configurado pode produzir efeito externo: não repetir automaticamente falhas de resultado desconhecido.

A montagem MAX98357A já foi confirmada; o aceite de resposta integrada exige rodada separada: formato/velocidade, totais, drenagem, silêncio e recuperação. Linux/systemd tem [guia próprio](homelab-linux-setup.md). Nenhum desses cenários foi executado pelo agente.

## Diagnóstico de entrada e inatividade

A falha capture_limit da primeira versão WS vinha do buffer linear do WAV e foi corrigida com ring produtor/consumidor. Em overflow real, o ESP informa input_buffer_overflow e não sobrescreve PCM pendente. Logs do ESP contam capturadas/enviadas/pendentes; RMS bruto do mic difere do RMS tratado pelo HPF/ganho no servidor. Logs do Python mostram início/fim de fala e progresso PCM/VAD, sem áudio bruto. Não alterar parâmetros de voz sem medir o caso real.

A cada input.ready, o Python registra o valor de CONVERSATION_IDLE_TIMEOUT_SECONDS. input.speech_started registra cancelamento da inatividade. Expiração registra conversation_idle_timeout, para a entrada e encerra ordenadamente sem anúncio TTS. Alterar apenas o .env e reiniciar é suficiente após o firmware inicial coordenado.
