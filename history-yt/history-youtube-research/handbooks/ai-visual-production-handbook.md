# AI Visual Production Handbook

Visual direction, scene design, prompt templates, and failure correction for
historical AI imagery and video. Written to be used with `tool-directory.md`.

Rule zero: historical plausibility beats spectacle. A viewer who knows the
period forgives a plain image; they do not forgive a wrong one.

---

## PART 1. Scene design system

Every scene is specified with this record. Nothing is generated before the record
exists.

| Field | Content |
|---|---|
| scene_id | e.g. S-07 |
| narration | exact voiceover line(s) for the scene |
| period / location | e.g. 9 AD, Teutoburg Forest, Germania |
| subjects | who/what is in frame |
| environment | terrain, architecture, weather |
| composition | shot size (wide/medium/close), subject placement, depth layers |
| camera | angle and movement (static / slow push / pan / dolly) |
| lighting | direction, hardness, color temperature, time of day |
| atmosphere | dust, rain, smoke, haze; what it means |
| action | what changes inside the shot |
| style | one locked style token set (see Part 2) |
| duration | seconds, matched to narration reading time |
| transition | cut / crossfade / map wipe |
| continuity refs | character sheet ids, location sheet ids used |

Approximate durations: narration reads at ~150-170 wpm for documentary pace.
Scene duration = words / 2.6 (seconds) rounded to 0.5s. Never faster than the voice.

---

## PART 2. Style lock (write once per video)

Define once, reuse verbatim in every prompt:

```
STYLE: {medium} illustration, {palette}, {line treatment}, {texture}, {framing signature}
Example (animated documentary):
"painterly 2D illustration, desaturated earth tones with ember-orange accents,
strong silhouettes, subtle canvas texture, cinematic 16:9 framing, no text in image"
Example (photorealistic reconstruction):
"cinematic photorealistic render, natural overcast light, 35mm photography,
muted film grade, subtle grain, anamorphic lens feel, shallow depth where intimate"
```

One style per video. Mixing painterly stills and photorealistic stills in the
same video is the single most common way to break cohesion.

Banned tokens: "hyper detailed", "8k", "masterpiece", "trending on ArtStation",
"unreal engine render" as filler. They pull the model toward generic game
key-art instead of period truth.

---

## PART 3. Universal prompt template

```
{STYLE}. {SHOT SIZE} of {SUBJECT, with costume/weapon specifics} in {LOCATION,
period-appropriate architecture}, {ACTION}. {LIGHTING}, {ATMOSPHERE}.
Historical accuracy notes: {objects/architecture to get right, e.g. "galea with
transverse crest", "lorica segmentata, not plate armor"}. Composition: {subject
placement, negative space for captions}. No text, no watermark, no anachronistic
anachronisms: {list}. Reference: {character sheet / location sheet id}.
```

Negative prompt (SD/ComfyUI workflows):
```
modern clothing, wristwatch, zipper, plastic, concrete (anachronistic), power
lines, satellite dish, cars, firearms (if pre-1500), wrong insignia, extra
fingers, fused fingers, deformed hands, text, watermark, logo, modern haircut,
clean-shaven Roman officer (unless plot requires), blue jeans
```

---

## PART 4. Period visual direction

### Ancient Rome (Republic / Empire, 509 BC - 476 AD)
- Architecture: opus incertum/quadratum walls, arches only after late Republic,
  marble veneers on important buildings, red tile roofs, insulae for poor,
  domus/villa for rich.
- Soldiers: Republican legionary: bronze Montefortino helmet, chainmail
  (hamata), rectangular scutum (by mid-Republic), pilum. Imperial legionary:
  lorica segmentata (from ~1st c AD), galea with transverse crest for officers,
  rectangular scutum. Auxiliaries similar but local types. Never put plate
  cuirass on foot soldiers.
- Citizens: tunic + toga (toga for adult male citizens only), stola/palla for
  women, leather sandals (caligae for soldiers). Colors: undyed wool for poor,
  dyed for wealthy; purple reserved (murex).
- Atmosphere: dust, oil lamps, woodsmoke, forum crowds, disciplined camp layouts.
- Reference sources: Trajan's Column (costumes, formations), Mainz/Newstead
  finds, authoritative reconstructions (e.g., Roman reenactment societies).

### Ancient Greece (Archaic to Classical, 800-323 BC)
- Hoplite: bronze muscle cuirass or linen linothorax, Corinthian/Attic helmet,
  aspis (round shield), spear. Phalanx deep files.
- Citizens: chiton (Ionic/Doric), himation cloak; women: peplos/chiton, korē
  style hair for archaic.
- Architecture: Doric for most temples; marble, painted (metopes were painted,
  not white). Agora scenes: stalls, herms, painted stoas.
- Note: white-marble ideal is a 19th-century misreading. Reconstructions should
  carry pigment (polychromy) for authenticity.

### Ancient Egypt (Old Kingdom to Ptolemaic)
- Titles matter: royal vs noble vs worker. Royal linen kilt, nemes headdress
  (pharaoh), shenti; nobles in white linen, jewelry only if wealthy; workers
  in simple white or brown linen; men often shaven or short-haired.
- Architecture: sandstone/limestone, huge columns (papyrus/lotus capitals),
  painted walls (tombs are vivid). Obelisks only where they existed.
- Visual language: use Egyptian canon for murals (profile heads, frontal
  shoulders) when simulating wall art; photoreal reconstructions should not
  impose it on living scenes.
- Avoid: "cleopatra" clichés (she was Macedonian Greek, Ptolemaic), anachronistic
  pyramids under construction with tens of thousands of slaves (debate: paid
  laborers per most Egyptologists; say "laborers", avoid "slaves" as default).

### Medieval Europe (500-1400)
- Early medieval (500-900): migration-period clothing; mail hauberks; simple
  tunics and cloaks; few stone castles (motte-and-bailey from 10th-11th c).
- High/late medieval (1000-1400): knightly kit evolves: mail hauberk → 13th c
  surcoat → plate fauld/greaves → full plate by ~1420-1450. Heraldic surcoats,
  not "shiny plate everywhere" for 1100.
- Architecture: Romanesque (round arches, thick walls, 1000-1150) → Gothic
  (pointed arches, rib vaults, flying buttresses, 1150+). Half-timbered towns.
- Peasants: wool, linen, leather; colors muted, undyed/home-dyed.
- Common errors: full plate in 1200, anachronistic "knave" cartoon garb,
  cheerful pastel castles. Stone gray, timber brown, rush and tallow smells.

### Feudal Japan (Sengoku to Edo, 1467-1868)
- Sengoku samurai: o-yoroi (boxy cuirass) earlier → tōsei-gusoku (more mobile)
  after mid-16th c; kabuto with crest (maedate); ō-sode large shoulder guards
  on o-yoroi. Ashigaru: jingasa (conical hat), spears (yari), later firearms
  (tanegashima, 1543+).
- Clothing: kimono layers; samurai formal kamishimo (kataginu + hakama) later;
  no-no-era anachronisms (Edo-period obi styling on Sengoku scenes).
- Architecture: shiro (castles with stone ishigaki bases and white plaster
  tenshu from late 16th c, e.g., Himeji 1609), thatched farmhouses, Edo machiya.
- Atmosphere: rice paddies, mist, banners (nobori), camp curtains (jinmaku).

### World War I (1914-1918)
- Uniforms: French horizon-blue (from 1915), German field-grey, British khaki
  + Brodie helmet (1915+), French Adrian helmet, German Stahlhelm (1916+).
  Russian, Ottoman, Italian variants distinct.
- Landscape: trench systems, barbed wire, blasted mud, shell holes, duckboards.
- Weapons: bolt-action rifles, Lewis/Vickers machine guns, heavy howitzers,
  early tanks (rhomboid, Mark series, 1916+), aircraft biplanes mostly. No
  radios as common infantry issue (runners, flares).
- Tone: mud, rats, gas (colored clouds), lice. Avoid heroic "charge" framing
  for the Western Front; use the Eastern front for movement if needed.

### World War II (1939-1945)
- Axis vs Allied kit is distinct: German feldgrau and Stahlhelm M35-M42, British
  battledress and Brodie or MKII helmet, US olive drab M1 helmet and M1941 field
  jacket, Soviet pilotka cap and M35 or M40 greatcoat. Japanese troops wore
  mustard-khaki tropical kit early in the war.
- Vehicles: Panzer III/IV, Tiger I/II; Sherman M4; T-34/85; Spitfire/Hurricane;
  Bf 109/Fw 190; Mustang. Numbers on vehicles matter for buffs; keep them generic
  or research specific unit markings.
- Civilian scenes: blackout, ration cards, propaganda posters (state-made, not
  copyrighted), home front factories.
- Caution with swastikas and other Nazi insignia: context matters; present
  documentary depiction, avoid glorification framing in thumbnails (also
  platform policy issue). Use blurred/cropped insignia or US-facing angles when
  not required for identification.

### Ancient and medieval battles
- Scale: men with spears/shields in dense blocks; dust, banners, noise implied
  by stills: broken spear shafts, shields stacked, horses (war horses were
  common; destriers heavy for knights 12th c+).
- Weather shifts read as drama: dawn mist for ambush (e.g., Teutoburg), dust
  for desert battles, rain where sources document it.
- Terrain: name it. Teutoburg = dense woodland + rain; Hastings = low hill +
  Senlac ridge; Marathon = coastal plain. Generic "open field" weakens credibility.

### Historical cities
- Rome: dense insulae, temples on forums, aqueduct arches entering town.
- Constantinople/Istanbul: Theodosian walls + harbor chains; Hagia Sophia dome.
- Tenochtitlan: causeways, canals, twin pyramid Templo Mayor (pre-1521).
- Baghdad (Abbasid): round city (al-Mansur), later sprawling markets.
- London 1666: pre-fire timber, St Paul's old (before Wren).
- Always ask: what does this city look like at the depicted year, not generic.

### Historical biographies
- Anchor the subject with 2-3 recurring "identity markers" (specific armor, a
  writing desk, a signature cloak, a map) so viewers track one person across
  decades. Use a locked character reference sheet (front/side/3-4) generated
  once and reused.
- Avoid putting real, living people into generated scenes; historical dead
  figures are documentary-acceptable as stylized illustration, not fake
  "photographs" that imply authenticity.

---

## PART 5. Character and environment consistency

1. Character sheets: generate a turnaround (front, 3/4, profile) on one canvas
   with the exact style lock before the video. Save as reference: `refs/char_<name>_sheet.png`.
2. Reuse the sheet in image-to-image / references every time that character
   appears. Seed lock helps but does not guarantee; reference images are the anchor.
3. Location sheets: 2-3 establishing stills per recurring location; the prompt
   must name persistent features (hill, river bend, gate) verbatim each time.
4. Same prompt stem, only the variable fields change (action, camera). Do not
   re-describe the world from scratch per shot.
5. Costume codes: keep one line per character describing dress exactly (e.g.,
   "charLEONIDAS: red cloak, bronze muscle cuirass, black-crested Corinthian
   helmet, greaves, spear").
6. Track everything in `scene_manifest.json`: scene_id, refs used, seed, model,
   prompt. Enables targeted regeneration of only the failed scenes.

---

## PART 6. Image-to-video and camera moves

Ken Burns is the floor, not the ceiling. In order of convincing quality:
1. Still + parallax layers (foreground/midground/background separated in edit,
   scaled/translated differently) - cheapest, very effective.
2. Slow push/pan via i2v model with "slow dolly in, no subject motion" prompt
   and amplitude capped. Set output 4-6s, loop or cut before drift shows.
3. Subject animation only when story needs it (charging horse, falling stone):
   use i2v, verify limb/weapon integrity frame by frame before acceptance.
4. Map sequences: pre-render with After Effects / Remotion / Manim-like tooling;
   better than any i2v model today.
5. Avoid: full scene i2v on complex crowds (faces warp), long clips (>6s, drift),
   "dynamic camera sweep" prompts (random composition drift).

Failure correction table:

| Failure | Fix |
|---|---|
| Extra/melted fingers | Inpaint hand region with sheet-consistent hand pose; crop so hands out of frame; use gloves/props |
| Wrong helmet crest (transverse vs longitudinal) | Inpaint or regenerate with explicit crest direction tokens; verify against refs |
| Weapon morphs across frames (i2v) | Shorten clip; freeze final frame extend in editor; prefer still+parallax |
| Face drift (character changes between scenes) | Re-issue i2v from a locked still, never from another i2v output |
| Flicker/shimmer | Generate at 24fps, add film grain + slight blur in post; crossfade 150-250ms to hide seams |
| Anachronistic architecture | Inpaint replacing offending structure; or re-crop tighter to exclude |
| Crowd faces mushy | Stay wide or silhouette/backlit crowds; never close-up random faces in crowd |
| Lighting inconsistency | Normalize in post: match black levels/gamma across clips before color grade |

---

## PART 7. Thumbnails as stills (support for Phase 7)

Generate thumbnail candidates from the same style lock, then grade for contrast
in the editor: single focal subject, face orientation optional, 2-4 word text,
60-70% of frame occupied by subject, background darkened 30-40%.

---

## PART 8. Verification loop

For every generated still:
1. Period plausibility scan (costume/weapon/architecture against the period list above).
2. Anachronism scan (negative prompt artifacts).
3. Character match against reference sheet.
4. Text/watermark scan.
5. Composition: focal point, caption space, crop for 16:9 and 9:16 derivatives.

Still approval rate target: expect 30-60% rejection on early runs; budget accordingly.
For i2v clips: frame-step review at 8-12 samples per clip; accept only if zero
anatomy/prop failures across samples.
