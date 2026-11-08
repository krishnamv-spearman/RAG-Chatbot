---
name: Fund Facts
colors:
  surface: '#101419'
  surface-dim: '#101419'
  surface-bright: '#36393f'
  surface-container-lowest: '#0a0e13'
  surface-container-low: '#181c21'
  surface-container: '#1c2025'
  surface-container-high: '#262a30'
  surface-container-highest: '#31353b'
  on-surface: '#e0e2ea'
  on-surface-variant: '#bacac5'
  inverse-surface: '#e0e2ea'
  inverse-on-surface: '#2d3136'
  outline: '#859490'
  outline-variant: '#3c4a46'
  surface-tint: '#3cddc7'
  primary: '#57f1db'
  on-primary: '#003731'
  primary-container: '#2dd4bf'
  on-primary-container: '#00574d'
  inverse-primary: '#006b5f'
  secondary: '#a4c9ff'
  on-secondary: '#00315d'
  secondary-container: '#0267b8'
  on-secondary-container: '#d6e5ff'
  tertiary: '#e0d3ff'
  on-tertiary: '#381385'
  tertiary-container: '#c6b3ff'
  on-tertiary-container: '#5538a2'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#62fae3'
  primary-fixed-dim: '#3cddc7'
  on-primary-fixed: '#00201c'
  on-primary-fixed-variant: '#005047'
  secondary-fixed: '#d4e3ff'
  secondary-fixed-dim: '#a4c9ff'
  on-secondary-fixed: '#001c39'
  on-secondary-fixed-variant: '#004883'
  tertiary-fixed: '#e8ddff'
  tertiary-fixed-dim: '#cebdff'
  on-tertiary-fixed: '#21005e'
  on-tertiary-fixed-variant: '#4f319c'
  background: '#101419'
  on-background: '#e0e2ea'
  surface-variant: '#31353b'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 36px
    fontWeight: '600'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.015em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 26px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 25px
    letterSpacing: 0em
  body-md:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0.005em
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.01em
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.04em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 1.25rem
  gutter-mobile: 0.75rem
  margin: 2rem
  margin-mobile: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

The design system establishes a quiet, rigorous, and strictly objective financial research environment. The primary emotional target is absolute intellectual calm and uncompromised credibility. Users interact with mutual fund prospectuses, expense ratios, historical performance, and statutory disclosures; consequently, the aesthetic intentionally discards speculative trading excitement, market hyperbole, and decorative distractions.

Drawing from modern precision software and technical instrumentation, the interface blends systematic minimalism with subtle, structural elevation. Information hierarchy is paramount: every visual element exists to frame verified factual data, clarify statutory bounds, or enforce compliance barriers (such as explicit no-advice notices). Interactive elements emit soft, subdued feedback rather than aggressive motion. Data displays use strict tabular alignment, crisp architectural framing, and surgical contrast to project reliability and unbiased institutional clarity.

## Colors

The palette operates in an immersive, deeply attenuated dark mode engineered for extended analytical reading without visual fatigue. Contrast ratios rigorously fulfill WCAG 2.1 AA benchmarks across all text and interactive states.

### Core Canvas & Surfaces
- **Canvas Base (`#0B0F14`):** The foundational infinite canvas and app background.
- **Sidebar & Surface (`#0F141A`):** Low-contrast resting container for navigation and structured drawers.
- **Card & Panel Layer (`#151B23`):** Elevated structural containers holding conversation nodes, data grids, and comparison matrices.
- **Raised & Hover (`#1C2430`):** Transient state background for interactive cards, dropdowns, and button hovers.
- **Structural Border (`#263140`):** Crisp 1px perimeter token defining component boundaries without competing with typography.

### Typography & Content Tokens
- **Text Primary (`#E6EDF3`):** Maximum-contrast neutral for headings, primary chat responses, and quantitative data.
- **Text Secondary (`#9AA7B4`):** Secondary text for timestamps, column labels, metrics legends, and secondary navigation.
- **Text Muted (`#6B7785`):** Reserved for statutory disclaimers, footnote metadata, and inactive field placeholders.

### Interactive & Semantic Accents
- **Primary Teal (`#2DD4BF`):** Represents focused interaction, primary action links, selected filters, and factual affirmations.
- **Warning / Notice Amber (`#F5B544`):** Dedicated to compliance cautions, non-fiduciary disclaimers, and data completeness warnings.
- **Informational Blue (`#60A5FA`):** Used for fund classifications, benchmark indices, and document source attributions.
- **Clarification Violet (`#A78BFA`):** Highlights conversational follow-ups, methodology explanations, and contextual drill-downs.
- **Negative / Error Coral (`#F87171`):** Applied exclusively to unavailable data sets, failed prospectus retrievals, or invalid queries.

## Typography

Inter provides pure neutral utility across the entire system. Its mechanical rhythm and tall x-height support immediate legibility in analytical research displays.

### Numeric & Quantitative Treatment
All tabular data, performance percentages, ticker symbols, CUSIP identifiers, fee percentages, and asset balances must enforce `font-variant-numeric: tabular-nums lining-nums`. This prevents layout shifts across dynamic metric streaming and ensures vertical column alignment in mutual fund comparison tables.

### Hierarchy & Content Rules
- Long-form chat outputs rely on `body-md` (15px with ~1.55 line-height) or `body-lg` (16px) to maximize scanning comfort over detailed paragraphs.
- Footnotes, SEC filings, date stamps, and non-advisory notices employ `body-sm` or `label-md` styled with the Secondary or Muted text colors.
- Uppercase tracking (`0.04em`) is applied strictly to `label-sm` when designating badge categories, table column headers, and compliance tags.

## Layout & Spacing

The layout utilizes a structural hybrid system composed of a persistent desktop context panel and a centered conversational feed with an explicit maximum reading column width of 768px.

### Form Factor Adaptations
- **Desktop (1024px+):** A 12-column layout with 2rem (`margin`) canvas insets and 1.25rem (`gutter`) separation. The left structural navigation occupies 280px fixed width, the central research thread expands fluidly up to 768px, and secondary inspection drawers (fund metrics, prospectus viewer) dock to the right at 380px fixed width.
- **Tablet (768px - 1023px):** Collapses the secondary inspector into an overlay sheet; the conversational thread expands to fill the view within 1.5rem margins.
- **Mobile (<768px):** Single-column fluid stack with 1rem canvas margins and 0.75rem column gutters. Context drawers transform into bottom-anchored modal sheets. Touch targets maintain a minimum vertical clearance of 44px.

## Elevation & Depth

This design system avoids heavy drop shadows, relying instead on tonal surface layering combined with razor-thin structural borders (`#263140`) to create tangible depth.

- **Base Layer (Elevation 0):** The app canvas at `#0B0F14`. Seamless, non-elevated.
- **Layer 1 (Card & Thread Surfaces):** Background `#151B23` wrapped in a 1px solid `#263140` border. Zero standard shadow.
- **Layer 2 (Floating Popovers, Menus, Modals):** Background `#1C2430` bounded by a 1px solid `#263140` border and backed by an ambient shadow: `0 8px 24px -4px rgba(0, 0, 0, 0.45)`.
- **Interactive Focus & Glow:** Active inputs, pinned data points, and focused elements omit sharp drop shadows in favor of a subtle 2px soft glow using `#2DD4BF` with 20% opacity (`box-shadow: 0 0 0 1px #2DD4BF, 0 0 12px rgba(45, 212, 191, 0.15)`).

## Shapes

The geometric signature balances calculated precision with subtle accessibility.
- **Containers, Panels & Cards:** Configured with a dedicated 12px radius (`rounded-lg`), providing clear architectural framing for information tables and message bubbles.
- **Interactive Controls (Inputs & Default Buttons):** Configured with 8px radius (`rounded-md`), establishing an intentional visual distinction between actionable controls and broad containers.
- **Badges, Status Chips, & Filter Pills:** Configured with complete pill curvature (`rounded-full`), clearly separating categorical labels from clickable cards and input fields.

## Components

### Buttons
- **Primary Action:** Solid background `#2DD4BF`, text `#0B0F14`, font weight 600. Minimum height 44px. Hover transitions to `#26b8a5`. Focus outline applies a 2px offset glow.
- **Secondary Action:** Background `#151B23`, border 1px solid `#263140`, text `#E6EDF3`. Hover shifts background to `#1C2430` with border `#9AA7B4`.
- **Ghost Action:** Background transparent, text `#9AA7B4`. Hover applies text `#E6EDF3` and background `#1C2430`.

### Chips & Badges
- **Shape:** Pill-shaped (`rounded-full`) with horizontal padding of 10px and vertical padding of 4px.
- **Notice / Caution Badge:** Background `rgba(245, 181, 68, 0.12)`, border 1px solid `rgba(245, 181, 68, 0.3)`, text `#F5B544`.
- **Fact / Info Badge:** Background `rgba(96, 165, 250, 0.12)`, border 1px solid `rgba(96, 165, 250, 0.3)`, text `#60A5FA`.
- **Clarification Badge:** Background `rgba(167, 139, 250, 0.12)`, border 1px solid `rgba(167, 139, 250, 0.3)`, text `#A78BFA`.

### Input Fields & Query Console
- **Chat Input Bar:** Height 52px, background `#151B23`, border 1px solid `#263140`, corner radius 12px, text `#E6EDF3`, placeholder `#6B7785`. Focus triggers a border shift to `#2DD4BF` and a faint teal glow.
- **Checkboxes & Radios:** Size 18px x 18px, 4px radius for checkboxes, full circle for radios. Border 1.5px solid `#263140`. Checked state renders background `#2DD4BF` with checkmark icon in `#0B0F14`. Minimum clickable target 44px x 44px.

### Cards & Data Panels
- **Container Structure:** Background `#151B23`, border 1px solid `#263140`, radius 12px, internal padding 16px to 24px.
- **Tabular Data Cards:** Header row uses `#0F141A` with a 1px bottom border `#263140`. Table values leverage tabular figures (`font-variant-numeric: tabular-nums`). Alternate rows remain unstriped, separating strictly through 1px horizontal borders in `#1C2430`.

### Compliance Guardrail Banners
- **No-Advice Guardrail Container:** Rendered as a persistent docked element or message preface. Background `#0F141A`, border-left 3px solid `#F5B544`, border-top, right, bottom 1px solid `#263140`. Typography leverages `body-sm` in `#9AA7B4` with key statutory limits highlighted in `#E6EDF3`.