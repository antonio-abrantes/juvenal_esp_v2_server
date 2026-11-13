# Guia Completo: Wake Word — Conceito, Modelos Prontos e Treino Personalizado

## Índice

1. [O que é uma Wake Word?](#1-o-que-é-uma-wake-word)
2. [Como a Wake Word funciona neste projecto](#2-como-a-wake-word-funciona-neste-projecto)
3. [Opção A — Usar um modelo já pronto do repositório](#3-opção-a--usar-um-modelo-já-pronto-do-repositório)
4. [Opção B — Solicitar um novo modelo ao repositório](#4-opção-b--solicitar-um-novo-modelo-ao-repositório)
5. [Opção C — Treinar o seu próprio modelo](#5-opção-c--treinar-o-seu-próprio-modelo)
6. [Como usar o modelo no projecto](#6-como-usar-o-modelo-no-projecto)
7. [Ajustar a sensibilidade](#7-ajustar-a-sensibilidade)
8. [Dicas e resolução de problemas](#8-dicas-e-resolução-de-problemas)

---

## 1. O que é uma Wake Word?

Uma **wake word** (também chamada de *hotword* ou *palavra de activação*) é uma palavra ou frase curta que o assistente fica continuamente à escuta, mesmo quando está "dormindo" — ou seja, sem processar nada.

Só quando detecta essa palavra específica é que o assistente "acorda" e começa a ouvir o que o utilizador quer dizer.

### Exemplos do dia-a-dia

| Assistente | Wake Word |
|---|---|
| Amazon Alexa | "Alexa" |
| Google Assistant | "Ok Google" |
| Apple Siri | "Hey Siri" |
| Este projecto | "Juvehnal" |

### Por que existe esse mecanismo?

Sem wake word, o assistente teria de estar constantemente a transcrever e processar tudo o que ouve — o que consumiria CPU, bateria e enviaria áudio desnecessário para processamento. A wake word resolve isso elegantemente: é um modelo muito leve (~12KB no caso do microWakeWord) que roda localmente no próprio dispositivo em tempo real, com custo mínimo de CPU, e só aciona o pipeline pesado (ASR + LLM + TTS) quando necessário.

### Como é detectada tecnicamente

A detecção não é feita por comparação de texto. O áudio do microfone é convertido em características espectrais a cada 10ms (semelhante a um espectrograma Mel com supressão de ruído), e uma rede neural pequena analisa essa sequência de características para determinar a probabilidade de a wake word ter sido dita. Se essa probabilidade ficar consistentemente acima de um limiar (ex: 0.97) por vários frames consecutivos, o sistema considera que a palavra foi detectada.

---

## 2. Como a Wake Word funciona neste projecto

O projecto usa a biblioteca **microWakeWord** da Open Home Foundation com modelos no formato **TFLite** (TensorFlow Lite), que são ficheiros `.tflite` — modelos de redes neurais quantizados e optimizados para rodar em hardware modesto, incluindo ARM (Raspberry Pi, Orange Pi, etc.).

### Pipeline completo

```
Microfone (16kHz) → MicroFrontend (40 features/10ms) → Modelo TFLite → Probabilidade
       ↓
  Probabilidade > threshold por N frames consecutivos?
       ↓ SIM
  🔔 Wake word detectada → ASR → LLM → TTS
```

### Ficheiros relevantes no projecto

| Ficheiro | Função |
|---|---|
| `models/juvehnal.tflite` | Modelo da wake word actual |
| `server/wake_word.py` | Código que carrega e executa o modelo |
| `.env` → `WAKE_WORD_MODEL` | Caminho para o modelo |
| `.env` → `WAKE_WORD_THRESHOLD` | Sensibilidade (padrão: 0.97) |

O modelo `.tflite` é o único ficheiro que precisa de ser substituído para trocar a wake word. O código em `wake_word.py` é genérico e funciona com qualquer modelo microWakeWord compatível.

---

## 3. Opção A — Usar um modelo já pronto do repositório

O repositório **[TaterTotterson/microWakeWords](https://github.com/TaterTotterson/microWakeWords)** contém dezenas de modelos já treinados e prontos para usar. Alguns exemplos disponíveis:

`alexa`, `alfred`, `computer`, `einstein`, `gandalf`, `gemini`, `hello_computer`, `hey_alexa`, `hey_alfred`, `hercules`, `claude`, `cortana`, `echo`, `emily`, e muitos outros nomes próprios em vários idiomas.

> **Nota:** A maioria dos modelos é em inglês. Para português, as melhores opções são nomes próprios curtos que o TTS em inglês pronuncia de forma reconhecível, ou solicitar um novo modelo (ver Opção B).

### Passo a passo

**1. Abrir a lista de modelos disponíveis**

Acesse: https://github.com/TaterTotterson/microWakeWords/tree/main/microWakeWords

Vai ver uma lista de pares de ficheiros `.tflite` e `.json` para cada wake word disponível.

**2. Escolher o modelo desejado**

Clique no ficheiro `.tflite` da wake word que quer usar. Por exemplo, para `computer`:

```
https://github.com/TaterTotterson/microWakeWords/blob/main/microWakeWords/computer.tflite
```

**3. Descarregar o ficheiro `.tflite`**

Na página do ficheiro no GitHub, clique no botão **"Download raw file"** (ícone de download no canto superior direito do visualizador de ficheiros).

**4. Copiar para a pasta `models/` do projecto**

```
projeto/
└── models/
    ├── juvehnal.tflite   ← modelo actual
    └── computer.tflite   ← novo modelo descarregado
```

**5. Atualizar o `.env`**

```properties
WAKE_WORD_MODEL=./models/computer.tflite
```

**6. Reiniciar o servidor**

```bash
python server/main.py
```

O log deve mostrar o novo modelo a carregar:

```
microWakeWord tflite carregado: .../models/computer.tflite
```

---

## 4. Opção B — Solicitar um novo modelo ao repositório

Se a wake word que quer não existe na lista, pode **pedir ao TaterTotterson para treinar** — é gratuito e normalmente leva poucos dias.

### Passo a passo

**1. Ir para a aba de Issues do repositório**

https://github.com/TaterTotterson/microWakeWords/issues

**2. Clicar em "New issue"**

**3. Definir o título no formato:**

```
mww: juvehnal
```

Exemplos de títulos válidos:
```
mww: hey juvehnal
mww: assistente
mww: jarvis
```

**4. Submeter sem corpo de texto** — não é necessário mais nada.

**5. Aguardar**

Quando o modelo estiver pronto, o issue será comentado e fechado. Os ficheiros `.tflite` e `.json` serão adicionados ao repositório. Depois é só seguir o Passo 3, 4, 5 e 6 da Opção A.

### ⚠️ Dica importante sobre pronúncia

O treinamento usa TTS (text-to-speech) para gerar amostras sintéticas da palavra. Se a pronúncia do TTS (em inglês) não for próxima do que você vai dizer, o modelo vai funcionar mal.

**Exemplo:** "Juvehnal" foi escrito assim propositalmente — o TTS em inglês lê "Juvehnal" de forma parecida com como um brasileiro diria "Juvenal".

Se quiser um nome em português, experimente primeiro num TTS online em inglês (ex: https://ttsmp3.com) e ajuste a grafia até a pronúncia soar correcta. Use essa grafia ajustada no título do issue.

---

## 5. Opção C — Treinar o seu próprio modelo

Esta é a opção mais avançada. O repositório original do microWakeWord é o **[OHF-Voice/micro-wake-word](https://github.com/OHF-Voice/micro-wake-word)**.

> ⚠️ **Aviso:** O próprio repositório adverte que treinar um modelo utilizável é difícil e requer experimentação. Esta opção é para utilizadores avançados. Para uso doméstico, a Opção B (solicitar ao TaterTotterson) é muito mais prática.

### Pré-requisitos

- Python 3.10 ou 3.11
- GPU NVIDIA com CUDA (recomendado — treino em CPU é muito lento)
- ~8GB de RAM
- ~10GB de espaço em disco (datasets de treino)
- Conta no Hugging Face (para descarregar datasets negativos)

### Passo a passo

**1. Clonar o repositório de treino**

```bash
git clone https://github.com/OHF-Voice/micro-wake-word.git
cd micro-wake-word
```

**2. Criar ambiente virtual e instalar dependências**

```bash
python -m venv venv
source venv/bin/activate        # Linux/macOS
# ou: venv\Scripts\activate     # Windows

pip install -e .
```

**3. Instalar o gerador de amostras Piper**

O treino usa o [piper-sample-generator](https://github.com/rhasspy/piper-sample-generator) para criar amostras sintéticas da sua wake word com vários sotaques e entonações.

```bash
pip install piper-sample-generator
```

**4. Gerar amostras positivas da sua wake word**

Substitua `"juvenal"` pelo nome que quer usar:

```bash
python -m piper_sample_generator \
  --model pt_BR-faber-medium \
  --text "juvenal" \
  --max-samples 10000 \
  --output-dir ./samples/positive/juvenal
```

> Use uma voz em português para melhor generalização com o seu sotaque.

**5. Descarregar datasets negativos do Hugging Face**

Os datasets negativos são amostras de áudio com fala genérica, música e ruído ambiente — usados para ensinar o modelo a ignorar tudo o que não é a wake word. Estão disponíveis em:

https://huggingface.co/datasets/kahrendt/microwakeword

Siga as instruções do repositório para descarregar os conjuntos `train`, `validation` e `test`.

**6. Abrir e executar o notebook de treino**

```bash
jupyter notebook notebooks/basic_training_notebook.ipynb
```

No notebook, configure:
- Nome da wake word
- Caminho para as amostras positivas geradas
- Caminho para os datasets negativos
- Hiperparâmetros (epochs, learning rate, thresholds)

Execute todas as células. O processo demora de minutos (GPU) a horas (CPU).

**7. Exportar o modelo para TFLite**

O notebook inclui uma célula de exportação que gera o ficheiro `.tflite` quantizado. Após a execução, o ficheiro estará em algo como:

```
micro-wake-word/trained_models/juvenal/juvenal.tflite
```

**8. Testar o modelo antes de usar**

O repositório inclui um script de teste:

```bash
python -m microwakeword.test_model \
  --model ./trained_models/juvenal/juvenal.tflite \
  --test-samples ./samples/positive/juvenal
```

Só avance para o passo seguinte se os resultados forem satisfatórios (baixo número de falsos positivos e falsos negativos).

---

## 6. Como usar o modelo no projecto

Independentemente de como obteve o ficheiro `.tflite` (Opção A, B ou C), os passos para integrá-lo são sempre os mesmos.

**1. Copiar o ficheiro `.tflite` para a pasta `models/`**

```
projeto/
└── models/
    └── meu_modelo.tflite
```

**2. Editar o `.env` na raiz do projecto**

```properties
WAKE_WORD_MODEL=./models/meu_modelo.tflite
```

O caminho é relativo à pasta `server/` (onde o servidor arranca). Se preferir, use o caminho absoluto:

```properties
WAKE_WORD_MODEL=/home/usuario/juvenal-bot/project/models/meu_modelo.tflite
```

**3. Reiniciar o servidor**

```bash
# Com venv activo
python server/main.py
```

**4. Confirmar no log que o modelo foi carregado**

```
microWakeWord tflite carregado: .../models/meu_modelo.tflite
  Input:  shape=[ 1  2 40] dtype=<class 'numpy.int8'>
  Output: dtype=<class 'numpy.uint8'>, zp=0
  Threshold: 0.97
Warmup completo (40 invocacoes). Detecao ativa!
```

Se este log aparecer, o modelo está a correr correctamente.

---

## 7. Ajustar a sensibilidade

O parâmetro `WAKE_WORD_THRESHOLD` no `.env` controla o equilíbrio entre **sensibilidade** e **falsos positivos**:

```properties
WAKE_WORD_THRESHOLD=0.97
```

| Valor | Comportamento |
|---|---|
| `0.99` | Muito rigoroso — poucas activações falsas, mas pode falhar em detectar |
| `0.97` | Padrão recomendado — bom equilíbrio |
| `0.90` | Mais sensível — detecta melhor em ruído, mas mais activações falsas |
| `0.80` | Muito sensível — pode activar com palavras similares |

Ajuste este valor aos poucos (incrementos de 0.01-0.02) até encontrar o comportamento ideal para o seu ambiente e microfone.

---

## 8. Dicas e resolução de problemas

### Wake word não é detectada

- Verifique se o microfone correcto está seleccionado (`AUDIO_INPUT_DEVICE` no `.env`)
- Tente reduzir o `WAKE_WORD_THRESHOLD` para `0.90` e teste novamente
- Fale mais próximo do microfone e com dicção clara
- Se usou Opção C, o modelo pode precisar de mais amostras de treino ou re-treino

### Demasiadas activações falsas (acorda sozinho)

- Aumente o `WAKE_WORD_THRESHOLD` para `0.98` ou `0.99`
- O parâmetro `CONSECUTIVE_DETECTIONS` em `server/wake_word.py` (padrão: 3) exige que o modelo detecte a palavra em 3 frames consecutivos — este mecanismo já reduz bastante os falsos positivos

### Modelo não carrega (erro no log)

- Confirme que o ficheiro `.tflite` existe no caminho especificado no `.env`
- Confirme que o `ai-edge-litert` está instalado no ambiente virtual: `pip show ai-edge-litert`
- O modelo deve ter formato microWakeWord (input shape `[1, 2, 40]`) — modelos de outros frameworks não são compatíveis

### Como verificar os dispositivos de áudio disponíveis

```bash
python -c "import sounddevice; print(sounddevice.query_devices())"
```
