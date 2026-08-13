---
name: "Locked CMS"
description: "A calm, compact operations interface for agencies and their clients."
colors:
  action-blue: "#2457d6"
  action-blue-deep: "#1944b3"
  action-blue-soft: "#edf3ff"
  canvas: "#f4f6fa"
  surface: "#ffffff"
  surface-muted: "#f7f8fb"
  surface-strong: "#eef1f6"
  ink: "#101828"
  ink-soft: "#344054"
  ink-muted: "#667085"
  border: "#dfe3ea"
  border-strong: "#c8ced9"
  success: "#067647"
  success-soft: "#ecfdf3"
  danger: "#b42318"
  danger-soft: "#fef3f2"
  warning: "#b54708"
  warning-soft: "#fffaeb"
typography:
  headline:
    fontFamily: "Roboto, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "clamp(24px, 2.2vw, 32px)"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Roboto, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "20px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Roboto, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "Roboto, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.5
  mono:
    fontFamily: "Roboto Mono, ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "7px"
  md: "10px"
  lg: "14px"
  xl: "18px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  2xl: "32px"
  3xl: "48px"
  4xl: "64px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "9px 15px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.action-blue-deep}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink-soft}"
    rounded: "{rounded.md}"
    padding: "9px 15px"
    height: "40px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "9px 12px"
    height: "42px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "32px"
---

# Design System: Locked CMS

## Overview

**Creative North Star: "The Calm Operations Desk"**

Locked CMS feels like a focused work surface: dependable, compact, and quiet enough for repeated daily use. The visual system preserves the product's established blue, navy, white, and Roboto identity while using disciplined spacing and hierarchy to keep complex site operations legible.

Agency screens favor efficient scanning; client screens reveal only the controls clients can safely use. Navigation, feedback, and destructive actions remain explicit rather than decorative.

**Key Characteristics:**

- Compact operational density with generous separation between distinct tasks.
- A restrained blue accent reserved for selection, focus, and primary actions.
- Pale cool-gray canvas, white working surfaces, and dark navy application chrome.
- Plain-language states with visible recovery paths.
- Responsive layouts that become stacked, touch-friendly task cards on small screens.

## Colors

The palette is cool, trustworthy, and functional: one decisive action blue, neutral work surfaces, and stable semantic colors.

### Primary

- **Action Blue:** Drives primary actions, links, active navigation details, and focus.
- **Deep Action Blue:** Provides hover emphasis and high-contrast link treatment.
- **Quiet Blue:** Marks selected or informational states without competing with content.

### Neutral

- **Cool Canvas:** Separates the application frame from working surfaces.
- **Paper Surface:** Holds cards, forms, tables, dialogs, and inputs.
- **Soft and Strong Ink:** Preserve clear hierarchy between content, supporting text, and metadata.
- **Cool Borders:** Define structure before shadows do.

### Named Rules

**The One Accent Rule.** Use blue for the primary next action, current location, links, and focus—not as general decoration.

**The Semantic State Rule.** Success, warning, and danger colors always communicate state and must also include text or an icon.

## Typography

**Display Font:** Roboto with system sans-serif fallbacks

**Body Font:** Roboto with system sans-serif fallbacks
**Label/Mono Font:** Roboto Mono with platform monospace fallbacks

**Character:** Roboto keeps the interface direct and familiar; tight weight changes and restrained negative tracking supply hierarchy without an ornamental display face.

### Hierarchy

- **Headline** (600, fluid 24–32px, 1.2): Page titles and first-use onboarding statements.
- **Title** (600, 20px, 1.2): Major sections and card groups.
- **Body** (400, 14px, 1.55): Default interface copy, usually limited to about 70 characters per line.
- **Label** (600, 13px, 1.5): Form labels, controls, and compact navigation.
- **Mono** (400, 12px, 1.5): URLs, slugs, credentials, code, and technical values.

### Named Rules

**The Operational Type Rule.** Use weight, spacing, and color for hierarchy; introduce no additional typefaces for dashboard novelty.

## Layout

The desktop application uses a fixed 248px sidebar and a centered content area up to 1240px wide. Pages use 40–52px horizontal breathing room, while cards follow the 4/8/12/16/24/32/48/64 spacing scale. Lists follow header → filters → table or empty state; details follow breadcrumb → header → sections; forms use one primary card and a clear terminal action.

At 820px and below, the sidebar becomes an off-canvas menu with a 60px mobile bar. Multicolumn details collapse to one column. At 560px and below, page padding reduces to 16px, actions wrap or fill the available width, and responsive tables become labeled record cards. Controls keep a 40–42px minimum height.

## Elevation & Depth

Depth is structural and quiet. Borders and tonal surface changes do most of the work; ambient shadows distinguish cards, floating menus, dialogs, and the mobile sidebar only when separation is useful.

### Shadow Vocabulary

- **Hairline Lift:** `0 1px 2px rgb(16 24 40 / 0.05)` for resting cards and secondary controls.
- **Low Lift:** `0 3px 10px rgb(16 24 40 / 0.07)` for small floating elements.
- **Dialog Lift:** `0 24px 64px rgb(16 24 40 / 0.16)` for overlays and mobile navigation.

**The Border-First Rule.** Prefer a cool border or tonal shift at rest; reserve larger shadows for true overlays.

## Shapes

Shapes are gently curved rather than pill-heavy. Compact controls use 7px corners, standard controls 10px, cards 14px, and prominent auth or result surfaces 18px. Status badges may be fully rounded because they are labels, not containers. One-pixel borders define most interactive boundaries.

## Components

### Buttons

- **Shape:** Gently curved standard controls with a 10px radius and 40px minimum height.
- **Primary:** Action Blue with white text, medium weight, and compact 9px × 15px padding.
- **Hover / Focus:** Deepens to Deep Action Blue; keyboard focus receives a visible blue ring. Pressed buttons move down by one pixel.
- **Secondary / Ghost:** White bordered buttons carry secondary actions; borderless ghost buttons remain visually quiet until hover.

### Chips

- **Style:** Small, fully rounded labels with soft semantic backgrounds and strong readable text.
- **State:** Filter chips use a bordered group; the selected chip alone receives the quiet blue surface.

### Cards / Containers

- **Corner Style:** 14px for standard cards and empty states.
- **Background:** White on the cool canvas; muted gray only for nested or secondary panels.
- **Shadow Strategy:** Hairline lift at rest, with borders doing the primary grouping.
- **Internal Padding:** 32px on desktop and 20px on narrow mobile screens.

### Inputs / Fields

- **Style:** White surface, strong cool border, 10px corners, 42px minimum height.
- **Focus:** Action Blue border plus a translucent three-pixel focus ring.
- **Error / Disabled:** Danger border and explicit copy for errors; disabled controls lower opacity and do not accept pointer input.

### Navigation

The desktop sidebar uses dark navy chrome, 42px rows, lightweight line icons, and a muted default label. The current destination uses a navy-blue surface and brighter text. Mobile navigation uses the same system in an off-canvas drawer with a visible backdrop, focus-safe controls, and a persistent 60px top bar.

### Async Feedback

Autosave, import, annotation, and domain operations use live regions, semantic state tokens, honest loading copy, and an explicit retry or next action. Dialogs trap attention visually while retaining clear Close, Cancel, and Apply actions.

## Do's and Don'ts

### Do:

- **Do** make one primary action obvious and keep secondary actions available without equal visual weight.
- **Do** use the shared spacing, radius, status, empty-state, and responsive-table patterns.
- **Do** preserve user input and explain how to recover from validation, network, authentication, and server failures.
- **Do** keep client surfaces limited to approved editing and publishing tasks.
- **Do** test agency and tenant paths at 375px, 768px, and 1280px.

### Don't:

- **Don't** add inline styles or one-off hardcoded presentation colors to templates.
- **Don't** use accent color, gradients, glows, or shadows as decoration in dashboard surfaces.
- **Don't** expose raw HTML, page creation, or page deletion to tenant users.
- **Don't** hide destructive consequences behind vague labels such as “OK” or “Submit.”
- **Don't** add a new component shape, spacing value, or typography family when the incumbent system already covers the need.
