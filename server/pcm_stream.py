"""Conversão contínua para PCM16 mono 16 kHz, sem acumular toda a síntese."""

def pcm16_blocks(chunks, stop, max_seconds=60):
    import numpy as np

    target_rate = 16000
    rate, channels = None, None
    samples = np.empty(0, dtype=np.float64)
    base = received = output_index = 0
    pending = bytearray()

    def convert(final=False):
        nonlocal samples, base, output_index
        boundary = received if final else max(0, received - 1)
        exclusive = (boundary * target_rate + rate - 1) // rate
        if exclusive <= output_index:
            return b""
        indices = np.arange(output_index, exclusive, dtype=np.int64)
        numerators = indices * rate
        left = numerators // target_rate
        right = np.minimum(left + 1, received - 1)
        fraction = (numerators % target_rate) / target_rate
        values = samples[left - base] * (1 - fraction) + samples[right - base] * fraction
        output_index = exclusive
        keep = min(received - 1, output_index * rate // target_rate)
        samples = samples[max(0, keep - base):].copy()
        base = keep
        return np.clip(np.rint(values), -32768, 32767).astype("<i2").tobytes()

    for chunk in chunks:
        if stop.is_set():
            return
        if (chunk.sample_format != "s16le" or type(chunk.sample_rate) is not int
                or not 8000 <= chunk.sample_rate <= 192000
                or type(chunk.channels) is not int or not 1 <= chunk.channels <= 8
                or len(chunk.pcm) % (2 * chunk.channels)):
            raise ValueError("invalid_native_pcm")
        if rate is None:
            rate, channels = chunk.sample_rate, chunk.channels
        elif rate != chunk.sample_rate or channels != chunk.channels:
            raise ValueError("native_pcm_format_changed")
        if not chunk.pcm:
            continue
        native = np.frombuffer(chunk.pcm, dtype="<i2").reshape(-1, channels)
        if received + len(native) > int(rate * max_seconds):
            raise ValueError("synthesis_duration_limit")
        mono = native.astype(np.float64).mean(axis=1)
        received += len(mono)
        samples = np.concatenate((samples, mono))
        pending.extend(convert())
        full = len(pending) // 640 * 640
        for offset in range(0, full, 640):
            if stop.is_set():
                return
            yield bytes(pending[offset:offset + 640])
        if full:
            del pending[:full]
    if rate is None or not received:
        raise ValueError("empty_synthesis")
    pending.extend(convert(final=True))
    for offset in range(0, len(pending), 640):
        if stop.is_set():
            return
        yield bytes(pending[offset:offset + 640])
