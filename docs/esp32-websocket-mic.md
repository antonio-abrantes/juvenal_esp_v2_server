# ESP32-S3 como Microfone via WebSocket

Guia para usar o ESP32-S3 como interface de microfone wireless para o servidor Juvehnal, substituindo o microfone USB local por um dispositivo I2S conectado via WebSocket na rede local.

---

## Visão Geral

```
INMP441 (I2S) → ESP32-S3 → Wi-Fi → WebSocket /ws/esp → Servidor Python
                                                              ↓
                                               faster-whisper (ASR) → LLM → TTS
```

O ESP32-S3 lê áudio do microfone INMP441 via I2S, detecta atividade de voz localmente (energia RMS simples) e envia a palavra de ativação ao servidor via WebSocket. O servidor responde pedindo o stream de áudio, que o ESP32 envia como frames binários PCM int16 a 16kHz. O servidor transcreve com faster-whisper e segue o pipeline normal.

---

## Hardware

| Componente     | Modelo                  | Notas                            |
|---------------|-------------------------|----------------------------------|
| Microcontrolador | ESP32-S3 DevKitC-1   | PSRAM octal 80MHz recomendado    |
| Microfone     | INMP441                 | I2S digital, omnidirecional      |

### Pinagem

| INMP441 | ESP32-S3 GPIO | Função         |
|---------|--------------|----------------|
| SD      | GPIO 10      | I2S Data In    |
| WS      | GPIO 11      | I2S LR Clock   |
| SCK     | GPIO 12      | I2S Bit Clock  |
| VDD     | 3.3V         |                |
| GND     | GND          |                |
| L/R     | GND          | Seleciona canal esquerdo |

> **Nota**: L/R do INMP441 ligado ao GND coloca o microfone no canal esquerdo (I2S_STD_SLOT_LEFT), conforme configuração no código.

---

## Configuração do Arduino IDE

### 1. Placa

- Instale o pacote **ESP32** da Espressif via Boards Manager
- Versão mínima: **3.0.0** (necessária para `driver/i2s_std.h`)
- Selecione: `Tools → Board → ESP32S3 Dev Module`
- Configurações recomendadas:
  - `PSRAM`: OPI PSRAM
  - `CPU Frequency`: 240MHz
  - `Flash Mode`: QIO 80MHz
  - `Partition Scheme`: Default 4MB with spiffs

### 2. Bibliotecas necessárias

Instale via **Library Manager** (`Tools → Manage Libraries`):

| Biblioteca          | Autor / Repositório            | Versão mínima |
|--------------------|-------------------------------|---------------|
| WebSockets_Generic | khoih-prog/WebSockets_Generic | 2.16.1        |
| ArduinoJson        | bblanchon/ArduinoJson         | 7.x           |

> `driver/i2s_std.h` já está incluída no ESP32 Arduino Core — não precisa instalar separadamente.

---

## Arquivo do Projeto

O sketch está em `esp-project/esp32_ws_mic/esp32_ws_mic.ino`.

### Configurações obrigatórias antes de compilar

Edite as seguintes constantes no início do arquivo:

```cpp
#define WIFI_SSID   "SEU_SSID"
#define WIFI_PASS   "SUA_SENHA"
#define SERVER_HOST "192.168.1.100"   // IP do servidor Python na LAN
#define SERVER_PORT 8000
```

---

## Protocolo WebSocket

### Endpoint do servidor

```
ws://<SERVER_HOST>:8000/ws/esp
```

### Mensagens ESP32 → Servidor

| Tipo    | Conteúdo                              | Quando                              |
|---------|---------------------------------------|-------------------------------------|
| JSON    | `{"action": "wake"}`                  | Energia de voz detectada localmente |
| JSON    | `{"action": "ping"}`                  | Resposta ao ping do servidor        |
| JSON    | `{"action": "stop_audio"}`            | Timeout local de streaming          |
| Binário | Bytes PCM int16 little-endian 16kHz   | Enquanto servidor pede áudio        |

### Mensagens Servidor → ESP32

| Tipo | Conteúdo                    | Significado                          |
|------|-----------------------------|--------------------------------------|
| JSON | `{"event": "start_audio"}`  | Começa a enviar frames PCM           |
| JSON | `{"event": "stop_audio"}`   | Para de enviar áudio                 |
| JSON | `{"event": "ping"}`         | Keep-alive (responder com ping)      |
| JSON | `{"event": "pong"}`         | Resposta ao ping do ESP              |
| JSON | `{"event": "esp_connected"}`| Broadcast — ESP conectou             |
| JSON | `{"event": "conversation_start"}` | Conversa iniciada              |
| JSON | `{"event": "conversation_end", "turns": N}` | Conversa encerrada    |

---

## Fluxo de Estados do ESP32

```
IDLE ──(energia > limiar por ≥300ms)──▶ envia {"action":"wake"}
  │                                              │
  │                                              ▼
  │                                         WAIT_START
  │                                              │
  │                               recebe {"event":"start_audio"}
  │                                              │
  │                                              ▼
  │                                         STREAMING ──(timeout ou stop_audio)──▶ IDLE
  │                                              │
  └──────────────────────────────────────────────┘
```

---

## Ajuste de Parâmetros

### Limiar de energia (`ENERGY_THRESHOLD`)

| Valor | Comportamento                                  |
|-------|------------------------------------------------|
| 400   | Muito sensível — detecta respiração e ruídos   |
| 800   | Padrão — sala silenciosa ou semi-silenciosa    |
| 1500  | Conservador — requer voz clara e próxima       |
| 2500+ | Requer fala em volume alto                     |

### Duração mínima de fala (`WAKE_SPEECH_MIN_MS`)

Evita disparos por ruídos curtos. O padrão de 300ms equivale a uma sílaba. Aumente para 500ms se houver falsos positivos.

### Timeout de streaming (`STREAM_TIMEOUT_MS`)

Tempo máximo que o ESP32 transmite áudio em uma sessão (padrão: 15 segundos). Se o servidor encerrar antes via `stop_audio`, o estado volta para IDLE imediatamente.

---

## Observações de Formato de Áudio

- **Taxa de amostragem**: 16000 Hz (requerida pelo faster-whisper)
- **Formato I2S**: 32 bits Philips standard, mono canal esquerdo
- **Conversão**: `int16 = raw32 >> 14` (mesmo shift do `audio_recorder.h` de referência)
- **Formato enviado via WebSocket**: PCM int16 little-endian, sem cabeçalho
- **Tamanho dos frames**: 256 amostras × 2 bytes = 512 bytes por frame (~16ms de áudio)

---

## Troubleshooting

### ESP32 não conecta ao Wi-Fi
- Verifique SSID e senha
- O ESP32-S3 suporta apenas redes 2.4GHz

### WebSocket não conecta
- Confirme que o servidor está rodando (`python server/main.py`)
- Confirme o IP do servidor (`SERVER_HOST`) — use `ip addr` ou `ipconfig` na máquina do servidor
- Verifique que a porta 8000 está acessível na rede local (sem firewall bloqueando)

### Sem áudio / ASR retorna vazio
- Verifique a pinagem física (GPIO10/11/12 conforme tabela)
- Confirme que L/R do INMP441 está conectado ao GND
- Aumente `ENERGY_THRESHOLD` se o ESP disparar `wake` mas não houver fala real
- Monitor serial mostrará `▶️ Servidor pronto — iniciando stream de áudio` quando o pipeline ativar

### Transcrição com ruído / alucinações
- O servidor já possui detecção de alucinação por tokens (descarta se <40% tokens únicos)
- Reduza o ruído ambiente ou aumente `ENERGY_THRESHOLD`
- Verifique que o microfone está a ≤50cm do falante

### Wake word disparando sem voz
- Aumente `ENERGY_THRESHOLD` (ex: 1200 → 2000)
- Aumente `WAKE_SPEECH_MIN_MS` (ex: 300 → 600)
- Adicione isolamento acústico ou afaste o ESP32 de fontes de ruído

---

## Integração com o Servidor

O endpoint `/ws/esp` em `server/main.py` foi adicionado ao servidor existente. Não são necessárias alterações de configuração — o endpoint coexiste com o `/ws` original (usado pelo frontend web).

Ao conectar o ESP32, o servidor emite `{"event": "esp_connected"}` para todos os clientes WebSocket, incluindo o frontend, que pode exibir o status da conexão do dispositivo físico.
