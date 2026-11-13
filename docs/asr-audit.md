# Relatório de Auditoria ASR — Projeto Juvehnal

**Data:** Março 2026  
**Escopo:** `server/transcriber.py`, `server/main.py`, `.env`  
**Objetivo:** Auditar e otimizar o pipeline faster-whisper para máxima performance em hardware modesto (incluindo Linux headless/servidor)

---

## Resumo

O pipeline ASR estava bem estruturado nos fundamentos (modelo singleton, sample rate correto, VAD ativo, language fixo). As melhorias implementadas focam em **parâmetros de transcrição configuráveis via `.env`** e **propagação de contexto entre turnos da conversa** (`initial_prompt`), que reduzem latência e melhoram a qualidade sem trocar de tecnologia.

---

## Pontos auditados

| # | Ponto | Status | Impacto | Ação |
|---|---|---|---|---|
| 1 | Modelo carregado uma única vez | ✅ OK | — | Nenhuma |
| 2 | `language="pt"` fixo | ✅ OK | — | Nenhuma |
| 3 | `int16 → float32` normalizado | ✅ OK | — | Nenhuma |
| 4 | Sample rate 16kHz, mono | ✅ OK | — | Nenhuma |
| 5 | `vad_filter=True` ativo | ✅ OK | — | Nenhuma |
| 6 | `beam_size` hardcoded em 5 | ⚠️ Melhorado | Alto | `WHISPER_BEAM_SIZE=1` no `.env` |
| 7 | `temperature` não definido | ⚠️ Melhorado | Médio | `WHISPER_TEMPERATURE=0.0` |
| 8 | `no_speech_threshold` não definido | ⚠️ Melhorado | Alto | `WHISPER_NO_SPEECH_THRESHOLD=0.7` |
| 9 | `word_timestamps` não explícito | ⚠️ Melhorado | Baixo | `word_timestamps=False` explícito |
| 10 | `initial_prompt` nunca passado | ❌ Implementado | Médio | Propaga texto do turno anterior |
| 11 | `WHISPER_DEVICE` não configurável | ❌ Implementado | Alto | Exposto no `.env` |
| 12 | `WHISPER_COMPUTE_TYPE` não configurável | ❌ Implementado | Alto | Exposto no `.env` |
| 13 | Nenhum parâmetro configurável via `.env` | ❌ Implementado | Alto | Seção completa no `.env` |

---

## Mudanças implementadas

### 1. `beam_size`: 5 → 1 (padrão)

**Antes:**
```python
segments, info = self._model.transcribe(audio_f32, language="pt", beam_size=5, ...)
```

**Depois:**
```python
self._beam_size = int(os.getenv("WHISPER_BEAM_SIZE", "1"))
# ...
segments, info = self._model.transcribe(audio_f32, beam_size=self._beam_size, ...)
```

**Por quê:** `beam_size=5` avalia 5 hipóteses em paralelo. Para uso conversacional em tempo real, a melhoria de qualidade é mínima e o custo de CPU é ~2x maior. `beam_size=1` (greedy decoding) é suficiente para fala em PT-BR com modelo pré-treinado e reduz latência de transcrição significativamente em CPU. Configurável via `.env` para quem preferir mais qualidade com hardware mais forte.

---

### 2. `temperature=0.0`

**Por quê:** `temperature=0.0` desativa a amostragem estocástica, tornando a transcrição determinística e ligeiramente mais rápida. O padrão do faster-whisper inclui fallback para temperaturas maiores quando a compressão é alta — isso foi mantido.

---

### 3. `no_speech_threshold=0.7` (era 0.6 implícito)

**Por quê:** O padrão do faster-whisper é 0.6. Aumentar para 0.7 reduz falsos positivos — o modelo fica mais exigente antes de considerar que "há fala", o que diminui alucinações em silêncio ou ruído ambiente. Configurável via `WHISPER_NO_SPEECH_THRESHOLD` para ajuste fino por microfone.

---

### 4. `word_timestamps=False` (explícito)

**Por quê:** Desabilitar explicitamente timestamps de palavras evita processamento desnecessário. O projeto não usa timestamps — o texto transcrito é suficiente.

---

### 5. `initial_prompt` entre turnos da conversa

**Antes:** cada turno era transcrito de forma completamente independente.

**Depois:** o `conversation_loop` guarda o texto do turno anterior e o passa como `initial_prompt` ao chamar `_listen_once`, que repassa ao `start_session` → `_transcribe`.

**Por quê:** O Whisper usa o `initial_prompt` como contexto para calibrar pronúncia e vocabulário. Em uma conversa onde o usuário já mencionou "Antônio", "Juvehnal" ou termos técnicos específicos, o modelo tende a transcrever esses termos corretamente nos turnos seguintes. Configurável via `WHISPER_CONDITION_ON_PREVIOUS_TEXT=false` para desativar se necessário.

---

### 6. `WHISPER_DEVICE` e `WHISPER_COMPUTE_TYPE` expostos no `.env`

**Antes:** sempre `cpu` e `int8` hardcoded no `main.py`.

**Depois:** lidos do `.env`, passados ao `WhisperTranscriber.__init__`.

**Por quê:** Permite que usuários com GPU NVIDIA mudem para `WHISPER_DEVICE=cuda` e `WHISPER_COMPUTE_TYPE=float16` sem tocar no código.

---

### 7. Seção completa de ASR no `.env`

Documentação inline de todos os modelos disponíveis com recomendações por hardware, incluindo tabela de latências estimadas para CPU e GPU.

---

## Mudanças descartadas

### Silero VAD externo

**Avaliação:** O projeto já usa `vad_filter=True` do faster-whisper com `vad_parameters` customizados (`min_silence_duration_ms=500`, `speech_pad_ms=200`). Além disso, o `transcriber.py` tem um VAD próprio por energia (`ENERGY_THRESHOLD=300`) que evita enviar áudio sem fala ao modelo. Adicionar Silero VAD seria uma terceira camada redundante, com custo de ~100MB de dependência adicional e sem ganho mensurável neste caso de uso.

**Conclusão:** ❌ Descartado — VAD atual é suficiente.

### `compression_ratio_threshold`

**Avaliação:** O faster-whisper usa esse parâmetro internamente para detectar transcrições degeneradas (ex: o modelo "travou" em loop). O valor padrão (2.4) é adequado para fala conversacional normal. Modificar sem dados de calibração específicos pode causar falsos positivos.

**Conclusão:** ❌ Descartado — padrão adequado para o caso de uso.

### `best_of` separado de `beam_size`

**Avaliação:** `best_of` só é relevante quando `temperature > 0` (sampling). Como definimos `temperature=0.0`, `best_of` não tem efeito.

**Conclusão:** ❌ Descartado — sem efeito com `temperature=0.0`.

### Troca de modelo (tiny/small/large)

**Avaliação:** O modelo `base` é o padrão atual. A troca de modelo agora está **totalmente configurável via `.env`** (`WHISPER_MODEL_SIZE`), com documentação inline de cada opção. Não foi trocado automaticamente pois depende do hardware de cada usuário.

**Conclusão:** ✅ Decisão delegada ao usuário via `.env`.

---

## Guia de configuração por hardware

```properties
# ─── Hardware muito fraco (Raspberry Pi 4, mini PCs ARM, 2GB RAM) ───
WHISPER_MODEL_SIZE=tiny
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_BEAM_SIZE=1

# ─── Hardware modesto (Intel N100, Celeron, AMD E-series, 4GB RAM) ──
WHISPER_MODEL_SIZE=base       # ← padrão atual
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_BEAM_SIZE=1

# ─── Hardware médio (Core i5/i7, Ryzen 5, 8GB RAM) ──────────────────
WHISPER_MODEL_SIZE=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
WHISPER_BEAM_SIZE=1           # ou 2 se quiser mais qualidade

# ─── PC com GPU NVIDIA (4GB+ VRAM) ──────────────────────────────────
WHISPER_MODEL_SIZE=large-v3-turbo   # melhor custo-benefício
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
WHISPER_BEAM_SIZE=5           # com GPU, beam_size alto tem custo baixo
```

---

## Arquivos modificados

| Arquivo | Mudança |
|---|---|
| `server/transcriber.py` | Parâmetros configuráveis via env, `initial_prompt`, `word_timestamps=False` |
| `server/main.py` | `WHISPER_DEVICE`/`COMPUTE_TYPE` lidos do env; `initial_prompt` propagado entre turnos; `_listen_once` aceita `initial_prompt` |
| `.env` | Seção ASR expandida com todos os parâmetros, modelos disponíveis e recomendações por hardware |

---

## Viabilidade em Linux headless/servidor

Todos os componentes do pipeline ASR são 100% compatíveis com Linux sem interface gráfica:

- `faster-whisper` roda em CPU ou GPU, sem dependência de display
- `sounddevice` usa PortAudio que funciona via ALSA/PulseAudio headless
- Os novos parâmetros não adicionam nenhuma dependência nova
- Para hardware muito fraco (ex: VPS com 2 vCPU/2GB), usar `tiny` + `beam_size=1` mantém a aplicação funcional com latência de ~1-2s por transcrição

Ver `docs/homelab-linux-setup.md` para o guia completo de instalação em Linux.
