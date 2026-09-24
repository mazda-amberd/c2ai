export type AppStyle = {
  pageBackground: string;
  cardBackground: string;
  border: string;
  text: string;
  shadow: string;
};

export const appsColors: AppStyle[] = [
  {
    pageBackground: "var(--green-pageBg)",
    cardBackground: "var(--green-cardBg)",
    border: "hsla(var(--green-border))",
    text: "hsla(var(--green-text))",
    shadow: "var(--green-shadow)",
  }, // Green
  {
    pageBackground: "var(--purple-pageBg)",
    cardBackground: "var(--purple-cardBg)",
    border: "hsla(var(--purple-border))",
    text: "hsla(var(--purple-text))",
    shadow: "var(--purple-shadow)",
  }, // Purple
  {
    pageBackground: "var(--blue-pageBg)",
    cardBackground: "var(--blue-cardBg)",
    border: "hsla(var(--blue-border))",
    text: "hsla(var(--blue-text))",
    shadow: "var(--blue-shadow)",
  }, // Blue
  {
    pageBackground: "var(--cyan-pageBg)",
    cardBackground: "var(--cyan-cardBg)",
    border: "hsla(var(--cyan-border))",
    text: "hsla(var(--cyan-text))",
    shadow: "var(--cyan-shadow)",
  }, // Cyan
];