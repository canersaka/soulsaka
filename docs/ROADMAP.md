# Roadmap

What exists, what is next, and the stretch work that turns this from "built an AI app"
into "did ML systems work on real silicon".

## Done (runs today, tested without a GPU)

- Hub: SQLite corpus with FTS5, hashed identities, pairing auth, durable job queue,
  SSE fan-out, capture pipeline (speaker check → ASR → memory extraction), hybrid
  retrieval, chat with pluggable model profiles and a cloud egress gate.
- Importers (iMessage, WhatsApp, email, Discord, git, docs) with auto-discovery.
- Web PWA with offline queue; always-on listener with VAD and disk spool.
- Training: register-tagged dataset snapshots, QLoRA backends for CUDA (Unsloth/PEFT)
  and Apple Silicon (MLX), versioned adapters, GGUF LoRA export, adapter serving.
- Evals: blind pairs, discriminator, voice similarity, fidelity curve; `soulsaka bench`.
- Voice: reference clip assembly and zero-shot TTS.

## Next (needs your hardware)

1. **First corpus number.** `soulsaka import auto` on the Mac; target ≥ 30k words.
2. **v1 baseline.** `soulsaka train run` on the G14, `soulsaka train serve-llm`,
   `soulsaka eval pairs --version v1 --n 30`, send `/rate/v1` to five friends. Record the
   first point on the curve *before* touching hyper-parameters.
3. **Voice enrolment.** Ten push-to-talk notes, `soulsaka voice reference`,
   `soulsaka voice say "test"`; then `soulsaka eval voice --version v1`.
4. **Monthly loop.** `scripts/retrain.sh` in cron. Every version adds a point.
5. **TTS fine-tune** once verified audio passes one hour (F5-TTS fine-tuning script on
   `captures WHERE speaker_is_me = 1`), versioned like the adapters.

## Stretch: systems work

### AMD XDNA NPU (Ryzen AI 9 HX 370)

Goal: run Whisper (and later the quantised LLM) on the NPU and publish NPU vs GPU vs
CPU latency and power for the same clips. Plan:

- Export Whisper encoder/decoder to ONNX (Optimum) and run through ONNX Runtime with
  the VitisAI execution provider from the Ryzen AI SDK (Windows). Add an `onnx`
  backend in `ml/asr.py` behind the same `ASR` protocol; select with
  `asr.backend = "onnx"` and `asr.device = "npu"`.
- `soulsaka bench --wav clip.wav` already measures audio-capture-to-transcript end to end;
  add per-stage timings (decode, encoder, decoder) to `ASRResult.segments` metadata and
  a `--repeat` flag, then a table: CPU int8 / CUDA fp16 / NPU per clip length.
- Power: sample `nvidia-smi --query-gpu=power.draw` and the Ryzen AI SDK's NPU
  utilisation counters during the run; report joules per second of audio.
- Write up the rough edges honestly (operator coverage, quantisation accuracy vs
  faster-whisper, first-run compile time). That write-up is the interview material.

### Latency budget: under 800 ms to first audio

Voice round trip is listener → ASR → LLM → TTS → speaker. Budget and where it goes:

| stage | today | target | levers |
| --- | --- | --- | --- |
| VAD end-of-speech | 800 ms silence | 400 ms | tune `silence_end_s`, endpoint on falling energy |
| upload + ASR | ~300 ms (5070 Ti, turbo) | 200 ms | stream chunks over WebSocket, decode while speaking |
| LLM first token | ~250 ms (4B Q4) | 150 ms | KV-cache reuse of the system prompt, speculative decoding with the 0.8B draft |
| TTS first chunk | 1.5 s (F5-TTS) | 300 ms | streaming TTS (Fish Speech / F5 chunked), start on first clause |

Instrument with `soulsaka bench` plus Nsight Systems on the G14 for the CUDA stages;
keep the per-version numbers next to the fidelity curve.

### Register-conditioned generation study

Three registers in one adapter versus three adapters: does conditioning cost fidelity?
The dataset builder already tags registers; train `v N-text-only` alongside `vN` and
compare discriminator accuracy per register. Also the Turkish/English code-switching
evaluation: split the holdout by `lang` and report the curve per language.

### Phone always-on

iOS will not keep a web app's microphone open in the background. The options are a
Capacitor wrapper with background audio mode, or an Apple Watch/AirPods shortcut that
records to the phone and syncs. Both keep the same hub API.
