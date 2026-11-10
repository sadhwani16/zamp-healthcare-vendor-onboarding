/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./*.jsx"],
  theme: {
    extend: {
      colors: {
        paper: "var(--color-paper)", paper2: "var(--color-paper-2)", paper3: "var(--color-paper-3)",
        cotton: "var(--color-cotton)", desk: "var(--color-desk)",
        ink: "var(--color-ink)", ink2: "var(--color-ink-2)", muted: "var(--color-muted)",
        rule: "var(--color-rule)", rule2: "var(--color-rule-2)",
        accent: "var(--color-accent)", accent2: "var(--color-accent-2)",
        board: "var(--color-board)", boardtype: "var(--color-board-type)", boardmuted: "var(--color-boardmuted)",
        success: "var(--color-success)", successbg: "var(--color-successbg)", successink: "var(--color-successink)", successrule: "var(--color-successrule)",
        warningbg: "var(--color-warningbg)", warningink: "var(--color-warningink)", warningrule: "var(--color-warningrule)",
        errorbg: "var(--color-errorbg)", errorink: "var(--color-errorink)", errorrule: "var(--color-errorrule)",
      },
      fontFamily: { body: ["var(--font-body)"], figures: ["var(--font-outlier)"] },
    },
  },
  plugins: [],
};
