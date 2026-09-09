# UI Style Guide

This document defines the visual language for the Mój Asystent desktop application. It applies to the overlay, expanded chat, onboarding, settings, confirmation dialogs and future desktop UI.

## Core direction

The V1 interface is **black, minimalistic and restrained**.

The visual target is Apple/iOS-inspired clarity adapted to a Windows desktop assistant, not a generic colorful AI dashboard.

### Required characteristics

- predominantly near-black and black surfaces;
- white primary text and neutral gray secondary text;
- very limited color usage;
- clean hierarchy with generous but efficient spacing;
- thin, subtle borders rather than heavy outlines;
- restrained translucency/blur where it improves depth;
- smooth, short interaction-driven animations;
- consistent visual language across compact overlay, chat, onboarding and settings;
- high legibility at small overlay sizes.

## Palette

Use a neutral grayscale foundation. Exact tokens may evolve, but the visual relationship should remain stable.

Suggested baseline:

```text
Background        #050505
Raised surface    #0B0B0C
Secondary surface #111113
Primary text      #F5F5F7
Secondary text    #A1A1A8
Muted text        #6F6F76
Subtle border     rgba(255,255,255,0.10)
Strong border     rgba(255,255,255,0.17)
Primary control   #F5F5F7 on #080808 text
```

Semantic colors may be used sparingly for warnings, errors, success and privacy/permission states. Never rely on color alone to communicate state.

## Avoid

Do not drift into common generated "AI app" styling.

Avoid by default:

- purple/blue AI gradients;
- neon glows around every element;
- bright multicolor accents;
- glassmorphism on every surface;
- large collections of identical rounded cards;
- excessive shadows;
- oversized pill controls everywhere;
- decorative animation without interaction meaning;
- unnecessary dashboard chrome;
- visual elements that make the overlay feel like a web SaaS page rather than a native desktop assistant.

## Shape and depth

- Use moderate corner radii; reserve larger radii for the compact assistant bubble or major floating surfaces.
- Prefer a small number of elevation levels.
- Use borders and subtle luminance differences before adding shadows.
- Blur/transparency should be restrained and must retain readable contrast over arbitrary desktop backgrounds.

## Typography

- Prefer the native/system UI font stack for desktop consistency unless a future branding decision explicitly changes it.
- Use sentence case in Polish UI copy.
- Keep labels concise and readable.
- Use weight and spacing for hierarchy rather than many font sizes.
- Avoid all-caps labels except where the design specification explicitly requires a small technical/status treatment.

## Motion

Motion should explain state changes.

Good uses:

- compact → expanded overlay;
- idle → listening;
- listening → thinking;
- opening settings;
- confirmation appearing;
- streaming/processing indicators.

Avoid decorative perpetual motion. Respect reduced-motion settings.

## Assistant states

Listening, thinking, speaking and follow-up states should remain visually calm. Differentiate them using a combination of copy, iconography, waveform/activity and subtle luminance changes rather than assigning a bright color to every state.

## Native desktop feel

The application should feel intentionally designed for desktop use:

- compact controls;
- keyboard accessibility;
- clear focus behavior;
- predictable hover/pressed states;
- sensible density;
- no mobile-first navigation patterns forced onto the desktop layout.

## Source-of-truth rule

For visual implementation work, this file and `docs/OVERLAY_UI.md` are both required reading. If a mockup or explicit user instruction conflicts with this guide, the newer explicit product decision wins and this document should be updated in the same change.
