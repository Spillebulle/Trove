/** @type {import('tailwindcss').Config} */

/*
 * Tailwind is a thin naming layer over `src/tokens.css`. Every value here is a
 * `var(--token)`; nothing in this file is a colour, a size or a shadow of its
 * own. See ../../Design-Principles/STYLE-GUIDE.md.
 *
 * Three scales are *replaced* rather than extended - `fontSize`, `borderRadius`
 * and `boxShadow`. That is deliberate: the guide fixes the type scale, the five
 * radii and the four things that float, and leaving Tailwind's defaults in
 * place leaves `text-lg`, `rounded-2xl` and `shadow-md` reachable, which is how
 * an interface ends up with eleven radii.
 */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    /*
     * Every size is the token, never the number. This is what carries the web
     * scale (STYLE-GUIDE 6.5): `<html class="web">` makes `--text-body` 14px
     * instead of 12, and a `12px` stated here would pin the app to the desktop
     * table while the chrome around it grew.
     */
    fontSize: {
      display: ['var(--text-display)', { lineHeight: '1', letterSpacing: '-2px', fontWeight: '900' }],
      page: ['var(--text-page)', { lineHeight: 'var(--lh)' }],
      heading: ['var(--text-heading)', { lineHeight: 'var(--lh)' }],
      body: ['var(--text-body)', { lineHeight: 'var(--lh)' }],
      control: ['var(--text-control)', { lineHeight: 'var(--lh)' }],
      small: ['var(--text-small)', { lineHeight: 'var(--lh)' }],
      tiny: ['var(--text-tiny)', { lineHeight: 'var(--lh)' }],
      // The eyebrow and the wordmark are the same size in both tables, so they
      // are stated flat rather than through a token that does not move.
      eyebrow: ['10px', { lineHeight: 'var(--lh)', letterSpacing: '2px' }],
    },
    borderRadius: {
      none: '0',
      tight: 'var(--r-tight)', // 3px  keycap, badge, app mark
      ctl: 'var(--r-ctl)', //    5px  button, chip, swatch
      tool: 'var(--r-tool)', //  6px  tool button, well, field
      card: 'var(--r-card)', //  8px  card, tile, menu, popover
      modal: 'var(--r-modal)', //10px dialog, floating panel
      art: 'var(--r-art)', //    6px  artwork, whatever its size
      full: '9999px', //              dots, the toggle pill
    },
    boxShadow: {
      none: 'none',
      menu: 'var(--shadow-menu)',
      float: 'var(--shadow-float)',
      modal: 'var(--shadow-modal)',
      knob: 'var(--shadow-knob)',
    },

    // The colours text is allowed to be, stated rather than inherited, so a
    // stray `text-gray-400` generates nothing and is caught in review.
    textColor: {
      inherit: 'inherit',
      current: 'currentColor',
      transparent: 'transparent',
      white: '#ffffff',

      strong: 'var(--text-strong)',
      fg: 'var(--text)',
      muted: 'var(--text-muted)',
      dim: 'var(--text-dim)',
      placeholder: 'var(--placeholder)',

      accent: { DEFAULT: 'var(--accent)', dim: 'var(--accent-dim)', ink: 'var(--accent-ink)' },

      caution: 'var(--caution)',
      good: 'var(--good)',
      critical: 'var(--critical)',

      // Text laid on artwork. The same in both themes: a picture supplies its
      // own contrast, so a pale scrim would erase the picture rather than the
      // text (7.21).
      art: { DEFAULT: 'var(--ink-art)', dim: 'var(--ink-art-dim)' },

      'line-dashed': 'var(--line-dashed)',
    },

    extend: {
      colors: {
        backdrop: 'var(--backdrop)',
        window: 'var(--window)',
        dock: 'var(--dock)',
        chrome: 'var(--chrome)',
        popover: 'var(--popover)',

        line: {
          DEFAULT: 'var(--line)',
          soft: 'var(--line-soft)',
          popover: 'var(--line-popover)',
          dashed: 'var(--line-dashed)',
        },

        control: {
          DEFAULT: 'var(--control)',
          hover: 'var(--control-hover)',
          active: 'var(--control-active)',
        },
        rail: 'var(--rail)',
        knob: 'var(--knob)',
        field: 'var(--field)',

        strong: 'var(--text-strong)',
        fg: 'var(--text)',
        muted: 'var(--text-muted)',
        dim: 'var(--text-dim)',
        placeholder: 'var(--placeholder)',

        accent: {
          DEFAULT: 'var(--accent)',
          dim: 'var(--accent-dim)',
          ink: 'var(--accent-ink)',
          tint: 'var(--accent-tint)',
          ring: 'var(--accent-ring)',
        },

        caution: {
          DEFAULT: 'var(--caution)',
          bg: 'var(--caution-bg)',
          line: 'var(--caution-line)',
        },
        good: { DEFAULT: 'var(--good)', bg: 'var(--good-bg)' },
        critical: {
          DEFAULT: 'var(--critical)',
          bg: 'var(--critical-bg)',
          line: 'var(--critical-line)',
        },

        'scrim-flat': 'var(--scrim-flat)',
        'ink-art': { DEFAULT: 'var(--ink-art)', dim: 'var(--ink-art-dim)' },

        series: {
          1: 'var(--series-1)',
          2: 'var(--series-2)',
          3: 'var(--series-3)',
          4: 'var(--series-4)',
          5: 'var(--series-5)',
          6: 'var(--series-6)',
        },
        grid: 'var(--grid)',
      },

      fontFamily: {
        sans: ['Archivo', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        // Every figure that is read as a value. Tabular, so a changing number
        // does not jitter its column.
        mono: [
          'ui-monospace',
          'Cascadia Mono',
          'JetBrains Mono',
          'SF Mono',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },

      /*
       * The chrome's fixed sizes, by name, so a strip cannot drift from the
       * guide by a pixel. The numbers in the comments are the desktop table;
       * Trove stamps `class="web"`, so what renders is the web column beside
       * it. The token is the same either way, which is the point: a component
       * asks for `h-button` and never asks which scale it is on.
       */
      spacing: {
        menubar: 'var(--h-menubar)', //    34 -> 52  top bar
        toolbar: 'var(--h-toolbar)', //    36 -> 44  filter strip
        status: 'var(--h-status)', //      26 -> 32  status/footer
        panelhead: 'var(--h-panelhead)', //32 -> 40  panel header
        row: 'var(--h-row)', //            26 -> 32  list row with a picture
        'row-plain': 'var(--h-row-plain)', //20 -> 26 text-only row
        nav: 'var(--h-nav)', //            30 -> 38  sidebar navigation row
        button: 'var(--h-button)', //      26 -> 32
        field: 'var(--h-field)', //        26 -> 32
        bottomnav: 'var(--h-bottomnav)', //52 -> 56
        sidebar: 'var(--w-sidebar)', //    240 -> 280
        panel: 'var(--w-panel)', //        264 -> 300
        strip: 'var(--pad-strip)', //      12 -> 16  padding inside every strip
        mark: 'var(--mark)', //            15 -> 22  the app mark in the top bar
        icon: 'var(--icon)', //            16 -> 18  icon in a row or a button
        'icon-lg': 'var(--icon-lg)', //    20 -> 22  icon in a panel header

        // The artwork ladder (7.21). Four widths and no fifth. Written as
        // widths, because artwork is sized by its width and takes its height
        // from `aspect-wide` (16/9) or `aspect-art` (2/3).
        'art-row': 'var(--art-row)', //    40 -> 48   inline in a row
        'art-tile': 'var(--art-tile)', //  100 -> 120 a picture beside text
        'art-card': 'var(--art-card)', //  150 -> 180 the offer card
        'art-hero': 'var(--art-hero)', //  260 -> 320 one per detail page
        avatar: 'var(--avatar)', //        28 -> 36
      },

      aspectRatio: {
        art: 'var(--art-ratio)',
        wide: '16 / 9',
      },

      borderWidth: { DEFAULT: '1px', 0: '0', 2: '2px' },

      transitionTimingFunction: { ease: 'var(--ease)' },
      transitionDuration: { hover: '80ms', open: '160ms' },

      keyframes: {
        // The one permitted indeterminate animation: a third-width bar
        // travelling the whole track, only where the total cannot be known.
        'progress-slide': {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(300%)' },
        },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        // A menu appears with a 4px rise; nothing bounces, nothing pulses.
        rise: {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'none' },
        },
      },
      animation: {
        'progress-slide': 'progress-slide 1.4s cubic-bezier(0.65, 0, 0.35, 1) infinite',
        shimmer: 'shimmer 1.6s infinite',
        rise: 'rise 160ms var(--ease) both',
      },
    },
  },
  plugins: [],
}
