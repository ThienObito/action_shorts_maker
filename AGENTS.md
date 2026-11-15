# Documentary pre-flight (all new projects)

Before calling `produce_us_documentary` for new YouTube links or videos, ask these four short questions unless the user already gave explicit choices for this project (do not silently reuse choices from a prior project):

1. Source audio: `mute_all` (recommended for source music/speech), `extract_env_sfx` (Demucs four-stem `other` ambience; not guaranteed speech/music-free; audition before publishing), or `keep_raw_low` (only for verified machine-only source).
2. Original on-screen text masking: `auto_detect_blur` (heuristic; review), `top_blur_brand`, `bottom_blur_sub`, `both_top_bottom_blur`, or `none`. Masking is for licensed footage and cannot guarantee complete original watermark removal.
3. BGM: `dynamic_multi`, `industrial`, `precision`, `craft`, `foundry`, `mystery`, `triumph`, or `none`.
4. Script niche: `engineering_machining`, `woodworking_bonsai`, or `survival_commentary`.

Never queue a job with an unspecified pre-flight option. The MCP tool returns `preflight_required` rather than launching when choices are absent. English AI narration, landscape ASS captions, three portrait ASS-captioned Shorts and 8-second Intro Hook remain on. The Studio shows real files/jobs and only renders derivatives when individual stems exist; it is not 14 independent LLM agents. No -14 LUFS certification without measurement. Process only media the user owns or is licensed to reuse. A Demucs stem can still contain original speech/music; do not claim it is clean without auditioning it. Do not push sources, videos, audio, outputs, credentials or models to Git.

`process_youtube_links` is a legacy quick-Shorts tool and does not implement the documentary invariants above; never call it as a substitute for `produce_us_documentary` on a new documentary request. The Studio preflight selector stores choices only in the browser session and does not itself start a job.
