# Modelos locais

Para habilitar wake word no servidor, coloque o modelo personalizado validado em `models/juvehnal.tflite`, ou configure `WAKE_WORD_MODEL` no `.env`.

O modelo “Juvenal” (`juvehnal`) foi treinado com os recursos de [TaterTotterson/Tater-Wake-Words](https://github.com/TaterTotterson/Tater-Wake-Words). Use o artefato já validado pelo projeto; não substitua por outro modelo apenas por ter o mesmo nome. O treinamento é externo ao servidor.

Os pesos e caches são locais e ignorados pelo Git. Um clone contém esta documentação e a estrutura da pasta; o modelo precisa ser provisionado separadamente, por exemplo, copiando o artefato validado do firmware Juvenal que você já possui. Preserve seus avisos e licenças.

Whisper ou Parakeet (`ASR_BACKEND`) e Piper utilizam os modelos configurados e podem baixá-los pela respectiva biblioteca ao iniciar a capacidade correspondente. Parakeet PT-BR ONNX/INT8 é uma opção avaliada pelo autor no Orange Pi, com dependência opcional; veja [seleção e resultados](../README.md#transcrição-whisper-ou-parakeet-pt-br). Entrada somente textual dispensa ASR e wake word; saída `endpoint` dispensa Piper e reprodução local, salvo a política específica de resposta HA no ESP.

Veja o [README principal](../README.md) antes de iniciar.
