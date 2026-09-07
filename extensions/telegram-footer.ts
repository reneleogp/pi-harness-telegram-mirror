export type FooterTheme = {
  fg: (color: string, text: string) => string;
};

export function formatTelegramFooter(
  theme: FooterTheme | undefined,
  connected: boolean,
  mirrorOn: boolean,
): string {
  const state = connected
    ? (mirrorOn ? theme?.fg("success", "✓") ?? "✓" : "✗")
    : "unavailable";
  return `telegram: ${state}`;
}
