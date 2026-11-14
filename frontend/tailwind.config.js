/** @type {import('tailwindcss').Config} */
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: v("canvas"), surface: v("surface"), "surface-2": v("surface-2"), line: v("line"),
        ink: v("ink"), muted: v("muted"), accent: v("accent"), "accent-soft": v("accent-soft"),
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "Inter", "ui-sans-serif", "system-ui", '"Noto Sans Devanagari"', "sans-serif"],
        display: ['"Fraunces"', "Georgia", "serif"],
      },
      boxShadow: { card: "0 1px 0 rgb(0 0 0 / 0.03), 0 1px 3px rgb(30 25 15 / 0.06)" },
    },
  },
  plugins: [],
};
