"""Always-on microphone client: ``soulsaka listen``.

The listener keeps the microphone open, runs voice activity detection locally, keeps only
the stretches that contain speech, buffers them on disk (``spool_dir()``) while the hub is
unreachable, and uploads them to ``POST /api/captures/audio`` as soon as it can. The hub
then decides whether it was you speaking, transcribes, and extracts memories.

Pipeline::

    microphone -> 512-sample frames (16 kHz) -> VAD probability -> Segmenter
               -> Spool (<uid>.wav + <uid>.json) -> Uploader -> hub

Privacy
-------
* The only thing stored on the listening machine is the spool, and every entry in it is
  deleted as soon as the hub has acknowledged it.
* With ``--no-upload`` nothing is ever sent anywhere; segments just accumulate in the spool.
* The hub verifies the speaker of every segment against the enrolled voice profile and
  **discards audio that is not the enrolled speaker** (``privacy.other_speakers``, see
  PRIVACY.md): only your own utterances are transcribed and kept.
"""

from __future__ import annotations
