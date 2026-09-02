# Roadmap

## What works today

The hub with the corpus, memories, retrieval and chat. The importers with auto-discovery. The web app with offline capture. The always-on listener with speaker verification on the hub. Training on CUDA or Apple Silicon with versioned adapters. The three evals and the curve. Zero-shot voice cloning. All of it tested without a GPU, so the first real-hardware run is the next step.

## Next

1. Get the first corpus number with `soulsaka import --auto`. The target is at least 30k words.
2. Train v1, serve it, generate blind pairs, send the rating link to a few friends. Record the first point on the curve before touching any hyperparameters.
3. Record ten push-to-talk notes, build the voice reference, and run the voice eval.
4. Put `scripts/retrain.sh` in cron. Every month adds a point.
5. Once verified audio passes one hour, fine-tune the voice model on it and version it like the adapters.

## Bigger things

Run speech recognition on the NPU in the Windows machine. Export Whisper to ONNX, run it through ONNX Runtime with the vendor's execution provider, add it as another ASR backend, and compare latency and power against the GPU and the CPU on the same clips using `soulsaka bench`. Write up the rough edges honestly.

Cut the voice round trip to under a second. The stages are end-of-speech detection, upload and transcription, first token from the model, and first audio from the voice model. Each has a lever: shorter silence timeout, streaming audio chunks, reusing the model's cache for the system prompt, and streaming text-to-speech. `soulsaka bench` is the yardstick.

Check whether one adapter for all three registers costs anything compared to one adapter per register, and split the curve by language to see how Turkish and English compare.

Always-on mic on the phone. iOS will not keep a web app's mic open in the background, so this needs a native wrapper with background audio, talking to the same hub.

Calendar as retrieval, so the assistant knows what is on Thursday without training on it.
