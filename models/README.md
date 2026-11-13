# Modelos locais

Para habilitar wake word no servidor, coloque o modelo personalizado validado em `models/juvehnal.tflite`, ou configure `WAKE_WORD_MODEL` no `.env`.

O modelo “Juvenal” (`juvehnal`) foi treinado com os recursos de [TaterTotterson/Tater-Wake-Words](https://github.com/TaterTotterson/Tater-Wake-Words). Use o artefato já validado pelo projeto; não substitua por outro modelo apenas por ter o mesmo nome. O treinamento é externo ao servidor.

Os pesos e caches são locais e ignorados pelo Git. Um clone contém esta documentação e a estrutura da pasta; o modelo precisa ser provisionado separadamente, por exemplo, copiando o artefato validado do firmware Juvenal que você já possui. Preserve seus avisos e licenças.

Whisper e Piper utilizam os modelos configurados e podem baixá-los pela respectiva biblioteca ao iniciar a capacidade correspondente. Entrada somente textual dispensa Whisper e wake word; saída `endpoint` dispensa Piper e reprodução local.

Veja o [README principal](../README.md) antes de iniciar.
