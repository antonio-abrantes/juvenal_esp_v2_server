# Comparação manual de transcrição: Whisper e Parakeet PT-BR

O padrão continua `ASR_BACKEND=whisper`, usando os valores `WHISPER_*` existentes. O candidato é `ASR_BACKEND=parakeet`: modelo `alefiury/parakeet-tdt-v3-ptBR-30s-onnx`, INT8, CPU, via `onnx-asr`. O modelo é carregado uma vez por processo. Não há troca automática para outro backend em caso de erro.

Há evidência posterior de uso real no Windows e no Orange Pi/RK3566, com melhora relatada pelo usuário; veja a seção 6. Isso ainda não substitui a comparação controlada com os mesmos WAVs. O VAD de captura, silêncio, WebSocket, firmware, LLM, ferramentas e TTS permanecem com o comportamento existente. O Whisper preserva seu VAD interno e demais parâmetros; o Parakeet recebe o mesmo PCM inteiro, até 30 segundos, sem outro VAD, prompt de contexto ou seleção de idioma. São pipelines ASR diferentes, identificados nos resultados.

## 1. Preparar no Windows

Executar na raiz `C:\Projects\Juvenal_v2`. Usar o Python da venv do servidor, mesmo se o terminal estiver no ambiente ESP-IDF.

Instalar **somente quando for experimentar o Parakeet**:

```powershell
& .\projects\juvenal_server\.venv\Scripts\python.exe -m pip install -r .\projects\juvenal_server\requirements-asr-parakeet.txt
```

Não é necessário reinstalar o Whisper. A instalação acrescenta o backend opcional e pode ajustar dependências compartilhadas conforme o resolvedor do pip; não instala PyTorch/NeMo. Nenhum pacote/modelo foi instalado pelo agente. Para isolamento absoluto das dependências, usar outra venv com `requirements-core.txt`, `requirements-asr.txt` e `requirements-asr-parakeet.txt` para o replay, sem mexer na venv do serviço Orange Pi.

A primeira inicialização do Parakeet baixa os arquivos do Hugging Face para o cache do usuário (ou `HF_HOME`, se configurado). Os pesos INT8 têm aproximadamente 0,67 GB; isso não é uma medida de RAM. Aguardar `ASR pronto: backend=parakeet` e o término do startup. Download e carregamento são registrados separadamente da inferência.

## 2. Coletar a baseline com o ESP

O ESP deve apontar para o servidor Windows. Só um processo pode ocupar a porta 8000.

```powershell
& .\projects\juvenal_server\scripts\start.ps1 -AsrBackend whisper -AsrDiagnostics
```

`-AsrDiagnostics` habilita deliberadamente salvamento de **áudio e transcrições**. As opções do comando valem para esse processo, sobrepõem o `.env` e são restauradas ao sair do script. O `.env` real não é reescrito.

Cada execução cria `JUVENAL_DATA_DIR/asr-comparison/<run>/results.jsonl` e `audio/*.wav`. Por padrão, `JUVENAL_DATA_DIR=data`, dentro do projeto. O log informa `<run>`; cada WAV começa pelo número da amostra. `data/` e `*.wav` já são ignorados pelo Git. Se mudar `JUVENAL_DATA_DIR`, mantenha o destino privado e fora do versionamento. Os arquivos ficam no disco até remoção manual; desligue a coleta quando terminar e compartilhe apenas os resultados necessários, sem segredos.

Antes de cada frase, aguardar o estado de escuta. Dizer “Juvenal” somente quando estiver fora da conversa. Manter distância, volume de voz, ambiente e microfone iguais. Anotar a frase **realmente falada**, o número da amostra e qualquer hesitação. Usar a frase 00 como aquecimento; não incluir essa amostra na avaliação de latência ao vivo.

| ID | Frase para falar | O que conferir |
|---|---|---|
| 00 | Testando a gravação de voz na sala. | Aquecimento e frase simples |
| 02 | Qual é a previsão do tempo para amanhã em Sousa, na Paraíba? | Cidade, estado e pergunta |
| 03 | A luz da suíte master está acesa? | Cômodo que apresentou erros |
| 04 | Não é a cozinha, é a sala. | Preservação da negação e correção |
| 05 | São vinte e três graus e a umidade está em sessenta por cento. | Números e unidades |
| 06 | Amanhã às sete e meia eu tenho uma reunião. | Horário e temporalidade |
| 07 | Eu estava na sala, mas agora estou no quarto. | Troca de cômodo |
| 08 | O ventilador do escritório está fazendo muito barulho. | Equipamento e cômodo |
| 09 | Quero uma resposta curta sobre a diferença entre memória e armazenamento. | Fala mais longa |

Ajuste informado pelo usuário após a coleta inicial: a frase 01 não foi falada e foi retirada da comparação. Os demais IDs foram mantidos para preservar a referência original; a frase 00 deve ser falada sem “Estou”.

A coleta ao vivo mantém o fluxo normal: pode consultar IA/ferramentas e produzir áudio. Para avaliar comandos reais, opcionalmente acrescentar “Ligue a luz da suíte master” e “Desligue a luz da cozinha”, sabendo que eles **podem executar ações** se o módulo estiver habilitado. O replay abaixo não executa nenhuma ação.

Parar o servidor com `Ctrl+C` antes da comparação offline para evitar disputa de CPU/RAM. Não alterar o VAD, o silêncio, o beam, o ganho ou a persona entre as rodadas.

## 3. Comparar exatamente as mesmas gravações

No PowerShell, indicar a pasta `audio` da coleta, usando o identificador mostrado no log:

```powershell
$audiosAsr = 'C:\Projects\Juvenal_v2\projects\juvenal_server\data\asr-comparison\SUBSTITUA_PELO_RUN\audio'
& .\projects\juvenal_server\.venv\Scripts\python.exe .\projects\juvenal_server\scripts\replay_asr.py --backend whisper --input-dir $audiosAsr
& .\projects\juvenal_server\.venv\Scripts\python.exe .\projects\juvenal_server\scripts\replay_asr.py --backend parakeet --input-dir $audiosAsr
```

Executar um comando e aguardar terminar antes do seguinte. Não é necessário iniciar `main.py`. A ferramenta carrega apenas um modelo, faz um aquecimento com o primeiro WAV e depois três repetições de cada gravação. Não abre microfone, servidor ou conexão com o ESP; não importa o cliente LLM, ferramentas ou TTS. Pode acessar o Hugging Face para obter o modelo. WAVs inválidos, estéreo, outra frequência ou acima de 30 s são recusados, sem conversão ou truncamento silencioso.

Cada rodada grava seu próprio `results.jsonl` privado e imprime um resumo. `--repetitions 5` permite mais repetições. `--timeout 120` é o prazo padrão por WAV **somente no replay**; não altera os prazos da aplicação. O campo `audio_sha256` deve coincidir nos dois modelos para confirmar que a gravação é a mesma. O aquecimento tem `warmup=true` e não entra no resumo.

## 4. Ler os resultados

Exemplo **ilustrativo**, não resultado medido:

```text
ASR backend=whisper amostra=2 audio_id=... audio_s=4.50 asr_ms=5500 rtf=1.222 status=ok primeira=False
ASR backend=parakeet amostra=2 audio_id=... audio_s=4.50 asr_ms=... rtf=... status=ok primeira=False
```

- `asr_ms`: conversão/preprocessamento do modelo, inferência e decodificação concluída. Exclui captura, espera por silêncio, download/carregamento, escrita de diagnósticos, LLM, ferramentas e TTS. No Whisper, inclui seu VAD interno.
- `rtf`: tempo de ASR dividido pela duração do áudio recebido. Menor é mais rápido; abaixo de 1 significa processar em menos tempo que a duração do clipe. Não confundir com RTFx, que é o inverso.
- `first_inference`: identifica a primeira chamada, que pode custar mais. `load_ms` está no evento `model_loaded` e pode incluir download/importação na primeira inicialização.
- `status=empty`: nenhum texto útil retornado, não prova qualidade. `error`/`cancelled` não são resultados válidos de velocidade. O replay interrompe em erro, sem gerar resumo de sucesso.
- `rms`/`peak`: nível do PCM capturado, para detectar variação grosseira de volume, sem normalizar ou alterar a gravação.
- Transcrição em `transcript`, quando `LOG_TRANSCRIPTS=true`; ao vivo, permanece também o log existente de `Transcrição reconhecida`. Os JSONLs registram backend, modelo, opções, versões, sistema/arquitetura e IDs de sessão/turno quando disponíveis.

Para qualidade, conferir nomes, cômodos, números e especialmente “não”. Ignorar só maiúsculas/pontuação não semântica; uma transcrição rápida que troca suíte por sala ou perde negação é pior. Não usar a resposta da IA como prova de transcrição correta, pois ela pode corrigir ou inventar uma interpretação.

Planilha manual sugerida: ID, frase realmente falada, WAV/hash, texto Whisper, texto Parakeet, erro importante em cada um, mediana de `asr_ms` por frase. O resumo global da ferramenta facilita a visão geral; os JSONLs permitem comparar cada frase. Não há nota automática de qualidade.

Primeiro comparar no mesmo Windows. Depois, se o candidato fizer sentido, repetir os mesmos arquivos e comandos Python no Linux/ARM, em ambiente preparado para comparação. O resultado do PC não garante vantagem no Orange Pi; conferir memória disponível e impacto nos serviços existentes. Não instalar pacotes nem substituir o serviço Orange Pi automaticamente.

## 5. Experimentar a conversa completa ou voltar ao padrão

Depois do replay, para conversar pelo ESP com o candidato:

```powershell
& .\projects\juvenal_server\scripts\start.ps1 -AsrBackend parakeet -AsrDiagnostics
```

Para voltar à baseline, sem coleta de arquivos:

```powershell
& .\projects\juvenal_server\scripts\start.ps1 -AsrBackend whisper
```

Sem argumentos, `start.ps1` respeita o `.env`; ausência de `ASR_BACKEND` significa Whisper. Para escolha permanente, adicionar `ASR_BACKEND=parakeet` ao `.env` privado, após instalar as dependências opcionais. `PARAKEET_MODEL` permite outro checkpoint compatível; `PARAKEET_CPU_THREADS=0` deixa o runtime escolher, sem prometer melhor desempenho. Ajustar threads deve ser uma comparação posterior separada.

Linux usa as mesmas variáveis de ambiente, por exemplo `ASR_BACKEND=parakeet bash scripts/start.sh`, se as dependências já estiverem preparadas. Os instaladores com perfil `auto` selecionam `requirements-asr-parakeet.txt` quando `ASR_BACKEND=parakeet`; na ausência da variável continuam no manifesto Whisper. A imagem Docker existente continua incluindo somente Whisper nesta entrega; este roteiro não solicita imagem/deploy novo.

## 6. Resultado observado no Orange Pi — 15/11/2026

O usuário atualizou sua branch develop, instalou a dependência opcional na venv e selecionou `ASR_BACKEND=parakeet` no `.env`. A unit systemd iniciou no Orange Pi 3B/RK3566/8 GB, Debian 12 ARM64/Python 3.11, com `stt=True` e ferramentas disponíveis. O usuário confirmou que o reconhecimento ficou bem melhor e escolheu manter o Parakeet para uso.

O log registrou nove capturas: uma inicial com `status=empty`, seguida de oito com texto. Nessas oito, o tempo ASR foi de 1.722 a 2.994 ms, média 2.358,125 ms e mediana 2.318,5 ms. Houve oito confirmações de playback do ESP. A inicial vazia retomou a escuta; sem WAV dessa rodada, sua causa não foi determinada. O carregamento inicial registrou 30.539 ms, incluindo obtenção/carregamento do modelo, separado da inferência. Na rodada Linux, `diagnostico=False` e `grava_audio=False`: métricas no journal continuam disponíveis, sem coleta adicional de WAV/JSONL.

Logs anteriores do mesmo host com Whisper base mostraram STT de 4.344, 4.390 e 5.428 ms, em outras falas. Portanto, houve melhora observada de tempo e qualidade percebida, sem percentual universal ou avaliação WER controlada. Exemplos no Parakeet preservaram negação, temperatura/umidade e horário/reunião. “Souza” em lugar de “Sousa” permanece uma imprecisão; ausência de palavras só pode ser avaliada contra o áudio realmente falado.

A consulta HA recebeu HTTP 200, mas trouxe listas success/failed vazias e foi marcada `hass_action_unconfirmed`; isso é confirmação do retorno da ferramenta, não falha do ASR ou prova de execução física. Robustez prolongada, memória/temperatura do host e comparação dos mesmos arquivos continuam sem medição nesta rodada. Os cerca de 664 MB do download se referem aos arquivos do Parakeet INT8.

## Referências

- [Modelo PT-BR e exemplo ONNX/INT8](https://huggingface.co/alefiury/parakeet-tdt-v3-ptBR-30s-onnx): pesos CC-BY-4.0, crédito a alefiury e ao checkpoint de origem alexandreacff/parakeet-tdt-v3-ptBR-30s; base NVIDIA Parakeet TDT v3.
- [onnx-asr](https://github.com/istupakov/onnx-asr) e [API](https://istupakov.github.io/onnx-asr/reference/): runtime MIT, entrada NumPy/16 kHz e CPUExecutionProvider.
- [Benchmarks do runtime](https://istupakov.github.io/onnx-asr/benchmarks/): hardware e idiomas distintos; não são medição do Juvenal no RK3566. Qualidade publicada do checkpoint é FP32 em outro hardware, não comprovação do INT8 aqui.
