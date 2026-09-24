export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        esm: "calc(var(--radius) - 8px)",
      },
      backgroundImage: {
        "custom-bg": "var(--gradient-background)",
        "gradient-button": "var(--gradient-button)",
        "cyan-bg": "var(--cyan-pageBg)",
        "blue-card": "var(--blue-pageBg)",
      },
      colors: {
        background: "hsla(var(--background))",
        foreground: "hsl(var(--foreground))",
        login: "hsl(var(--login))",
        header: "hsla(var(--header))",
        border: {
          DEFAULT: "hsla(var(--border))",
          secondary: "hsla(var(--border-secondary))",
        },
        input: "hsl(var(--input))",
        destructive: "hsl(var(--destructive))",
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        muted: {
          DEFAULT: "hsla(var(--muted))",
          foreground: "hsla(var(--muted-foreground))",
        },
        button: {
          primary: "hsla(var(--button-primary))",
          secondary: "hsla(var(--button-secondary))",
          destructive: "hsla(var(--button-destructive))",
        },
        text: {
          primary: "hsla(var(--text-primary))",
          secondary: "hsla(var(--text-secondary))",
        },
        healthy: {
          DEFAULT: "hsla(var(--healthy-bg))",
          text: "hsla(var(--healthy-text))",
        },
        warning: {
          DEFAULT: "hsla(var(--warning-bg))",
          text: "hsla(var(--warning-text))",
        },
        critical: {
          DEFAULT: "hsla(var(--critical-bg))",
          text: "hsla(var(--critical-text))",
        },
        chart: {
          healthy: "var(--chart-healthy)",
          warning: "var(--chart-warning)",
          "warning-bg": "var(--chart-warning-bg)",
          critical: "var(--chart-critical)",
          "critical-bg": "var(--chart-critical-bg)",
          panel: "var(--chart-panel)",
          "panel-border": "var(--chart-panel-border)",
          muted: "var(--chart-muted)",
          axis: "var(--chart-axis)",
          accent: "var(--chart-accent)",
          "accent-dim": "var(--chart-accent-dim)",
          "accent-bg": "var(--chart-accent-bg)",
          toolbar: "var(--chart-toolbar)",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
          border: "hsl(var(--popover-border))",
        },
        destructive: {
          DEFAULT: "hsla(var(--destructive))",
          foreground: "hsla(var(--destructive-foreground))",
          border: "hsla(var(--destructive-border))",
        },
        terminate: {
          dialog: "hsla(var(--terminate-dialog-surface))",
          border: "hsla(var(--terminate-dialog-border))",
          title: "hsla(var(--terminate-title))",
          body: "hsla(var(--terminate-body))",
          callout: "hsla(var(--terminate-dialog-callout))",
          "callout-border": "hsla(var(--terminate-callout-border))",
          "callout-foreground": "hsla(var(--terminate-callout-foreground))",
          accent: "hsla(var(--terminate-accent))",
          "accent-hover": "hsla(var(--terminate-accent-hover))",
          input: "hsla(var(--terminate-dialog-input))",
          "input-border": "hsla(var(--terminate-input-border))",
        },
      },
      boxShadow: {
        "shadow-primary": "var(--shadow-primary)",
        "shadow-secondary": "var(--shadow-secondary)",
      },
    },
  },
  plugins: [],
};
