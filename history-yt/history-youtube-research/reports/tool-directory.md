# Tool Directory

Research date: 2026-11-05. Prices change. Re-verify before purchase.
Status labels: VERIFIED means read from an official pricing or docs page on this date. UNKNOWN means not verified in this pass. Do not treat a free trial as free access.

## How to read this file

Free access is split into four kinds, because the gap matters for budgeting:

1. Open weights / open source. You can run the model yourself. Commercial use still depends on the model license.
2. Free tier. A standing free plan with ongoing limits.
3. Free credits or trial. One-time allowance.
4. Paid only. No useful free path.

Watermarks, export size, and request limits only count if they are on the page. If they were not visible, they are marked UNKNOWN.

## Recommended production stacks

### $0 marginal cost stack

- Images and local animation: ComfyUI with FLUX.1 dev or FLUX.2 klein 4B/9B. BFL docs describe klein 4B and 9B as open weights that run on consumer GPUs (VERIFIED, https://docs.bfl.ai). Confirm the license at the model page before any commercial release.
- Narration: Piper (already configured in the local histyt pipeline) or Kokoro. fal lists Kokoro TTS at $0.02 per 1,000 characters when bought through its API (VERIFIED, https://fal.ai/pricing). Local use avoids per-character cost but voice quality and pronunciation control need testing.
- Editing: DaVinci Resolve or Kdenlive. Exact free-tier limits were not verified in this pass, so check the current download page before committing.
- Assembly and batch runs: FFmpeg. Use it for loudness checks, captions, and export instead of a manual editor step where possible.
- Maps: pre-rendered 2D animation. This remains more reliable than image-to-video for borders, arrows, and dates.

### API stack for speed

- Images: fal FLUX.1 schnell at $0.003 per megapixel, FLUX.1 dev at $0.025 per megapixel, FLUX 2 at $0.012 per megapixel (VERIFIED, https://fal.ai/pricing).
- Hero stills and editing: FLUX 3 at $0.048 per megapixel through BFL's calculator, or Seedream 4.5 at $0.04 per image through fal (VERIFIED). Use the more expensive path only for covers and recurring characters.
- Motion clips: FLUX 3 Max at $0.085 per second, Kling v2.6 at $0.07 per second, Kling v3 at $0.14 per second, MiniMax H3 Max at $0.05 per second (VERIFIED, https://fal.ai/pricing). Budget per scene, not per video, and stop the clip before drift appears.
- Voice: ElevenLabs Starter at $6 per month for 30,000 credits, with a commercial license included on that page (VERIFIED, https://elevenlabs.io/pricing). Text to speech costs 1 credit per character on the page FAQ, so Starter covers roughly 30,000 characters before music and effects draw from the same pool.
- Subscription alternative: Runway Standard at $12 per month billed annually, or $15 monthly, for 625 credits. Runway equates 625 credits to 52 seconds of Gen-4.5, 104 seconds of Gen-4 Turbo, or 78 Gen-4 images at 1080p (VERIFIED, https://runway.com/pricing). That is a test allowance, not a full documentary budget.

## Image and video

### Black Forest Labs / FLUX

- Website: https://bfl.ai and https://docs.bfl.ai
- Type: paid API with open-weight models available for local deployment.
- Free access: no standing free API tier was visible on the pricing page. Treat as paid-only for API use.
- Pricing (VERIFIED, https://bfl.ai/pricing): pay-as-you-go with no seat fee. The calculator showed a rate of $0.048 per image. Open-weight licensing is sold by tier, not as a simple per-image price. Builder covers FLUX 3 Image Klein and FLUX.2 klein, 10K images per month, 1 domain, 10 licensed users, and fine-tuning and LoRA rights. Platform and Professional tiers raise the image allowance to 100K per month. Exact tier dollars were not visible in the fetched text, so mark dollar prices UNKNOWN.
- Commercial rights: API output terms and weight-license terms are separate. Read both before a channel launch. The tier page distinguishes permissive commercial use at Enterprise level, which is a warning against assuming the lowest tier is unrestricted.
- Export limits and watermarks: UNKNOWN from this pass.
- API: yes. Async polling through a polling URL. Delivery URLs expire, so download results at once.
- Best use: character sheets, consistent stills, cover frames, and inpainting or outpainting corrections.
- Weaknesses: credit model means a bad prompt still costs money. Open-weight deployment needs a capable GPU and time to tune workflows.

### fal.ai

- Website: https://fal.ai
- Type: API aggregator for hosted models, plus serverless GPUs.
- Free access: no free-credit amount was visible on the pricing page. Treat as paid-only until a free offer is verified on the account page.
- Pricing (VERIFIED, https://fal.ai/pricing):
  - Serverless GPUs: RTX PRO 6000 96GB at $4.00 per hour list, H100 80GB at $4.50 per hour, H200 141GB at $6.00 per hour. Billed per second while the runner is alive.
  - Video: $0.005 to $0.14 per second depending on model. FLUX 3 Max is $0.085 per second. Kling v2.6 is $0.07 per second. Kling v3 and O3 are $0.14 per second. MiniMax H3 Max is $0.05 per second.
  - Images: FLUX.1 schnell $0.003 per megapixel, FLUX 2 $0.012, FLUX.1 dev $0.025, Seedream 4.5 $0.04 per image, Nano Banana $0.0398 per image.
  - Audio: Kokoro $0.02 per 1,000 characters, MiniMax Speech 2.8 Turbo $0.06 per 1,000 characters, speech-to-text not listed in the extracted table.
  - 3D: Trellis $0.02 per generation, Meshy 6 $0.8 per generation, Hyper3D Rodin $0.4 per generation.
- Commercial rights: model-specific. Check the endpoint page, not just the platform page.
- Export limits and watermarks: UNKNOWN from this pass.
- API: yes. Prepaid credits. No charge for queue wait or server errors.
- Best use: testing several video models with one key, plus running an open model on a rented GPU.
- Weaknesses: prices are per output unit, so a 30-second clip at $0.14 per second costs $4.20 before regeneration. Multi-model testing can burn credits fast.

### Runway

- Website: https://runway.com
- Type: hosted image, video, music, voice, and effects platform.
- Free access: free tier with 125 one-time credits and 5GB storage (VERIFIED, https://runway.com/pricing). This is credits, not open access.
- Paid pricing: Standard $15 monthly or $12 billed annually, 625 credits per month. Pro $35 monthly or $28 billed annually, 2,250 credits per month. Max $95 monthly or $76 billed annually, 9,500 credits per month.
- Credit math (VERIFIED): Standard equals 52 seconds of Gen-4.5 or 78 Gen-4 images. Pro equals 187 seconds of Gen-4.5 or 281 Gen-4 images. Max equals 791 seconds of Gen-4.5 or 1,187 Gen-4 images.
- Commercial rights: paid plans state no watermarks. Confirm the plan terms for the exact model before launch.
- Export limits: 4K upscaling is listed on paid plans. Source resolution and codec limits are model-specific.
- API: plan pages mention Runway MCP at Pro and Max. Endpoint-level API terms were not extracted in this pass.
- Best use: short motion tests, image-to-video from a locked still, and a fallback when local GPU rendering is too slow.
- Weaknesses: credit math is unforgiving. A 12-minute video cannot be generated clip-by-clip on Standard.

### Open-weight local models

- ComfyUI, FLUX.1 dev, FLUX.2 klein, SDXL, FLUX.1 Kontext, Wan, HunyuanVideo, LTX-Video, Hunyuan3D, Kokoro, Piper, Whisper.
- Type: open weights or open source. Free access means you pay with GPU time, not credits.
- Pricing: $0 in license fees for the software itself. Rented GPU time through fal or a similar provider is billed per hour.
- Commercial rights: model-by-model. Some weights carry non-commercial or attribution terms. Verify the model card before a monetized upload. This is the most common way a "free" stack fails legally.
- Export limits and watermarks: none from the model itself. Your editor sets resolution and codec.
- API: local HTTP or queue-based workflows. ComfyUI is scriptable.
- Best use: stills, inpainting, upscaling, captions, and repeatable styles without per-image fees.
- Weaknesses: setup time, VRAM needs, and inconsistent results if prompts change mid-video.

### Other hosted video models

- Kling, MiniMax / Hailuo, Seedance, Veo, Sora, Pika, Luma Ray, PixVerse, Grok Imagine.
- Type: hosted text-to-video and image-to-video.
- Pricing found (VERIFIED, https://fal.ai/pricing): PixVerse V6 at $0.005 per second, Grok Imagine at $0.01 to $0.05 per second, Seedance 2.0 Fast at $0.0112 per 1,000 tokens. Direct app pricing for Veo, Sora, Pika, and Luma was not verified in this pass, so mark those UNKNOWN.
- Best use: 3 to 6 second reaction shots and environmental motion. Do not use them for crowd scenes or text-heavy maps.
- Weaknesses: face drift, prop morphing, and camera moves that fight the edit.

## Voice

### ElevenLabs

- Website: https://elevenlabs.io
- Type: text to speech, voice design, music, and sound effects.
- Free access: free tier with 10,000 credits per month (VERIFIED, https://elevenlabs.io/pricing).
- Paid pricing: Starter $6 per month for 30,000 credits, Creator $22 for 121,000, Pro $99 for 600,000, Scale $299 for 1.8M with 3 seats, Business $990 for 6M with 10 seats. Annual billing equals monthly price times 10. Introductory first-month prices are listed on the page and should not be budgeted as ongoing.
- Commercial rights: the Starter tier lists a commercial license. The free tier does not list one on the extracted page. Do not publish monetized narration from the free tier without confirming the current terms.
- Credit costs (VERIFIED): text to speech 1 credit per character, speech to text 330 credits per minute, music 900 credits per minute, sound effects 200 credits per generation. Credits roll over up to two months on paid plans, up to twice the monthly quota.
- Export limits: audio quality rises by tier. Pro adds 192 kbps and 44.1 kHz PCM through the API.
- API: yes, with tier-dependent concurrency and quality.
- Best use: a consistent narrator voice with pronunciation control.
- Weaknesses: one credit pool across voice, music, and effects means a music-heavy video uses narration credits.

### Open and low-cost voice

- Piper: local, free, already in the histyt config. Test name pronunciation before committing.
- Kokoro: local or API at $0.02 per 1,000 characters through fal (VERIFIED).
- Chatterbox: $0.025 per 1,000 characters through fal (VERIFIED).
- MiniMax Speech 2.8 Turbo: $0.06 per 1,000 characters through fal (VERIFIED).
- Cartesia, PlayHT, Rime, OpenAI TTS, Google Cloud TTS, Azure Speech: not priced in this pass. Mark pricing UNKNOWN and check each official page.
- Voice cloning rule: clone your own voice or a licensed voice only. Cloning a real, identifiable person without consent fails the QA gate and creates a trust problem.

## Music and sound

- ElevenLabs music: 900 credits per minute. Starter includes music commercial use on the pricing page (VERIFIED).
- Suno and Udio: not priced in this pass. Mark pricing and commercial-stock terms UNKNOWN.
- Stable Audio Open, MusicGen, ACE-Step: fal lists ACE Step prompt-to-audio at $0.0002 per second and Sonilo text-to-music at $0.0025 per second (VERIFIED). Model licenses still apply.
- Free libraries: Freesound, Pixabay, Mixkit, Zapsplat, and the YouTube Audio Library differ by license. Some require attribution, some forbid commercial use, some are clean for YouTube only. Record the license per track. A track without a recorded license fails QA.
- Loudness target: -14 LUFS integrated, true peak at or below -1 dBTP. Measure with `ffmpeg` or your editor meters, not by ear.

## Editing and assembly

- DaVinci Resolve: free and Studio versions exist, but exact tier limits were not verified here (UNKNOWN).
- Kdenlive, Shotcut, CapCut: not priced in this pass (UNKNOWN). CapCut has had format and watermark changes in the past, so check the export page before a launch.
- FFmpeg: free and scriptable. Use for loudness, silence detection, caption burn-in, and batch exports.
- Whisper and whisper.cpp: local speech-to-text for captions and chapter checks. Correct names by hand after transcription.
- Premiere Pro and Descript: not priced in this pass (UNKNOWN).

## AI disclosure

YouTube requires disclosure for realistic AI-generated or altered content, including generated realistic scenes that did not occur. Production assistance such as scripting, thumbnail help, captions, and upscaling does not require disclosure on the official page. Cloning your own voice also does not require disclosure there. Realistic reconstructions of real events do require it. Failure to disclose can lead to manual labels, removal, or suspension from the YouTube Partner Program. Source: https://support.google.com/youtube/answer/14328491, saved at evidence/youtube-ai-disclosure-2026-11-05.md.

## Last check before buying

1. Confirm the price on the official page on the purchase date.
2. Confirm commercial use for the exact model and tier, not the platform in general.
3. Confirm watermark and export resolution on a test render.
4. Save the license text next to the project files.
