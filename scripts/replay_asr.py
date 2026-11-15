"""Ferramenta manual de comparação: mesmos WAVs, somente ASR, sem servidor/LLM/HA/TTS."""

import argparse
import asyncio
import logging
import os
import statistics
import sys
import wave
from pathlib import Path


def read_pcm(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("WAV deve ser PCM16 mono, 16000 Hz, sem compressão")
        if not 0 < wav.getnframes() <= 480000:
            raise ValueError("WAV deve ter entre uma amostra e 30 segundos")
        pcm = wav.readframes(wav.getnframes())
        if len(pcm) != wav.getnframes() * 2:
            raise ValueError("WAV incompleto")
        return pcm


async def replay(args, clips):
    from transcriber import create_transcriber
    from diagnostics import log_text

    logger = logging.getLogger("asr_replay")
    transcriber = await asyncio.to_thread(create_transcriber)
    times = []
    rtfs = []
    try:
        # Aquecimento real, identificado e excluído do resumo. Não usa áudio sintético.
        logger.info("Aquecimento: primeira gravação, excluída do resumo")
        await transcriber.transcribe_pcm(clips[0][1], timeout=args.timeout,
                                        context={"source": clips[0][0].name, "warmup": True})
        for repetition in range(1, args.repetitions + 1):
            for path, pcm in clips:
                text = await transcriber.transcribe_pcm(pcm, timeout=args.timeout, context={
                    "source": path.name, "repetition": repetition, "warmup": False})
                record = transcriber.diagnostics.last_record
                if record["status"] not in {"ok", "empty"}:
                    raise RuntimeError("transcription_not_completed")
                times.append(record["asr_ms"])
                rtfs.append(record["rtf"])
                logger.info("Arquivo=%s repeticao=%d texto=%s", log_text(path.name),
                            repetition, log_text(text))
        logger.info("RESUMO backend=%s arquivos=%d inferencias=%d mediana_ms=%.1f media_ms=%.1f mediana_rtf=%.3f",
                    args.backend, len(clips), len(times), statistics.median(times),
                    statistics.mean(times), statistics.median(rtfs))
        logger.info("Resultados: %s", transcriber.diagnostics.directory / "results.jsonl")
        logger.info("Qualidade: conferir manualmente texto, negações, nomes e números; tempo menor não prova qualidade.")
    finally:
        transcriber.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("whisper", "parakeet"), required=True)
    parser.add_argument("--input-dir", type=Path, required=True, help="Pasta de WAVs coletados do ESP")
    parser.add_argument("--repetitions", type=int, choices=range(1, 11), default=3)
    parser.add_argument("--timeout", type=float, default=120, help="Prazo por WAV; somente ferramenta manual")
    args = parser.parse_args()
    if not 0 < args.timeout <= 600:
        parser.error("--timeout deve estar entre 0 e 600 segundos")
    if not args.input_dir.is_dir():
        parser.error("Pasta de entrada não encontrada")
    paths = sorted(args.input_dir.glob("*.wav"))
    if not paths:
        parser.error("Nenhum WAV encontrado")
    if len(paths) > 100:
        parser.error("Use no máximo 100 gravações por comparação")
    # Ler/validar todos os arquivos antes de carregar qualquer modelo.
    try:
        clips = [(path, read_pcm(path)) for path in paths]
    except (OSError, ValueError, EOFError, wave.Error) as error:
        parser.error(str(error))
    os.environ.update(ASR_BACKEND=args.backend, ASR_DIAGNOSTICS_ENABLED="true",
                      ASR_SAVE_AUDIO="false", LOG_TRANSCRIPTS="true")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    try:
        asyncio.run(replay(args, clips))
    except Exception as error:
        from diagnostics import log_text
        # Sem imprimir tracebacks com URLs/credenciais ou detalhes da configuração.
        logging.getLogger("asr_replay").error("Comparação interrompida (%s); nenhum resumo de sucesso gerado. "
                                             "Confira dependências/modelo, formato e timeout. erro=%s",
                                             type(error).__name__, log_text(error, limit=1024))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
